"""
Tests for survivorpack-admin Flask service.

Covers:
  - POST /admin/packs/upload
  - POST /admin/packs/{id}/activate
  - POST /admin/packs/{id}/deactivate
  - DELETE /admin/packs/{id}
  - GET /admin/packs
  - GET /health
  - Startup behavior (first-boot, sentinel, orphan cleanup, config drift)
"""
import io
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from conftest import make_survivorpack


# ── Health check ───────────────────────────────────────────────────────────

class TestHealth:
    def test_health_returns_200(self, flask_app):
        client, packs_dir, state_dir, mod = flask_app
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"


# ── POST /admin/packs/upload ───────────────────────────────────────────────

class TestUpload:
    def test_no_file_returns_400(self, flask_app):
        client, *_ = flask_app
        resp = client.post("/admin/packs/upload", data={})
        assert resp.status_code == 400

    def test_wrong_extension_returns_400(self, flask_app, tmp_path):
        client, *_ = flask_app
        data = {"file": (io.BytesIO(b"data"), "pack.zip")}
        resp = client.post("/admin/packs/upload",
                           data=data, content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_not_a_zip_returns_400(self, flask_app):
        client, *_ = flask_app
        data = {"file": (io.BytesIO(b"not a zip"), "pack.survivorpack")}
        resp = client.post("/admin/packs/upload",
                           data=data, content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_missing_manifest_returns_400(self, flask_app, tmp_path):
        client, packs_dir, *_ = flask_app
        # Zip with a ZIM but no manifest
        zip_path = tmp_path / "no-manifest.survivorpack"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("something.zim", b"ZIM_DATA")
        with open(zip_path, "rb") as f:
            data = {"file": (f, "no-manifest.survivorpack")}
            resp = client.post("/admin/packs/upload",
                               data=data, content_type="multipart/form-data")
        assert resp.status_code == 400
        # Temp dir must be cleaned up
        assert not any(packs_dir.glob("tmp-*"))

    def test_invalid_manifest_json_returns_400(self, flask_app, tmp_path):
        client, packs_dir, *_ = flask_app
        zip_path = tmp_path / "bad-manifest.survivorpack"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("pack_manifest.json", "NOT JSON {{{")
            zf.writestr("pack.zim", b"ZIM")
        with open(zip_path, "rb") as f:
            data = {"file": (f, "bad-manifest.survivorpack")}
            resp = client.post("/admin/packs/upload",
                               data=data, content_type="multipart/form-data")
        assert resp.status_code == 400
        assert not any(packs_dir.glob("tmp-*"))

    def test_manifest_missing_id_returns_400(self, flask_app, tmp_path):
        client, packs_dir, *_ = flask_app
        zip_path = tmp_path / "no-id.survivorpack"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("pack_manifest.json",
                        json.dumps({"name": "No ID Pack", "version": "1.0"}))
            zf.writestr("pack.zim", b"ZIM")
        with open(zip_path, "rb") as f:
            data = {"file": (f, "no-id.survivorpack")}
            resp = client.post("/admin/packs/upload",
                               data=data, content_type="multipart/form-data")
        assert resp.status_code == 400
        assert not any(packs_dir.glob("tmp-*"))

    def test_zip_path_traversal_rejected(self, flask_app, tmp_path):
        client, packs_dir, *_ = flask_app
        zip_path = make_survivorpack(tmp_path, bad_path="../evil.sh")
        with open(zip_path, "rb") as f:
            data = {"file": (f, zip_path.name)}
            resp = client.post("/admin/packs/upload",
                               data=data, content_type="multipart/form-data")
        assert resp.status_code == 400
        assert "traversal" in resp.get_json()["error"].lower()
        assert not any(packs_dir.glob("tmp-*"))

    def test_manifest_id_path_traversal_rejected(self, flask_app, tmp_path):
        """Manifest id with invalid chars (e.g. spaces, dots) must be rejected."""
        client, packs_dir, *_ = flask_app
        # Build a zip with a clean zim filename but a malicious manifest id.
        # Using "evil pack" (space) — passes zip-entry checks but fails id regex.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            manifest = {"id": "evil pack", "name": "Evil", "version": "1.0"}
            zf.writestr("pack_manifest.json", json.dumps(manifest))
            zf.writestr("data.zim", b"FAKE")
        buf.seek(0)
        data = {"file": (buf, "evil.survivorpack")}
        resp = client.post("/admin/packs/upload",
                           data=data, content_type="multipart/form-data")
        assert resp.status_code == 400
        assert "id" in resp.get_json()["error"].lower()
        # Nothing escaped into packs_dir
        assert not list(packs_dir.glob("evil*"))

    def test_duplicate_pack_id_returns_409(self, flask_app, tmp_path):
        client, packs_dir, state_dir, mod = flask_app
        # Manually install a pack into state
        state = {"packs": {"test-pack": {"name": "Test", "version": "1.0",
                                          "zim_path": "/packs/test-pack/t.zim",
                                          "system_prompt": "", "installed_at": 0}},
                 "active_pack": None}
        mod._write_state(state)

        zip_path = make_survivorpack(tmp_path, pack_id="test-pack")
        with open(zip_path, "rb") as f:
            data = {"file": (f, zip_path.name)}
            resp = client.post("/admin/packs/upload",
                               data=data, content_type="multipart/form-data")
        assert resp.status_code == 409

    def test_insufficient_disk_returns_507(self, flask_app, tmp_path):
        client, *_ = flask_app
        zip_path = make_survivorpack(tmp_path, zim_content=b"Z" * 100)
        # Mock statvfs to report only 10 bytes free
        mock_stat = MagicMock()
        mock_stat.f_bavail = 1
        mock_stat.f_frsize = 1
        with patch("os.statvfs", return_value=mock_stat):
            with open(zip_path, "rb") as f:
                data = {"file": (f, zip_path.name)}
                resp = client.post("/admin/packs/upload",
                                   data=data, content_type="multipart/form-data")
        assert resp.status_code == 507

    def test_valid_pack_installs_and_returns_201(self, flask_app, tmp_path):
        client, packs_dir, state_dir, mod = flask_app
        zip_path = make_survivorpack(tmp_path, pack_id="wildfire",
                                     name="Wildfire Pack",
                                     system_prompt="You are a wildfire expert.")
        with open(zip_path, "rb") as f:
            data = {"file": (f, zip_path.name)}
            resp = client.post("/admin/packs/upload",
                               data=data, content_type="multipart/form-data")
        assert resp.status_code == 201
        assert resp.get_json()["pack_id"] == "wildfire"

        # Pack dir created
        assert (packs_dir / "wildfire").is_dir()
        # State updated
        state = mod._read_state()
        assert "wildfire" in state["packs"]
        assert state["packs"]["wildfire"]["system_prompt"] == "You are a wildfire expert."
        # library.xml updated
        xml = (packs_dir / "library.xml").read_text()
        assert "wildfire" in xml
        # No orphan tmp dirs
        assert not any(packs_dir.glob("tmp-*"))


# ── POST /admin/packs/{id}/activate ───────────────────────────────────────

class TestActivate:
    def _install_pack(self, mod, packs_dir, pack_id="wildfire"):
        """Helper: write a pack into state without going through the upload endpoint."""
        pack_dir = packs_dir / pack_id
        pack_dir.mkdir(exist_ok=True)
        (pack_dir / f"{pack_id}.zim").write_bytes(b"FAKE_ZIM")
        state = mod._read_state()
        state["packs"][pack_id] = {
            "name": f"{pack_id.title()} Pack",
            "version": "1.0",
            "description": "",
            "zim_path": str(pack_dir / f"{pack_id}.zim"),
            "system_prompt": f"You are a {pack_id} expert.",
            "installed_at": 0,
        }
        mod._write_state(state)

    def test_pack_not_found_returns_404(self, flask_app):
        client, *_ = flask_app
        resp = client.post("/admin/packs/nonexistent/activate")
        assert resp.status_code == 404

    def test_already_active_returns_200_idempotent(self, flask_app, tmp_path):
        client, packs_dir, state_dir, mod = flask_app
        self._install_pack(mod, packs_dir, "wildfire")
        state = mod._read_state()
        state["active_pack"] = "wildfire"
        mod._write_state(state)

        resp = client.post("/admin/packs/wildfire/activate")
        assert resp.status_code == 200
        assert "already active" in resp.get_json()["status"]

    def test_activate_writes_active_pack(self, flask_app, tmp_path):
        client, packs_dir, state_dir, mod = flask_app
        self._install_pack(mod, packs_dir, "wildfire")

        with patch("requests.request") as mock_req:
            mock_req.return_value.status_code = 200
            mock_req.return_value.text = ""
            resp = client.post("/admin/packs/wildfire/activate")

        assert resp.status_code == 200
        state = mod._read_state()
        assert state["active_pack"] == "wildfire"

    def test_activate_calls_ow_model_update(self, flask_app, tmp_path):
        client, packs_dir, state_dir, mod = flask_app
        self._install_pack(mod, packs_dir, "wildfire")

        with patch("requests.request") as mock_req:
            mock_req.return_value.status_code = 200
            mock_req.return_value.text = ""
            client.post("/admin/packs/wildfire/activate")

        # Should have called OW models/create endpoint with pack model id
        req_calls = [str(c) for c in mock_req.call_args_list]
        assert any("models" in c for c in req_calls)

    def test_activate_no_prior_active_pack(self, flask_app, tmp_path):
        """Fresh device: no active pack → activating first pack should work."""
        client, packs_dir, state_dir, mod = flask_app
        self._install_pack(mod, packs_dir, "medical")

        with patch("requests.request") as mock_req:
            mock_req.return_value.status_code = 200
            mock_req.return_value.text = ""
            resp = client.post("/admin/packs/medical/activate")

        assert resp.status_code == 200
        state = mod._read_state()
        assert state["active_pack"] == "medical"

    def test_activate_ow_unavailable_returns_500(self, flask_app, tmp_path):
        client, packs_dir, state_dir, mod = flask_app
        self._install_pack(mod, packs_dir, "wildfire")

        with patch("requests.request") as mock_req:
            mock_req.return_value.status_code = 500
            mock_req.return_value.text = "Internal Server Error"
            resp = client.post("/admin/packs/wildfire/activate")

        assert resp.status_code == 500
        # Pack must NOT be marked active on failure
        state = mod._read_state()
        assert state["active_pack"] != "wildfire"


# ── POST /admin/packs/{id}/deactivate ─────────────────────────────────────

class TestDeactivate:
    def test_pack_not_active_returns_400(self, flask_app, tmp_path):
        client, packs_dir, state_dir, mod = flask_app
        # Install but don't activate
        state = mod._read_state()
        state["packs"]["wildfire"] = {"name": "Wildfire", "version": "1.0",
                                       "zim_path": "/packs/wildfire/w.zim",
                                       "system_prompt": "", "installed_at": 0}
        mod._write_state(state)
        resp = client.post("/admin/packs/wildfire/deactivate")
        assert resp.status_code == 400

    def test_deactivate_active_pack_returns_200(self, flask_app, tmp_path):
        client, packs_dir, state_dir, mod = flask_app
        state = mod._read_state()
        state["packs"]["wildfire"] = {"name": "Wildfire", "version": "1.0",
                                       "zim_path": "/packs/wildfire/w.zim",
                                       "system_prompt": "prompt", "installed_at": 0}
        state["active_pack"] = "wildfire"
        mod._write_state(state)

        with patch("requests.request") as mock_req:
            mock_req.return_value.status_code = 200
            mock_req.return_value.text = ""
            resp = client.post("/admin/packs/wildfire/deactivate")

        assert resp.status_code == 200
        state = mod._read_state()
        assert state["active_pack"] is None

    def test_deactivate_ow_unavailable_returns_500(self, flask_app, tmp_path):
        client, packs_dir, state_dir, mod = flask_app
        state = mod._read_state()
        state["packs"]["wildfire"] = {"name": "Wildfire", "version": "1.0",
                                       "zim_path": "/packs/wildfire/w.zim",
                                       "system_prompt": "p", "installed_at": 0}
        state["active_pack"] = "wildfire"
        mod._write_state(state)

        with patch("requests.request") as mock_req:
            mock_req.return_value.status_code = 500
            mock_req.return_value.text = "error"
            resp = client.post("/admin/packs/wildfire/deactivate")

        assert resp.status_code == 500
        # Pack must STILL be marked active (deactivation didn't complete)
        state = mod._read_state()
        assert state["active_pack"] == "wildfire"


# ── DELETE /admin/packs/{id} ───────────────────────────────────────────────

class TestUninstall:
    def test_not_found_returns_404(self, flask_app):
        client, *_ = flask_app
        resp = client.delete("/admin/packs/nonexistent")
        assert resp.status_code == 404

    def test_active_pack_returns_409(self, flask_app, tmp_path):
        client, packs_dir, state_dir, mod = flask_app
        state = mod._read_state()
        state["packs"]["wildfire"] = {"name": "W", "version": "1.0",
                                       "zim_path": "/p/w.zim",
                                       "system_prompt": "", "installed_at": 0}
        state["active_pack"] = "wildfire"
        mod._write_state(state)
        resp = client.delete("/admin/packs/wildfire")
        assert resp.status_code == 409

    def test_uninstall_removes_dir_and_state(self, flask_app, tmp_path):
        client, packs_dir, state_dir, mod = flask_app
        pack_dir = packs_dir / "wildfire"
        pack_dir.mkdir()
        (pack_dir / "wildfire.zim").write_bytes(b"ZIM")

        state = mod._read_state()
        state["packs"]["wildfire"] = {
            "name": "Wildfire", "version": "1.0",
            "zim_path": str(pack_dir / "wildfire.zim"),
            "system_prompt": "", "installed_at": 0,
        }
        mod._write_state(state)

        resp = client.delete("/admin/packs/wildfire")
        assert resp.status_code == 200
        assert not pack_dir.exists()
        state = mod._read_state()
        assert "wildfire" not in state["packs"]
        # library.xml updated (wildfire entry removed)
        xml = (packs_dir / "library.xml").read_text()
        assert "wildfire" not in xml


# ── GET /admin/packs ───────────────────────────────────────────────────────

class TestListPacks:
    def test_empty_returns_200_empty_list(self, flask_app):
        client, *_ = flask_app
        resp = client.get("/admin/packs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["packs"] == []
        assert data["active_pack"] is None

    def test_two_packs_one_active(self, flask_app, tmp_path):
        client, packs_dir, state_dir, mod = flask_app
        state = {
            "packs": {
                "wildfire": {"name": "Wildfire", "version": "1.0",
                             "zim_path": "/p/w.zim", "system_prompt": "", "installed_at": 0},
                "medical":  {"name": "Medical",  "version": "1.0",
                             "zim_path": "/p/m.zim", "system_prompt": "", "installed_at": 0},
            },
            "active_pack": "wildfire",
        }
        mod._write_state(state)

        resp = client.get("/admin/packs")
        assert resp.status_code == 200
        data = resp.get_json()
        packs = {p["id"]: p for p in data["packs"]}
        assert packs["wildfire"]["active"] is True
        assert packs["medical"]["active"] is False

    def test_corrupted_state_returns_500(self, flask_app, tmp_path):
        client, packs_dir, state_dir, mod = flask_app
        # Write invalid JSON to state file
        mod.STATE_FILE.write_text("{invalid json{{")
        resp = client.get("/admin/packs")
        assert resp.status_code == 500


# ── Startup behavior ───────────────────────────────────────────────────────

class TestStartup:
    def test_orphan_cleanup_on_startup(self, tmp_dirs):
        """tmp-* dirs left by interrupted installs must be removed on startup."""
        packs_dir, state_dir = tmp_dirs
        orphan = packs_dir / "tmp-abcdef12"
        orphan.mkdir()
        (orphan / "upload.zip").write_bytes(b"partial")

        with patch("requests.post") as mock_post, \
             patch("requests.request") as mock_req:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"token": "test-jwt"}
            mock_post.return_value.text = ""
            mock_req.return_value.status_code = 200
            mock_req.return_value.json.return_value = {"data": []}
            mock_req.return_value.text = ""
            client, mod = _make_app_and_check(packs_dir, state_dir)

        assert not orphan.exists()

    def test_first_boot_runs_once_when_sentinel_absent(self, tmp_dirs):
        """Without sentinel, OW signin and model creation must be called."""
        packs_dir, state_dir = tmp_dirs
        sentinel = state_dir / ".first-boot-complete"
        assert not sentinel.exists()

        with patch("requests.post") as mock_post, \
             patch("requests.request") as mock_req:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"token": "test-jwt"}
            mock_post.return_value.text = ""
            mock_req.return_value.status_code = 200
            mock_req.return_value.json.return_value = {"data": []}
            mock_req.return_value.text = ""
            client, mod = _make_app_and_check(packs_dir, state_dir)
            # signin called during first-boot
            assert mock_post.called
        assert sentinel.exists()

    def test_first_boot_skipped_when_sentinel_present(self, tmp_dirs):
        """With sentinel present, first-boot key-gen endpoint must NOT be called."""
        packs_dir, state_dir = tmp_dirs
        (state_dir / ".first-boot-complete").touch()

        with patch("requests.post") as mock_post, \
             patch("requests.request") as mock_req:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"token": "test-jwt"}
            mock_post.return_value.text = ""
            mock_req.return_value.status_code = 200
            mock_req.return_value.json.return_value = {"data": [{"id": "survivoros-base"}]}
            mock_req.return_value.text = ""
            _make_app_and_check(packs_dir, state_dir)
            # Old API key creation endpoint must never be called
            post_calls = [str(c) for c in mock_post.call_args_list]
            assert not any("/api/v1/auths/api_key" in c for c in post_calls)

    def test_first_boot_sentinel_not_written_when_ow_down(self, tmp_dirs):
        """If OW is unreachable during first boot, sentinel must NOT be written."""
        packs_dir, state_dir = tmp_dirs
        sentinel = state_dir / ".first-boot-complete"

        with patch("requests.post") as mock_post:
            import requests
            mock_post.side_effect = requests.RequestException("connection refused")
            _make_app_and_check(packs_dir, state_dir)

        assert not sentinel.exists()

    def test_empty_library_xml_created_on_first_boot(self, tmp_dirs):
        """library.xml must exist after startup even with no packs."""
        packs_dir, state_dir = tmp_dirs
        lib = packs_dir / "library.xml"
        assert not lib.exists()

        with patch("requests.post") as mock_post, \
             patch("requests.request") as mock_req:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"token": "test-jwt"}
            mock_post.return_value.text = ""
            mock_req.return_value.status_code = 200
            mock_req.return_value.json.return_value = {"data": []}
            mock_req.return_value.text = ""
            client, mod = _make_app_and_check(packs_dir, state_dir)

        assert lib.exists()
        assert "<library" in lib.read_text()


def _make_app_and_check(packs_dir, state_dir):
    """Import app module fresh with env pointing at given dirs. Returns (client, module)."""
    import sys
    for key in list(sys.modules.keys()):
        if key == "app":
            del sys.modules[key]
    env = {
        "PACKS_DIR":        str(packs_dir),
        "STATE_DIR":        str(state_dir),
        "SENTINEL_FILE":    str(state_dir / ".first-boot-complete"),
        "PACKS_STATE_FILE": str(state_dir / "packs_state.json"),
        "LIBRARY_XML":      str(packs_dir / "library.xml"),
        "OW_BASE_URL":      "http://mock-ow:8080",
        "OLLAMA_MODEL":     "llama3.2:3b",
    }
    with patch.dict(os.environ, env):
        import app as app_module
        app_module.app.config["TESTING"] = True
        return app_module.app.test_client(), app_module
