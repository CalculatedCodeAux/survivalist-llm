"""
survivorpack-admin — Domain pack lifecycle manager for SurvivorOS.

Responsibilities:
  - First-boot: configure Open WebUI (branding, API key, base model entry)
  - Config drift detection: re-applies OW config on every startup if mismatch
  - Pack upload: validate .survivorpack zip (manifest, ZIM, path traversal check)
  - Pack install: extract to /packs/{id}/, write library.xml atomically
  - Pack activation: POST to OW /api/v1/models/model/update with params.system
  - Pack deactivation: clear OW model system prompt to empty
  - Pack uninstall: remove ZIM + state, update library.xml
  - Health endpoint: /health (used by Docker healthcheck + kiwix depends_on)

Pack state machine:
  upload → extract → validate → install → (inactive)
                                             │
                                       activate ◄──► deactivate
                                             │
                                          (active)
                                             │
                                       deactivate → uninstall → (removed)
"""

import fcntl
import json
import logging
import os
import secrets
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
from flask import Flask, jsonify, request

# ── Configuration (from environment) ──────────────────────────────────────
PACKS_DIR     = Path(os.environ.get("PACKS_DIR",        "/packs"))
STATE_DIR     = Path(os.environ.get("STATE_DIR",        "/state"))
SENTINEL_FILE = Path(os.environ.get("SENTINEL_FILE",    "/state/.first-boot-complete"))
STATE_FILE    = Path(os.environ.get("PACKS_STATE_FILE", "/state/packs_state.json"))
LIBRARY_XML   = Path(os.environ.get("LIBRARY_XML",      "/packs/library.xml"))
OW_BASE_URL   = os.environ.get("OW_BASE_URL",           "http://open-webui:8080")
OLLAMA_MODEL  = os.environ.get("OLLAMA_MODEL",          "llama3.2:3b-instruct-q4_K_M")

OW_API_KEY_FILE = STATE_DIR / ".ow-api-key"

# Required OW config values — drift detection compares against these
EXPECTED_OW_CONFIG = {
    "WEBUI_NAME": "Ask SurvivorOS",
    "DEFAULT_MODELS": OLLAMA_MODEL,
    "ENABLE_SIGNUP": "False",
    "WEBUI_AUTH": "False",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

app = Flask(__name__)


# ── Startup ────────────────────────────────────────────────────────────────

def startup():
    """Run on every container start. Idempotent."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PACKS_DIR.mkdir(parents=True, exist_ok=True)

    _cleanup_orphans()

    if not LIBRARY_XML.exists():
        _write_library_xml([])
        log.info("Created empty library.xml")

    if not SENTINEL_FILE.exists():
        log.info("First boot detected — configuring Open WebUI")
        if _first_boot_configure():
            SENTINEL_FILE.touch()
            log.info("First-boot configuration complete, sentinel written")
        else:
            log.warning(
                "First-boot OW config failed — will retry on next start (sentinel NOT written)"
            )
    else:
        log.info("Sentinel present — checking for config drift")
        _check_config_drift()


def _cleanup_orphans():
    """Remove any tmp-* directories left by interrupted installs."""
    for tmp_dir in PACKS_DIR.glob("tmp-*"):
        if tmp_dir.is_dir():
            shutil.rmtree(tmp_dir, ignore_errors=True)
            log.warning("Cleaned up orphaned temp dir: %s", tmp_dir)


def _first_boot_configure():
    """
    Configure Open WebUI on first boot:
      1. Create an admin API key and persist it to OW_API_KEY_FILE
      2. Apply branding + model config
      3. Create the base (no-pack) model entry
    Returns True on success, False if OW is unreachable.
    """
    # OW starts with no auth (WEBUI_AUTH=False), so the admin key is
    # generated via the /api/v1/auths/api_key endpoint on the default admin.
    # Since auth is disabled we can hit the API directly without a token first.
    try:
        resp = requests.post(
            f"{OW_BASE_URL}/api/v1/auths/api_key",
            json={},
            timeout=10,
        )
        if resp.status_code == 200:
            api_key = resp.json().get("api_key") or resp.json().get("key")
            if api_key:
                OW_API_KEY_FILE.write_text(api_key)
                log.info("OW admin API key generated and saved")
        else:
            log.warning("Could not generate OW API key (status %d) — proceeding without", resp.status_code)
            api_key = None
    except requests.RequestException as e:
        log.error("OW unreachable during first-boot key generation: %s", e)
        return False

    # Apply config (tolerates missing API key — OW accepts unauthenticated
    # config writes when WEBUI_AUTH=False)
    if not _apply_ow_config(api_key):
        return False

    # Create the base model entry (no system prompt — "no pack active" state)
    _create_or_update_ow_model(
        model_id="survivoros-base",
        name="SurvivorOS — No Pack Active",
        system_prompt="",
        api_key=api_key,
    )

    return True


def _get_ow_api_key():
    """Load persisted OW API key, or None if not generated yet."""
    if OW_API_KEY_FILE.exists():
        return OW_API_KEY_FILE.read_text().strip() or None
    return None


def _ow_headers(api_key=None):
    key = api_key or _get_ow_api_key()
    if key:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    return {"Content-Type": "application/json"}


def _apply_ow_config(api_key=None):
    """Push branding/model config to OW. Returns True on success."""
    try:
        resp = requests.post(
            f"{OW_BASE_URL}/api/v1/configs/",
            headers=_ow_headers(api_key),
            json={
                "ui": {
                    "default_models": OLLAMA_MODEL,
                    "enable_signup": False,
                },
            },
            timeout=10,
        )
        if resp.status_code not in (200, 201):
            log.warning("OW config POST returned %d: %s", resp.status_code, resp.text[:200])
        else:
            log.info("OW config applied successfully")
        return True
    except requests.RequestException as e:
        log.error("OW unreachable during config apply: %s", e)
        return False


def _check_config_drift():
    """
    On every startup (after first boot), verify OW config matches expectations.
    Re-apply if drift is detected. Logs a warning but does not fail startup.
    """
    try:
        resp = requests.get(
            f"{OW_BASE_URL}/api/v1/configs/",
            headers=_ow_headers(),
            timeout=10,
        )
        if resp.status_code != 200:
            log.warning("Config drift check: OW returned %d — skipping", resp.status_code)
            return

        current = resp.json()
        ui = current.get("ui", {})
        drift_detected = (
            ui.get("default_models") != OLLAMA_MODEL
            or ui.get("enable_signup") is not False
        )

        if drift_detected:
            log.warning("OW config drift detected — re-applying configuration")
            _apply_ow_config()
        else:
            log.info("OW config drift check: OK")

    except requests.RequestException as e:
        log.warning("Config drift check failed (OW unreachable?): %s", e)


def _create_or_update_ow_model(model_id, name, system_prompt, api_key=None):
    """
    Create or update a custom OW model entry with a system prompt.
    Uses POST /api/v1/models/model/update (idempotent — creates if absent).
    """
    payload = {
        "id": model_id,
        "base_model_id": OLLAMA_MODEL,
        "name": name,
        "meta": {"description": f"SurvivorOS domain pack: {name}"},
        "params": {"system": system_prompt},
        "is_active": True,
    }
    try:
        # Try update first; fall back to create if model doesn't exist
        resp = requests.post(
            f"{OW_BASE_URL}/api/v1/models/model/update",
            headers=_ow_headers(api_key),
            json=payload,
            timeout=10,
        )
        if resp.status_code == 404:
            resp = requests.post(
                f"{OW_BASE_URL}/api/v1/models/create",
                headers=_ow_headers(api_key),
                json=payload,
                timeout=10,
            )
        if resp.status_code not in (200, 201):
            log.error(
                "Failed to create/update OW model %s: %d %s",
                model_id, resp.status_code, resp.text[:200],
            )
            return False
        log.info("OW model '%s' updated (system prompt len=%d)", model_id, len(system_prompt))
        return True
    except requests.RequestException as e:
        log.error("OW unreachable during model update: %s", e)
        return False


# ── Pack state ─────────────────────────────────────────────────────────────

def _read_state():
    """Read packs_state.json. Returns dict with 'packs' list and 'active_pack' key."""
    if not STATE_FILE.exists():
        return {"packs": {}, "active_pack": None}
    with open(STATE_FILE, "r") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            return json.load(f)
        except json.JSONDecodeError:
            log.error("packs_state.json is corrupted — returning empty state")
            raise


def _write_state(state):
    """Write packs_state.json atomically using tmp + rename (os.rename is atomic on POSIX)."""
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(state, f, indent=2)
    os.rename(tmp, STATE_FILE)


# ── library.xml ────────────────────────────────────────────────────────────

def _write_library_xml(pack_entries):
    """
    Write library.xml atomically.
    pack_entries: list of dicts with keys: id, path, title, description
    kiwix-serve with --monitorLibrary detects the mtime change and reloads within ~1s.
    """
    root = ET.Element("library", version="20110515")
    for entry in pack_entries:
        book = ET.SubElement(root, "book")
        book.set("id",    entry["id"])
        book.set("path",  entry["path"])
        book.set("title", entry.get("title", entry["id"]))
        if entry.get("description"):
            book.set("description", entry["description"])

    tree = ET.ElementTree(root)
    tmp = LIBRARY_XML.with_suffix(".tmp")
    tree.write(str(tmp), encoding="utf-8", xml_declaration=True)
    os.rename(tmp, LIBRARY_XML)
    log.info("library.xml updated (%d entries)", len(pack_entries))


def _rebuild_library_xml(state):
    """Rebuild library.xml from current installed packs in state."""
    entries = []
    for pack_id, pack in state["packs"].items():
        zim_path = pack.get("zim_path")
        if zim_path and Path(zim_path).exists():
            entries.append({
                "id":          pack_id,
                "path":        zim_path,
                "title":       pack.get("name", pack_id),
                "description": pack.get("description", ""),
            })
    _write_library_xml(entries)


# ── Routes ──────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/admin/packs", methods=["GET"])
def list_packs():
    try:
        state = _read_state()
    except json.JSONDecodeError:
        return jsonify({"error": "packs_state.json corrupted"}), 500
    active = state.get("active_pack")
    packs = []
    for pack_id, pack in state.get("packs", {}).items():
        packs.append({**pack, "id": pack_id, "active": pack_id == active})
    return jsonify({"packs": packs, "active_pack": active}), 200


@app.route("/admin/packs/upload", methods=["POST"])
def upload_pack():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename.endswith(".survivorpack"):
        return jsonify({"error": "File must have .survivorpack extension"}), 400

    # Write upload to a named temp file in packs dir (keeps it on same filesystem
    # for atomic rename later)
    tmp_dir = PACKS_DIR / f"tmp-{secrets.token_hex(8)}"
    tmp_dir.mkdir(parents=True)

    try:
        upload_path = tmp_dir / "upload.zip"
        file.save(str(upload_path))

        # Validate zip
        if not zipfile.is_zipfile(upload_path):
            return jsonify({"error": "File is not a valid zip archive"}), 400

        with zipfile.ZipFile(upload_path, "r") as zf:
            names = zf.namelist()

            # Path traversal check — must happen BEFORE any extraction
            for name in names:
                if ".." in name or name.startswith("/"):
                    return jsonify({"error": f"Zip path traversal rejected: {name}"}), 400

            # Manifest required
            if "pack_manifest.json" not in names:
                return jsonify({"error": "Missing pack_manifest.json"}), 400

            manifest_bytes = zf.read("pack_manifest.json")
            try:
                manifest = json.loads(manifest_bytes)
            except json.JSONDecodeError:
                return jsonify({"error": "pack_manifest.json is not valid JSON"}), 400

            # Required manifest fields
            for field in ("id", "name", "version"):
                if field not in manifest:
                    return jsonify({"error": f"pack_manifest.json missing required field: {field}"}), 400

            pack_id = manifest["id"]

            # Duplicate check
            try:
                state = _read_state()
            except json.JSONDecodeError:
                return jsonify({"error": "State file corrupted"}), 500

            if pack_id in state.get("packs", {}):
                return jsonify({"error": f"Pack '{pack_id}' is already installed"}), 409

            # ZIM file required
            zim_files = [n for n in names if n.endswith(".zim")]
            if not zim_files:
                return jsonify({"error": "Pack contains no .zim file"}), 400
            zim_name = zim_files[0]

            # System prompt (optional — base model packs may omit it)
            system_prompt = ""
            if "system_prompt.txt" in names:
                system_prompt = zf.read("system_prompt.txt").decode("utf-8").strip()

            # Disk space check — compare ZIM size against available space
            zim_info = zf.getinfo(zim_name)
            stat = os.statvfs(PACKS_DIR)
            available_bytes = stat.f_bavail * stat.f_frsize
            if zim_info.file_size > available_bytes:
                return jsonify({
                    "error": "Insufficient disk space",
                    "required_bytes": zim_info.file_size,
                    "available_bytes": available_bytes,
                }), 507

            # Extract into tmp dir first
            zf.extractall(str(tmp_dir))

        # Move to final location atomically
        pack_dir = PACKS_DIR / pack_id
        os.rename(tmp_dir, pack_dir)

        # Update state
        state["packs"][pack_id] = {
            "name":          manifest["name"],
            "version":       manifest["version"],
            "description":   manifest.get("description", ""),
            "zim_path":      str(pack_dir / zim_name),
            "system_prompt": system_prompt,
            "installed_at":  time.time(),
        }
        _write_state(state)
        _rebuild_library_xml(state)

        log.info("Pack installed: %s v%s", pack_id, manifest["version"])
        return jsonify({"status": "installed", "pack_id": pack_id}), 201

    except Exception as e:
        log.exception("Pack install failed: %s", e)
        return jsonify({"error": "Install failed — see server logs"}), 500
    finally:
        # Clean up tmp dir if it still exists (rename failed)
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


@app.route("/admin/packs/<pack_id>/activate", methods=["POST"])
def activate_pack(pack_id):
    try:
        state = _read_state()
    except json.JSONDecodeError:
        return jsonify({"error": "State file corrupted"}), 500

    if pack_id not in state.get("packs", {}):
        return jsonify({"error": f"Pack '{pack_id}' not found"}), 404

    if state.get("active_pack") == pack_id:
        return jsonify({"status": "already active", "pack_id": pack_id}), 200

    pack = state["packs"][pack_id]
    system_prompt = pack.get("system_prompt", "")

    # Push system prompt to OW — single atomic model update (no clear+set race)
    ok = _create_or_update_ow_model(
        model_id=f"survivoros-{pack_id}",
        name=pack["name"],
        system_prompt=system_prompt,
    )
    if not ok:
        return jsonify({"error": "Failed to update Open WebUI — OW unreachable"}), 500

    state["active_pack"] = pack_id
    _write_state(state)

    log.info("Pack activated: %s", pack_id)
    return jsonify({"status": "activated", "pack_id": pack_id}), 200


@app.route("/admin/packs/<pack_id>/deactivate", methods=["POST"])
def deactivate_pack(pack_id):
    try:
        state = _read_state()
    except json.JSONDecodeError:
        return jsonify({"error": "State file corrupted"}), 500

    if state.get("active_pack") != pack_id:
        return jsonify({"error": f"Pack '{pack_id}' is not the active pack"}), 400

    # Clear system prompt on the OW model (revert to base model behavior)
    ok = _create_or_update_ow_model(
        model_id=f"survivoros-{pack_id}",
        name=state["packs"][pack_id]["name"],
        system_prompt="",
    )
    if not ok:
        return jsonify({"error": "Failed to clear Open WebUI system prompt — OW unreachable"}), 500

    state["active_pack"] = None
    _write_state(state)

    log.info("Pack deactivated: %s", pack_id)
    return jsonify({"status": "deactivated", "pack_id": pack_id}), 200


@app.route("/admin/packs/<pack_id>", methods=["DELETE"])
def uninstall_pack(pack_id):
    try:
        state = _read_state()
    except json.JSONDecodeError:
        return jsonify({"error": "State file corrupted"}), 500

    if pack_id not in state.get("packs", {}):
        return jsonify({"error": f"Pack '{pack_id}' not found"}), 404

    if state.get("active_pack") == pack_id:
        return jsonify({"error": "Cannot uninstall the active pack — deactivate first"}), 409

    pack_dir = PACKS_DIR / pack_id
    if pack_dir.exists():
        shutil.rmtree(pack_dir)

    del state["packs"][pack_id]
    _write_state(state)
    _rebuild_library_xml(state)

    log.info("Pack uninstalled: %s", pack_id)
    return jsonify({"status": "uninstalled", "pack_id": pack_id}), 200


# ── Entrypoint ─────────────────────────────────────────────────────────────

startup()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
