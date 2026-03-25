"""
Shared pytest fixtures for SurvivorOS tests.

Directory layout:
  survivalist-llm/
    survivorpack-admin/app.py    ← Flask app (hyphen → can't import as package)
    detect_hardware.py
    tests/conftest.py            ← this file

sys.path manipulation is used to import from the hyphenated source dirs.
"""
import importlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make the source directories importable
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "survivorpack-admin"))


# ── Helpers ────────────────────────────────────────────────────────────────

def make_survivorpack(tmp_path, pack_id="test-pack", name="Test Pack",
                      system_prompt="You are a test assistant.",
                      zim_content=b"FAKE_ZIM_DATA", bad_path=None):
    """Build a minimal .survivorpack zip and return its path."""
    import zipfile
    zip_path = tmp_path / f"{pack_id}.survivorpack"
    manifest = {"id": pack_id, "name": name, "version": "1.0",
                "description": "Test pack"}
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("pack_manifest.json", json.dumps(manifest))
        zf.writestr("system_prompt.txt",  system_prompt)
        zf.writestr(f"{pack_id}.zim",     zim_content)
        if bad_path:
            zf.writestr(bad_path, b"evil")
    return zip_path


# ── Flask app fixture ──────────────────────────────────────────────────────

@pytest.fixture()
def tmp_dirs():
    """Isolated temp directories for packs/ and state/."""
    base = tempfile.mkdtemp(prefix="survivoros-test-")
    packs_dir = Path(base) / "packs"
    state_dir = Path(base) / "state"
    packs_dir.mkdir()
    state_dir.mkdir()
    yield packs_dir, state_dir
    shutil.rmtree(base, ignore_errors=True)


def _make_app(packs_dir, state_dir, ow_post_status=200, ow_get_body=None):
    """
    Import and configure the Flask app with mocked env + OW API.
    Returns (test_client, app_module).
    """
    if ow_get_body is None:
        ow_get_body = {"ui": {"default_models": "llama3.2:3b", "enable_signup": False}}

    env = {
        "PACKS_DIR":        str(packs_dir),
        "STATE_DIR":        str(state_dir),
        "SENTINEL_FILE":    str(state_dir / ".first-boot-complete"),
        "PACKS_STATE_FILE": str(state_dir / "packs_state.json"),
        "LIBRARY_XML":      str(packs_dir / "library.xml"),
        "OW_BASE_URL":      "http://mock-ow:8080",
        "OLLAMA_MODEL":     "llama3.2:3b",
    }

    mock_post_response = MagicMock()
    mock_post_response.status_code = ow_post_status
    mock_post_response.json.return_value = {"api_key": "sk-test-key"}
    mock_post_response.text = ""

    mock_get_response = MagicMock()
    mock_get_response.status_code = 200
    mock_get_response.json.return_value = ow_get_body

    with patch.dict(os.environ, env):
        # Remove cached module so startup() re-runs with fresh env
        for key in list(sys.modules.keys()):
            if key in ("app",):
                del sys.modules[key]

        with patch("requests.post", return_value=mock_post_response), \
             patch("requests.get",  return_value=mock_get_response):
            import app as app_module
            app_module.app.config["TESTING"] = True
            client = app_module.app.test_client()
            return client, app_module


@pytest.fixture()
def flask_app(tmp_dirs):
    """Flask test client (default: OW API returns 200)."""
    packs_dir, state_dir = tmp_dirs
    client, module = _make_app(packs_dir, state_dir)
    yield client, packs_dir, state_dir, module


@pytest.fixture()
def flask_app_ow_down(tmp_dirs):
    """Flask test client where OW API returns 500."""
    packs_dir, state_dir = tmp_dirs
    client, module = _make_app(packs_dir, state_dir, ow_post_status=500)
    yield client, packs_dir, state_dir, module
