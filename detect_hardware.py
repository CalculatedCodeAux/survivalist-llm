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
import platform
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


# ---------------------------------------------------------------------------
# Product tiers — maps detected hardware to a named product tier.
# Used by setup.sh for display messages and by marketing tooling.
#
# Tier        | Hardware              | Default model | Expected t/s
# ------------|-----------------------|---------------|-------------
# pi          | Raspberry Pi (ARM64)  | 3B Q4         | 5-8 t/s
# n100        | Intel N100 / x86_64   | 3B Q4         | 18-25 t/s
# performance | x86_64 with dGPU      | 7B+ GPU       | 30-60 t/s
# unknown     | Other                 | auto-select   | varies
#
# On ARM we cap at 3B regardless of RAM — 7B on Pi 5 ARM runs at ~2-4 t/s
# which feels broken to a first-time user.  Better to run 3B fast than 7B slow.
# ---------------------------------------------------------------------------
PRODUCT_TIERS = {
    "pi":          {"display": "SurvivorBox (Pi Edition)", "max_model_id": "cpu-small"},
    "n100":        {"display": "SurvivorBox (Standard)",   "max_model_id": "cpu-mid"},
    "performance": {"display": "SurvivorBox (Performance)", "max_model_id": None},   # no cap
    "unknown":     {"display": "SurvivorOS",               "max_model_id": None},
}

# Model ID order for tier capping — lower index = lighter model
_MODEL_ID_ORDER = ["cpu-tiny", "cpu-small", "cpu-mid", "cpu-large",
                   "gpu-small", "gpu-mid", "gpu-large"]


def get_cpu_arch() -> str:
    """Return normalised CPU architecture string: 'arm64', 'x86_64', or 'unknown'."""
    machine = platform.machine().lower()
    if machine in ("aarch64", "arm64", "armv8l"):
        return "arm64"
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    return machine


def detect_product_tier(arch: str, gpu: dict, ram_gb: float) -> str:
    """
    Classify hardware into a named product tier.

    Tier caps only apply to hardware that resembles an actual SurvivorBox
    product unit (≤ 16 GB RAM).  Dev machines and servers with more RAM
    fall through to "unknown" so model selection is driven purely by RAM
    capacity and the full catalogue is available.
    """
    has_gpu = gpu["type"] in ("nvidia", "amd") and gpu["driver_ok"] and gpu["vram_gb"] >= 2.5
    if arch == "arm64":
        return "pi"
    if arch == "x86_64" and has_gpu:
        return "performance"
    if arch == "x86_64" and ram_gb <= 16.0:
        # Looks like a product-unit N100 (8–16 GB is the normal config)
        return "n100"
    # Dev machine, server, or anything else — no cap, let RAM decide
    return "unknown"


def apply_tier_cap(model: dict, tier: str) -> dict:
    """
    If the detected tier has a max_model_id, ensure we don't select a
    heavier model than the tier allows.  Returns the original or a lighter model.
    """
    max_id = PRODUCT_TIERS[tier]["max_model_id"]
    if max_id is None:
        return model  # no cap for this tier

    current_idx = _MODEL_ID_ORDER.index(model["id"]) if model["id"] in _MODEL_ID_ORDER else 999
    max_idx = _MODEL_ID_ORDER.index(max_id) if max_id in _MODEL_ID_ORDER else 999

    if current_idx <= max_idx:
        return model  # already within the cap

    # Walk back to the heaviest model that fits the cap
    for candidate in MODEL_CATALOGUE:
        if candidate["id"] == max_id:
            return candidate
    return model  # fallback: shouldn't happen


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
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemTotal"):
                kb = int(line.split()[1])
                return round(kb / 1_048_576, 2)
    raise RuntimeError("/proc/meminfo: MemTotal line not found")


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
    can run.  min_ram_gb in each entry is total RAM required (OS overhead
    already accounted for in the catalogue values).
    """
    usable_ram = ram_gb
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

    if ram_gb < 2.0:
        print(f"ERROR: {ram_gb} GB RAM detected. SurvivorOS requires ≥ 2 GB RAM.", file=sys.stderr)
        sys.exit(1)

    cpu_cores = get_cpu_cores()
    gpu = probe_gpu()
    arch = get_cpu_arch()
    tier = detect_product_tier(arch, gpu, ram_gb)

    print(f"  RAM:        {ram_gb} GB", file=sys.stderr)
    print(f"  CPU cores:  {cpu_cores}", file=sys.stderr)
    print(f"  Arch:       {arch}", file=sys.stderr)
    print(f"  GPU type:   {gpu['type']}", file=sys.stderr)
    print(f"  VRAM:       {gpu['vram_gb']} GB", file=sys.stderr)
    print(f"  Driver OK:  {gpu['driver_ok']}", file=sys.stderr)
    print(f"  Tier:       {tier} ({PRODUCT_TIERS[tier]['display']})", file=sys.stderr)

    model = select_model(ram_gb, gpu)
    model = apply_tier_cap(model, tier)
    ollama_env = compute_ollama_env(ram_gb, cpu_cores, model)

    print(f"\n[detect_hardware] Selected model: {model['display']}", file=sys.stderr)
    print(f"  Reason: {model['reason']}", file=sys.stderr)

    result = {
        "hardware": {
            "ram_gb": ram_gb,
            "cpu_cores": cpu_cores,
            "arch": arch,
            "gpu": gpu,
        },
        "tier": tier,
        "tier_display": PRODUCT_TIERS[tier]["display"],
        "model": model,
        "ollama_env": ollama_env,
    }

    # Print JSON to stdout for consumption by setup.sh
    output = json.dumps(result, indent=2)
    print(output)
    return output


if __name__ == "__main__":
    main()
