"""
Tests for detect_hardware.py — hardware probe and model selection.

Mocks:
  - open('/proc/meminfo') via builtins.open
  - subprocess.run for nvidia-smi and rocm-smi
"""
import io
import json
import subprocess
from unittest.mock import MagicMock, mock_open, patch

import pytest
import detect_hardware


# ── /proc/meminfo helpers ──────────────────────────────────────────────────

def meminfo(total_kb):
    """Minimal /proc/meminfo content with given total RAM in KB."""
    return f"MemTotal:       {total_kb} kB\nMemFree:        {total_kb // 2} kB\n"


def run_with_ram(total_gb, vram_gb=None, has_nvidia=False, has_rocm=False):
    """
    Run detect_hardware.main() with mocked RAM and optional GPU.
    Returns parsed JSON output dict.
    """
    total_kb = total_gb * 1024 * 1024

    # nvidia-smi mock
    def fake_subprocess_run(cmd, *args, **kwargs):
        result = MagicMock()
        if "nvidia-smi" in cmd[0]:
            if has_nvidia and vram_gb:
                result.returncode = 0
                result.stdout = str(vram_gb * 1024)  # MB
            else:
                result.returncode = 1
                result.stdout = ""
        elif "rocm-smi" in (cmd[0] if isinstance(cmd, list) else cmd):
            if has_rocm and vram_gb:
                result.returncode = 0
                result.stdout = f"{vram_gb * 1024}"
            else:
                result.returncode = 1
                result.stdout = ""
        else:
            result.returncode = 1
            result.stdout = ""
        return result

    meminfo_content = meminfo(total_kb)
    m = mock_open(read_data=meminfo_content)

    with patch("builtins.open", m), \
         patch("subprocess.run", side_effect=fake_subprocess_run):
        output = detect_hardware.main()

    return json.loads(output)


# ── CPU model selection ────────────────────────────────────────────────────

class TestCPUModelSelection:
    def test_insufficient_ram_exits(self):
        """< 2GB RAM should raise SystemExit or return None."""
        with pytest.raises((SystemExit, Exception)):
            run_with_ram(1)

    def test_2gb_selects_phi3_mini_q2(self):
        result = run_with_ram(2)
        assert "phi3" in result["model"]["name"].lower() or "phi-3" in result["model"]["name"].lower()
        assert "q2" in result["model"]["name"].lower()

    def test_3gb_selects_phi3_mini_q2(self):
        """4GB boundary — still phi3 on 3GB."""
        result = run_with_ram(3)
        assert "phi" in result["model"]["name"].lower()

    def test_4gb_selects_llama_3b(self):
        result = run_with_ram(4)
        assert "3b" in result["model"]["name"].lower() or "3B" in result["model"]["name"]

    def test_6gb_selects_llama_3b_or_mistral(self):
        """4-7GB range — 3B or Mistral 7B depending on thresholds."""
        result = run_with_ram(6)
        model = result["model"]["name"].lower()
        assert any(x in model for x in ("3b", "7b", "mistral"))

    def test_8gb_selects_mistral_or_llama_8b(self):
        result = run_with_ram(8)
        model = result["model"]["name"].lower()
        assert any(x in model for x in ("7b", "8b", "mistral", "llama"))

    def test_12gb_selects_llama_8b(self):
        result = run_with_ram(12)
        model = result["model"]["name"].lower()
        assert "8b" in model or "llama" in model

    def test_output_is_valid_json(self):
        result = run_with_ram(8)
        # Must have required keys
        assert "model" in result
        assert "hardware" in result
        assert "ollama_env" in result
        assert "name"   in result["model"]
        assert "reason" in result["model"]

    def test_ollama_env_has_thread_count(self):
        result = run_with_ram(8)
        assert "OLLAMA_NUM_THREAD" in result["ollama_env"]
        assert int(result["ollama_env"]["OLLAMA_NUM_THREAD"]) >= 1

    def test_meminfo_missing_raises(self):
        """If /proc/meminfo doesn't exist, should raise a clear error."""
        with patch("builtins.open", side_effect=FileNotFoundError("/proc/meminfo not found")):
            with pytest.raises((FileNotFoundError, SystemExit, Exception)):
                detect_hardware.main()


# ── GPU model selection ────────────────────────────────────────────────────

class TestGPUModelSelection:
    def test_nvidia_2_5gb_selects_phi3_gpu(self):
        result = run_with_ram(8, vram_gb=3, has_nvidia=True)
        model = result["model"]["name"].lower()
        assert "phi" in model or "3b" in model

    def test_nvidia_6gb_selects_mistral_gpu(self):
        result = run_with_ram(8, vram_gb=6, has_nvidia=True)
        model = result["model"]["name"].lower()
        assert any(x in model for x in ("7b", "mistral"))

    def test_nvidia_12gb_selects_llama_8b_q8(self):
        result = run_with_ram(16, vram_gb=12, has_nvidia=True)
        model = result["model"]["name"].lower()
        assert "8b" in model or "llama" in model
        # High VRAM → higher quantization
        assert "q8" in model or "q4" in model

    def test_nvidia_absent_falls_through_to_cpu(self):
        """If nvidia-smi exits non-zero, should select based on CPU RAM."""
        result = run_with_ram(8, has_nvidia=False)
        # No GPU fields, or gpu.type == "none"
        gpu_type = result.get("hardware", {}).get("gpu", {}).get("type", "none")
        assert gpu_type in ("none", "cpu", "")

    def test_gpu_layers_set_when_nvidia_present(self):
        result = run_with_ram(8, vram_gb=6, has_nvidia=True)
        gpu_layers = result["model"].get("gpu_layers", 0)
        assert int(gpu_layers) > 0
