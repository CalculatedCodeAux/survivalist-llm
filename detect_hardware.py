#!/usr/bin/env python3
"""
Survivalist LLM - Hardware Detection & Model Selection
Detects RAM, VRAM, and CPU cores, then selects the optimal quantized model.
Outputs a JSON config consumed by setup.sh.
"""

import subprocess
import sys
import json
import os
import re


# ---------------------------------------------------------------------------
# Model catalogue — ordered best-to-worst within each tier.
# All models available via 'ollama pull <name>'.
# context_tokens: safe default context window given RAM constraints.
# ---------------------------------------------------------------------------
MODEL_CATALOGUE = [
    # ── GPU-accelerated tiers ──────────────────────────────────────────────
    {
        "id": "gpu-large",
        "name": "llama3.1:8b-instruct-q8_0",
        "display": "Llama 3.1 8B (Q8_0, GPU)",
        "min_vram_gb": 10.0,
        "min_ram_gb": 8.0,
        "requires_gpu": True,
        "gpu_layers": 99,          # offload all layers
        "size_gb": 8.5,
        "context_tokens": 8192,
        "reason": "High-fidelity 8B model fully resident in VRAM.",
    },
    {
        "id": "gpu-mid",
        "name": "mistral:7b-instruct-q4_K_M",
        "display": "Mistral 7B (Q4_K_M, GPU)",
        "min_vram_gb": 5.0,
        "min_ram_gb": 6.0,
        "requires_gpu": True,
        "gpu_layers": 99,
        "size_gb": 4.1,
        "context_tokens": 8192,
        "reason": "Mistral 7B Q4 fully in VRAM — fast and capable.",
    },
    {
        "id": "gpu-small",
        "name": "phi3:mini-instruct-4k-q4_K_M",
        "display": "Phi-3 Mini (Q4_K_M, GPU)",
        "min_vram_gb": 2.5,
        "min_ram_gb": 4.0,
        "requires_gpu": True,
        "gpu_layers": 99,
        "size_gb": 2.4,
        "context_tokens": 4096,
        "reason": "Phi-3 Mini fully in VRAM — optimal for low-VRAM GPUs.",
    },
    # ── CPU-only tiers ─────────────────────────────────────────────────────
    {
        "id": "cpu-large",
        "name": "llama3.1:8b-instruct-q4_K_M",
        "display": "Llama 3.1 8B (Q4_K_M, CPU)",
        "min_vram_gb": 0,
        "min_ram_gb": 10.0,
        "requires_gpu": False,
        "gpu_layers": 0,
        "size_gb": 4.7,
        "context_tokens": 4096,
        "reason": "Best capability available for CPU-only ≥10 GB RAM.",
    },
    {
        "id": "cpu-mid",
        "name": "mistral:7b-instruct-q4_K_M",
        "display": "Mistral 7B (Q4_K_M, CPU)",
        "min_vram_gb": 0,
        "min_ram_gb": 7.0,
        "requires_gpu": False,
        "gpu_layers": 0,
        "size_gb": 4.1,
        "context_tokens": 4096,
        "reason": "Mistral 7B Q4 on CPU — strong reasoning on modest RAM.",
    },
    {
        "id": "cpu-small",
        "name": "llama3.2:3b-instruct-q4_K_M",
        "display": "Llama 3.2 3B (Q4_K_M, CPU)",
        "min_vram_gb": 0,
        "min_ram_gb": 4.0,
        "requires_gpu": False,
        "gpu_layers": 0,
        "size_gb": 2.0,
        "context_tokens": 4096,
        "reason": "Llama 3.2 3B Q4 — good balance for 4-7 GB RAM systems.",
    },
    {
        "id": "cpu-tiny",
        "name": "phi3:mini-instruct-4k-q2_K",
        "display": "Phi-3 Mini (Q2_K, CPU)",
        "min_vram_gb": 0,
        "min_ram_gb": 2.0,
        "requires_gpu": False,
        "gpu_layers": 0,
        "size_gb": 1.6,
        "context_tokens": 2048,
        "reason": "Smallest viable model — last resort for ≤4 GB RAM machines.",
    },
]


def run(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return -1, "", ""


# ---------------------------------------------------------------------------
# Hardware probes
# ---------------------------------------------------------------------------

def get_ram_gb() -> float:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    kb = int(line.split()[1])
                    return round(kb / 1_048_576, 2)
    except Exception:
        pass
    return 0.0


def get_cpu_cores() -> int:
    try:
        rc, out, _ = run(["nproc"])
        if rc == 0:
            return int(out)
    except Exception:
        pass
    return os.cpu_count() or 1


def get_nvidia_vram_gb() -> float:
    """Return total VRAM in GB for the first NVIDIA GPU, or 0."""
    rc, out, _ = run(
        ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"]
    )
    if rc != 0 or not out:
        return 0.0
    try:
        # output is in MiB; take first GPU
        first_line = out.splitlines()[0].strip()
        mib = int(first_line)
        return round(mib / 1024, 2)
    except ValueError:
        return 0.0


def get_amd_vram_gb() -> float:
    """Return total VRAM in GB for the first AMD GPU, or 0."""
    rc, out, _ = run(["rocm-smi", "--showmeminfo", "vram"])
    if rc != 0 or not out:
        return 0.0
    # rocm-smi output varies by version; look for MiB/GiB values
    match = re.search(r"Total Memory.*?:\s*([\d,]+)\s*([KMGk]i?B)", out, re.I)
    if match:
        val = float(match.group(1).replace(",", ""))
        unit = match.group(2).upper()
        if unit.startswith("G"):
            return round(val, 2)
        if unit.startswith("M"):
            return round(val / 1024, 2)
        if unit.startswith("K"):
            return round(val / 1_048_576, 2)
    return 0.0


def probe_gpu() -> dict:
    """Return GPU info dict: {type, vram_gb, driver_ok}."""
    nvidia_vram = get_nvidia_vram_gb()
    if nvidia_vram > 0:
        rc, driver_out, _ = run(["nvidia-smi"])
        return {"type": "nvidia", "vram_gb": nvidia_vram, "driver_ok": rc == 0}

    amd_vram = get_amd_vram_gb()
    if amd_vram > 0:
        return {"type": "amd", "vram_gb": amd_vram, "driver_ok": True}

    return {"type": "cpu", "vram_gb": 0.0, "driver_ok": False}


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------

def select_model(ram_gb: float, gpu: dict) -> dict:
    """
    Walk the catalogue top-to-bottom and return the first model the hardware
    can comfortably run.  We leave 1.5 GB RAM headroom for the OS/Docker.
    """
    usable_ram = max(0.0, ram_gb - 1.5)
    vram = gpu["vram_gb"]
    has_gpu = gpu["type"] in ("nvidia", "amd") and gpu["driver_ok"]

    for m in MODEL_CATALOGUE:
        if m["requires_gpu"] and not has_gpu:
            continue
        if m["requires_gpu"] and vram < m["min_vram_gb"]:
            continue
        if not m["requires_gpu"] and usable_ram < m["min_ram_gb"]:
            continue
        return m

    # Absolute fallback — should never reach here unless < 2 GB RAM
    return MODEL_CATALOGUE[-1]


# ---------------------------------------------------------------------------
# Ollama memory / thread tuning
# ---------------------------------------------------------------------------

def compute_ollama_env(ram_gb: float, cpu_cores: int, model: dict) -> dict:
    """
    Return environment variable dict for the Ollama container.
    Keep memory limits conservative to avoid OOM kills.
    """
    # Reserve 1.5 GB for OS; the rest is available for Ollama
    available_mb = max(512, int((ram_gb - 1.5) * 1024))
    # Cap context window so KV-cache fits in RAM
    context = model["context_tokens"]

    # Parallel requests: 1 on low-end, 2 on ≥8 GB
    num_parallel = 2 if ram_gb >= 8.0 else 1

    # Thread count: leave 1 core for OS/networking
    num_threads = max(1, cpu_cores - 1)

    return {
        "OLLAMA_NUM_PARALLEL": str(num_parallel),
        "OLLAMA_MAX_LOADED_MODELS": "1",
        "OLLAMA_NUM_THREAD": str(num_threads),
        "OLLAMA_CONTEXT_LENGTH": str(context),
        # Memory limit string for Docker (in MB)
        "_DOCKER_MEM_LIMIT_MB": str(available_mb),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("[detect_hardware] Probing system hardware…", file=sys.stderr)

    ram_gb = get_ram_gb()
    cpu_cores = get_cpu_cores()
    gpu = probe_gpu()

    print(f"  RAM:        {ram_gb} GB", file=sys.stderr)
    print(f"  CPU cores:  {cpu_cores}", file=sys.stderr)
    print(f"  GPU type:   {gpu['type']}", file=sys.stderr)
    print(f"  VRAM:       {gpu['vram_gb']} GB", file=sys.stderr)
    print(f"  Driver OK:  {gpu['driver_ok']}", file=sys.stderr)

    model = select_model(ram_gb, gpu)
    ollama_env = compute_ollama_env(ram_gb, cpu_cores, model)

    print(f"\n[detect_hardware] Selected model: {model['display']}", file=sys.stderr)
    print(f"  Reason: {model['reason']}", file=sys.stderr)

    result = {
        "hardware": {
            "ram_gb": ram_gb,
            "cpu_cores": cpu_cores,
            "gpu": gpu,
        },
        "model": model,
        "ollama_env": ollama_env,
    }

    # Print JSON to stdout for consumption by setup.sh
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
