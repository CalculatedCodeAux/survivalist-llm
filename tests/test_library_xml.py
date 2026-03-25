"""
Tests for library.xml atomic write logic in survivorpack-admin/app.py.

Covers:
  - Add ZIM entry produces correct XML
  - Remove ZIM entry preserves others
  - Atomic write (tmp + rename — partial file never visible)
  - Concurrent writes produce valid XML (last write wins, no corruption)
"""
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest

# Add survivorpack-admin to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "survivorpack-admin"))


@pytest.fixture()
def lib_dir(tmp_path):
    """Temp directory with a writable library.xml path."""
    lib = tmp_path / "library.xml"
    return tmp_path, lib


def make_module_with_lib(lib_path):
    """Import app module with LIBRARY_XML pointed at lib_path."""
    for key in list(sys.modules.keys()):
        if key == "app":
            del sys.modules[key]
    env = {
        "PACKS_DIR":        str(lib_path.parent),
        "STATE_DIR":        str(lib_path.parent),
        "SENTINEL_FILE":    str(lib_path.parent / ".sentinel"),
        "PACKS_STATE_FILE": str(lib_path.parent / "state.json"),
        "LIBRARY_XML":      str(lib_path),
        "OW_BASE_URL":      "http://mock-ow:8080",
        "OLLAMA_MODEL":     "llama3.2:3b",
    }
    with patch.dict(os.environ, env):
        with patch("requests.post") as mock_post, \
             patch("requests.get")  as mock_get:
            from unittest.mock import MagicMock
            mock_post.return_value = MagicMock(status_code=200)
            mock_post.return_value.json.return_value = {"api_key": "k"}
            mock_get.return_value  = MagicMock(status_code=200)
            mock_get.return_value.json.return_value = {
                "ui": {"default_models": "m", "enable_signup": False}
            }
            import app as mod
            return mod


class TestAddZimEntry:
    def test_add_entry_produces_book_element(self, lib_dir):
        tmp_path, lib = lib_dir
        mod = make_module_with_lib(lib)
        entries = [{"id": "wildfire", "path": "/packs/wildfire/w.zim",
                    "title": "Wildfire Pack", "description": "Fire stuff"}]
        mod._write_library_xml(entries)

        tree = ET.parse(lib)
        books = tree.findall("book")
        assert len(books) == 1
        assert books[0].get("id") == "wildfire"
        assert books[0].get("path") == "/packs/wildfire/w.zim"
        assert books[0].get("title") == "Wildfire Pack"

    def test_add_two_entries(self, lib_dir):
        tmp_path, lib = lib_dir
        mod = make_module_with_lib(lib)
        entries = [
            {"id": "wildfire", "path": "/p/w.zim", "title": "Wildfire"},
            {"id": "medical",  "path": "/p/m.zim", "title": "Medical"},
        ]
        mod._write_library_xml(entries)
        tree = ET.parse(lib)
        ids = {b.get("id") for b in tree.findall("book")}
        assert ids == {"wildfire", "medical"}


class TestRemoveZimEntry:
    def test_remove_entry_leaves_others_intact(self, lib_dir):
        tmp_path, lib = lib_dir
        mod = make_module_with_lib(lib)
        # Start with two entries
        all_entries = [
            {"id": "wildfire", "path": "/p/w.zim", "title": "Wildfire"},
            {"id": "medical",  "path": "/p/m.zim", "title": "Medical"},
        ]
        mod._write_library_xml(all_entries)

        # Write only the survivor
        mod._write_library_xml([all_entries[1]])
        tree = ET.parse(lib)
        books = tree.findall("book")
        assert len(books) == 1
        assert books[0].get("id") == "medical"

    def test_remove_all_produces_empty_library(self, lib_dir):
        tmp_path, lib = lib_dir
        mod = make_module_with_lib(lib)
        mod._write_library_xml([{"id": "x", "path": "/p/x.zim", "title": "X"}])
        mod._write_library_xml([])
        tree = ET.parse(lib)
        assert tree.findall("book") == []


class TestAtomicWrite:
    def test_tmp_file_used_then_renamed(self, lib_dir, monkeypatch):
        """Write must go to a .tmp file, then os.rename to final path."""
        tmp_path, lib = lib_dir
        mod = make_module_with_lib(lib)

        rename_calls = []
        real_rename = os.rename

        def tracking_rename(src, dst):
            rename_calls.append((src, dst))
            real_rename(src, dst)

        monkeypatch.setattr(os, "rename", tracking_rename)
        mod._write_library_xml([{"id": "x", "path": "/p/x.zim", "title": "X"}])

        assert len(rename_calls) == 1
        src, dst = rename_calls[0]
        assert str(src).endswith(".tmp")
        assert str(dst) == str(lib)

    def test_partial_write_never_visible(self, lib_dir, monkeypatch):
        """
        If a crash happens mid-write, the original library.xml must remain intact.
        Simulate by making os.rename raise an exception.
        """
        tmp_path, lib = lib_dir
        mod = make_module_with_lib(lib)

        # Write initial valid state
        mod._write_library_xml([{"id": "orig", "path": "/p/orig.zim", "title": "Original"}])
        original_content = lib.read_text()

        # Now simulate a crash during the rename
        monkeypatch.setattr(os, "rename", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(OSError):
            mod._write_library_xml([{"id": "new", "path": "/p/new.zim", "title": "New"}])

        # Original file must be unchanged
        assert lib.read_text() == original_content
        # No lingering tmp file should be present after a real crash
        # (we can't fully test this without actual OS crash, but tmp must not become final)
        assert "new" not in lib.read_text()


class TestConcurrentWrites:
    def test_concurrent_writes_no_corruption(self, lib_dir):
        """
        Two threads writing simultaneously must not corrupt library.xml.
        Last writer wins; result must be valid XML.
        """
        tmp_path, lib = lib_dir
        mod = make_module_with_lib(lib)

        errors = []

        def write_pack_a():
            try:
                for _ in range(20):
                    mod._write_library_xml([
                        {"id": "a", "path": "/p/a.zim", "title": "Pack A"}
                    ])
            except Exception as e:
                errors.append(e)

        def write_pack_b():
            try:
                for _ in range(20):
                    mod._write_library_xml([
                        {"id": "b", "path": "/p/b.zim", "title": "Pack B"}
                    ])
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=write_pack_a)
        t2 = threading.Thread(target=write_pack_b)
        t1.start(); t2.start()
        t1.join();  t2.join()

        assert not errors, f"Concurrent write errors: {errors}"

        # Result must be parseable XML
        content = lib.read_text()
        tree = ET.fromstring(content)
        assert tree.tag == "library"

        # Result must have exactly one of the two valid final states
        ids = {b.get("id") for b in tree.findall("book")}
        assert ids in ({"a"}, {"b"}), f"Unexpected final state: {ids}"
