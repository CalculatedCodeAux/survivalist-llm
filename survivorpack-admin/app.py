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
import re
import secrets
import shutil
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
from flask import Flask, jsonify, redirect, render_template_string, request

# ── Configuration (from environment) ──────────────────────────────────────
PACKS_DIR     = Path(os.environ.get("PACKS_DIR",        "/packs"))
STATE_DIR     = Path(os.environ.get("STATE_DIR",        "/state"))
SENTINEL_FILE = Path(os.environ.get("SENTINEL_FILE",    "/state/.first-boot-complete"))
STATE_FILE    = Path(os.environ.get("PACKS_STATE_FILE", "/state/packs_state.json"))
LIBRARY_XML   = Path(os.environ.get("LIBRARY_XML",      "/packs/library.xml"))
OW_BASE_URL   = os.environ.get("OW_BASE_URL",           "http://open-webui:8080")
OLLAMA_MODEL  = os.environ.get("OLLAMA_MODEL",          "llama3.2:3b-instruct-q4_K_M")

# JWT cache — refreshed via _ow_signin(); avoids per-request credential reads
_ow_jwt_cache: dict = {"token": None}

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
      1. Sign in as OW default admin to obtain JWT
      2. Create the base (no-pack) model entry
    OW branding config (WEBUI_NAME, DEFAULT_MODELS, ENABLE_SIGNUP) is handled
    entirely via docker-compose.yml env vars — no API call required.
    Returns True on success, False if OW is unreachable.
    """
    try:
        _ow_signin()
        log.info("OW admin sign-in successful")
    except Exception as e:
        log.error("OW unreachable during first-boot: %s", e)
        return False

    _create_or_update_ow_model(
        model_id="survivoros-base",
        name="SurvivorOS",
        system_prompt=(
            "You are SurvivorOS, an offline survival and emergency reference assistant. "
            "Be concise and practical. Prioritize life safety. "
            "For medical, fire, or structural emergencies always say: call 911 first. "
            "State clearly when you are uncertain. "
            "Use both metric and imperial units when relevant."
        ),
    )
    return True


def _ow_signin():
    """
    Sign in as the default OW admin account and cache the JWT.
    OW creates admin@localhost / admin on first start when WEBUI_AUTH=False.
    API key creation is disabled in this environment — JWT is the only auth path.
    """
    resp = requests.post(
        f"{OW_BASE_URL}/api/v1/auths/signin",
        json={"email": "admin@localhost", "password": "admin"},
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"OW signin failed {resp.status_code}: {resp.text[:100]}")
    token = resp.json().get("token")
    if not token:
        raise RuntimeError("OW signin response missing token")
    _ow_jwt_cache["token"] = token
    log.debug("OW JWT refreshed")
    return token


def _ow_headers():
    """Return auth headers, signing in first if no cached token."""
    if not _ow_jwt_cache["token"]:
        _ow_signin()
    return {
        "Authorization": f"Bearer {_ow_jwt_cache['token']}",
        "Content-Type": "application/json",
    }


def _ow_request(method, path, **kwargs):
    """
    Authenticated OW request with automatic token refresh on expiry.
    OW JWTs expire in ~28 days; re-signin transparently on 401 unless
    the 401 means "already registered" (model-create duplicate, not auth).
    """
    resp = requests.request(
        method, f"{OW_BASE_URL}{path}", headers=_ow_headers(), timeout=10, **kwargs
    )
    if resp.status_code == 401 and "already registered" not in resp.text:
        log.debug("OW 401 on %s %s — refreshing JWT", method, path)
        _ow_jwt_cache["token"] = None
        resp = requests.request(
            method, f"{OW_BASE_URL}{path}", headers=_ow_headers(), timeout=10, **kwargs
        )
    return resp


def _apply_ow_config():
    """
    No-op: OW branding and model config are set entirely via env vars in
    docker-compose.yml (WEBUI_NAME, DEFAULT_MODELS, ENABLE_SIGNUP, WEBUI_AUTH).
    POST /api/v1/configs/ returns 405 in OW 0.8.10 — not a supported endpoint.
    """
    log.info("OW config managed via env vars — no API config call needed")
    return True


def _check_config_drift():
    """
    On every startup (after first boot), verify the survivoros-base model entry
    exists in OW. If missing (e.g. OW DB wiped, container recreated), recreate it.
    Also recreates the active pack's model entry if it disappeared.
    Logs warnings but does not fail startup.
    """
    try:
        resp = _ow_request("GET", "/api/v1/models")
        if resp.status_code != 200:
            log.warning("Config drift check: OW models list returned %d — skipping", resp.status_code)
            return

        model_ids = {m["id"] for m in resp.json().get("data", [])}

        if "survivoros-base" not in model_ids:
            log.warning("OW model 'survivoros-base' missing — recreating (DB drift?)")
            _create_or_update_ow_model(
                "survivoros-base",
                "SurvivorOS",
                "You are SurvivorOS, an offline survival and emergency reference assistant. "
                "Be concise and practical. Prioritize life safety. "
                "For medical, fire, or structural emergencies always say: call 911 first. "
                "State clearly when you are uncertain. "
                "Use both metric and imperial units when relevant.",
            )
        else:
            log.info("OW config drift check: survivoros-base present ✓")

        # Also check whether the active pack's model entry is still there
        try:
            state = _read_state()
        except (json.JSONDecodeError, OSError):
            return
        active = state.get("active_pack")
        if active:
            active_model_id = f"survivoros-{active}"
            if active_model_id not in model_ids:
                pack = state.get("packs", {}).get(active, {})
                log.warning("OW model for active pack '%s' missing — recreating", active)
                _create_or_update_ow_model(
                    active_model_id,
                    pack.get("name", active),
                    pack.get("system_prompt", ""),
                )

    except Exception as e:
        log.warning("Config drift check failed: %s", e)


def _create_or_update_ow_model(model_id, name, system_prompt):
    """
    Create or update a custom OW model entry with a system prompt.
    Strategy: try create first; if OW returns 401 "already registered",
    fall through to update. Update requires ?id= query param AND id in body.
    """
    payload = {
        "id": model_id,
        "base_model_id": OLLAMA_MODEL,
        "name": name,
        "meta": {"description": f"SurvivorOS domain pack: {name}", "capabilities": {}},
        "params": {"system": system_prompt},
        "is_active": True,
    }
    try:
        resp = _ow_request("POST", "/api/v1/models/create", json=payload)
        if resp.status_code in (200, 201):
            log.info("OW model '%s' created", model_id)
            return True
        # OW returns 401 with "already registered" for duplicate model IDs (not a real auth error)
        if resp.status_code == 401 and "already registered" in resp.text:
            log.debug("OW model '%s' exists — updating", model_id)
        else:
            log.error(
                "OW model create failed for '%s': %d %s",
                model_id, resp.status_code, resp.text[:200],
            )
            return False

        # Update requires ?id= query param AND id field in body
        resp = _ow_request(
            "POST", f"/api/v1/models/model/update?id={model_id}", json=payload
        )
        if resp.status_code in (200, 201):
            log.info("OW model '%s' updated (system_prompt len=%d)", model_id, len(system_prompt))
            return True
        log.error(
            "OW model update failed for '%s': %d %s",
            model_id, resp.status_code, resp.text[:200],
        )
        return False
    except requests.RequestException as e:
        log.error("OW unreachable during model create/update: %s", e)
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
    # Unique tmp name per thread to avoid concurrent-write collisions
    tmp = LIBRARY_XML.with_name(
        f".library-{os.getpid()}-{threading.get_ident()}.xml.tmp"
    )
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


# ── Admin UI HTML template ───────────────────────────────────────────────────
_ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SurvivorOS — Pack Manager</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: "Plus Jakarta Sans", system-ui, sans-serif;
      background: #F7F5F0;
      color: #1C1A17;
      min-height: 100vh;
    }
    /* ── Top nav (matches nginx-injected nav on chat pages) ── */
    nav#top {
      position: fixed;
      top: 0; left: 0; right: 0;
      z-index: 99998;
      height: 44px;
      background: #1C1A17;
      display: flex;
      align-items: center;
      padding: 0 16px;
      gap: 4px;
      border-bottom: 2px solid #D4570A;
    }
    nav#top .brand {
      color: #D4570A;
      font-weight: 700;
      font-size: 14px;
      letter-spacing: .05em;
      flex: none;
      margin-right: 16px;
    }
    nav#top a {
      color: #F7F5F0;
      font-size: 13px;
      font-weight: 500;
      text-decoration: none;
      padding: 6px 12px;
      border-radius: 4px;
    }
    nav#top a.active, nav#top a:hover { background: rgba(247,245,240,.12); }
    /* ── Content ── */
    .container {
      max-width: 680px;
      margin: 0 auto;
      padding: 72px 20px 72px;
    }
    h1 { font-size: 1.4rem; font-weight: 700; margin-bottom: 4px; }
    .subtitle { font-size: 0.85rem; color: #6B6456; margin-bottom: 28px; }
    .section {
      background: #fff;
      border: 1px solid #E0DDDA;
      border-radius: 10px;
      padding: 18px;
      margin-bottom: 20px;
    }
    .section h2 {
      font-size: 0.78rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .08em;
      color: #6B6456;
      margin-bottom: 14px;
    }
    .pack {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 0;
      border-bottom: 1px solid #F0EDE9;
    }
    .pack:last-child { border-bottom: none; }
    .pack-name { font-weight: 600; font-size: 0.95rem; color: #1C1A17; }
    .pack-meta { font-size: 0.78rem; color: #6B6456; margin-top: 2px; }
    .badge-active {
      background: #D4570A;
      color: #fff;
      border-radius: 3px;
      padding: 2px 7px;
      font-size: 0.68rem;
      font-weight: 700;
      letter-spacing: .06em;
      text-transform: uppercase;
      margin-left: 8px;
      vertical-align: middle;
    }
    .pack-actions { display: flex; gap: 8px; flex-shrink: 0; }
    button {
      cursor: pointer;
      border: none;
      border-radius: 6px;
      padding: 7px 14px;
      font-size: 0.82rem;
      font-family: inherit;
      font-weight: 600;
    }
    .btn-activate   { background: #D4570A; color: #fff; }
    .btn-activate:hover { background: #B8490A; }
    .btn-deactivate { background: #F0EDE9; color: #6B6456; border: 1px solid #E0DDDA; }
    .btn-deactivate:hover { background: #E0DDDA; }
    .btn-delete     { background: #FFF0F0; color: #C0392B; border: 1px solid #F5CACA; }
    .btn-delete:hover { background: #F5CACA; }
    .empty { color: #9B9289; font-style: italic; font-size: 0.9rem; }
    .upload-form { display: flex; flex-direction: column; gap: 12px; }
    .upload-form input[type=file] {
      background: #F7F5F0;
      border: 2px dashed #C8C4BE;
      border-radius: 8px;
      padding: 12px;
      color: #1C1A17;
      font-family: inherit;
      font-size: 0.9rem;
    }
    .upload-form input[type=file]:hover { border-color: #D4570A; }
    .btn-upload { background: #D4570A; color: #fff; width: fit-content; padding: 10px 24px; font-size: 0.9rem; }
    .btn-upload:hover { background: #B8490A; }
    .msg { padding: 12px 16px; border-radius: 8px; margin-bottom: 20px; font-size: 0.88rem; font-weight: 500; }
    .msg-ok  { background: #EBF5EB; color: #2D6A4F; border: 1px solid #A8D5B5; }
    .msg-err { background: #FFF0F0; color: #C0392B; border: 1px solid #F5CACA; }
    /* ── Disclaimer footer (matches nginx-injected bar on chat pages) ── */
    .disclaimer {
      position: fixed;
      bottom: 0; left: 0; right: 0;
      background: #111;
      color: #fff;
      padding: 8px 14px;
      font-size: 14px;
      line-height: 1.4;
      border-top: 3px solid #c00;
      z-index: 99999;
    }
    .disclaimer strong { color: #ff6666; }
    .disclaimer a { color: #7bc8ff; margin-left: 8px; text-decoration: none; }
  </style>
</head>
<body>
  <nav id="top">
    <span class="brand">SurvivorOS</span>
    <a href="/">Chat</a>
    <a href="/library">Library</a>
    <a href="/admin" class="active">Admin</a>
  </nav>

  <div class="container">
    <h1>Pack Manager</h1>
    <p class="subtitle">Install domain packs to give the AI specialised knowledge for off-grid scenarios.</p>

    {% if msg %}
    <div class="msg {{ 'msg-ok' if ok else 'msg-err' }}">{{ msg }}</div>
    {% endif %}

    <div class="section">
      <h2>Installed Packs</h2>
      {% if packs %}
      {% for pack in packs %}
      <div class="pack">
        <div>
          <span class="pack-name">{{ pack.name }}</span>
          {% if pack.active %}<span class="badge-active">Active</span>{% endif %}
          <div class="pack-meta">{{ pack.id }} &mdash; {{ pack.get('description', '') }}</div>
        </div>
        <div class="pack-actions">
          {% if pack.active %}
          <form method="POST" action="/admin/packs/{{ pack.id }}/deactivate">
            <button class="btn-deactivate" type="submit">Deactivate</button>
          </form>
          {% else %}
          <form method="POST" action="/admin/packs/{{ pack.id }}/activate">
            <button class="btn-activate" type="submit">Activate</button>
          </form>
          {% endif %}
          <form method="POST" action="/admin/packs/{{ pack.id }}/delete_ui">
            <button class="btn-delete" type="submit" onclick="return confirm('Delete {{ pack.name }}?')">Delete</button>
          </form>
        </div>
      </div>
      {% endfor %}
      {% else %}
      <p class="empty">No packs installed. Upload a .survivorpack file below.</p>
      {% endif %}
    </div>

    <div class="section">
      <h2>Upload New Pack</h2>
      <form class="upload-form" method="POST" action="/admin/packs/upload_ui" enctype="multipart/form-data">
        <input type="file" name="file" accept=".survivorpack,.zip" required>
        <button class="btn-upload" type="submit">Upload &amp; Install</button>
      </form>
    </div>
  </div>

  <div class="disclaimer">
    <strong>⚠ NOT MEDICAL OR SAFETY ADVICE.</strong>
    AI can be wrong or dangerous. Verify anything critical.
    Emergency? <strong>Call 911.</strong>
    <a href="/library">Offline Library →</a>
  </div>
</body>
</html>"""


# ── Routes ──────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/admin")
@app.route("/admin/")
def admin_ui():
    """HTML pack management UI."""
    state = _read_state()
    active = state.get("active_pack")
    packs = [
        {**pack, "id": pack_id, "active": pack_id == active}
        for pack_id, pack in state.get("packs", {}).items()
    ]
    return render_template_string(_ADMIN_HTML, packs=packs, msg=None, ok=True)


@app.route("/admin/packs/upload_ui", methods=["POST"])
def upload_pack_ui():
    """HTML form handler — calls upload_pack() within the same request context."""
    try:
        result = upload_pack()
        resp, status = result if isinstance(result, tuple) else (result, 200)
        data = resp.get_json() or {}
        if status in (200, 201):
            return _admin_ui_msg(f"Pack '{data.get('pack_id', '')}' installed.", ok=True)
        return _admin_ui_msg(data.get("error", "Upload failed."), ok=False)
    except Exception as exc:
        return _admin_ui_msg(str(exc), ok=False)


@app.route("/admin/packs/<pack_id>/delete_ui", methods=["POST"])
def delete_pack_ui(pack_id):
    """HTML form handler for pack deletion — calls delete_pack() in current context."""
    result = uninstall_pack(pack_id)
    resp, status = result if isinstance(result, tuple) else (result, 200)
    data = resp.get_json() or {}
    if status == 200:
        return _admin_ui_msg(f"Pack '{pack_id}' deleted.", ok=True)
    return _admin_ui_msg(data.get("error", "Delete failed."), ok=False)


def _admin_ui_msg(msg: str, *, ok: bool):
    """Re-render the admin UI with a status message."""
    state = _read_state()
    active = state.get("active_pack")
    packs = [
        {**pack, "id": pack_id, "active": pack_id == active}
        for pack_id, pack in state.get("packs", {}).items()
    ]
    return render_template_string(_ADMIN_HTML, packs=packs, msg=msg, ok=ok)


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

            # Validate pack_id to prevent path traversal via manifest
            if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$', pack_id):
                return jsonify({"error": "pack_manifest.json id must contain only letters, numbers, hyphens, and underscores"}), 400

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
