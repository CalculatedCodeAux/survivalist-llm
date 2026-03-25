#!/usr/bin/env bash
# ============================================================
# Survivalist LLM Server — One-Line Installer
# ============================================================
# Usage (one-liner):
#   sudo bash -c "$(curl -fsSL https://your-host/survivalist-llm/setup.sh)"
#
# Or locally after cloning/downloading:
#   sudo bash setup.sh [--ssid NAME] [--password PASS] [--interface wlan0]
#
# What this script does:
#   1. Validates OS and hardware
#   2. Installs system dependencies (Docker, hostapd, dnsmasq, nginx)
#   3. Detects hardware and selects the best quantized model
#   4. Configures the WiFi Access Point (hostapd + dnsmasq)
#   5. Builds the docker-compose .env file
#   6. Pre-pulls the Ollama model (can take a while — shows progress)
#   7. Starts all containers
#   8. Installs the systemd service for auto-start on boot
#
# Requirements:
#   - Ubuntu 20.04 / 22.04 / 24.04 (aarch64 or x86_64)
#   - A WiFi adapter that supports AP (master) mode
#   - Internet connection for INITIAL setup only
#   - Root / sudo access
# ============================================================

set -euo pipefail
IFS=$'\n\t'

# ── Colour helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'  GREEN='\033[0;32m'  YELLOW='\033[1;33m'
BLUE='\033[0;34m' BOLD='\033[1m'      NC='\033[0m'

log()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()  { err "$*"; exit 1; }
banner() {
  echo -e "\n${BOLD}${GREEN}"
  echo "  ┌─────────────────────────────────────────────────┐"
  printf "  │  %-47s │\n" "$*"
  echo "  └─────────────────────────────────────────────────┘"
  echo -e "${NC}"
}

# ── Defaults (override with CLI flags) ─────────────────────────────────────
AP_SSID="SurvivalistLLM"
AP_PASSWORD="survival2025!"
AP_CHANNEL="6"
AP_IP="192.168.50.1"
AP_DHCP_START="192.168.50.100"
AP_DHCP_END="192.168.50.200"
COUNTRY_CODE="US"
INSTALL_DIR="/opt/survivalist-llm"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Parse CLI arguments ─────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    --ssid)       AP_SSID="$2";      shift 2 ;;
    --password)   AP_PASSWORD="$2";  shift 2 ;;
    --interface)  WLAN_IFACE="$2";   shift 2 ;;
    --channel)    AP_CHANNEL="$2";   shift 2 ;;
    --country)    COUNTRY_CODE="$2"; shift 2 ;;
    --ip)         AP_IP="$2";        shift 2 ;;
    --help|-h)
      echo "Usage: sudo bash setup.sh [OPTIONS]"
      echo "  --ssid        WiFi network name  (default: SurvivalistLLM)"
      echo "  --password    WiFi password       (default: survival2025!)"
      echo "  --interface   WiFi interface      (auto-detected)"
      echo "  --channel     WiFi channel 1-11   (default: 6)"
      echo "  --country     Country code        (default: US)"
      echo "  --ip          AP IP address       (default: 192.168.50.1)"
      exit 0 ;;
    *) warn "Unknown argument: $1"; shift ;;
  esac
done

# ── Pre-flight checks ────────────────────────────────────────────────────────
banner "Survivalist LLM Installer"

[[ $EUID -eq 0 ]] || die "This script must be run as root (use sudo)."

# Ubuntu version check
if [[ -f /etc/os-release ]]; then
  source /etc/os-release
  [[ "$ID" == "ubuntu" ]] || warn "Tested on Ubuntu; your OS ($ID) may require adjustments."
  log "Detected OS: $PRETTY_NAME"
else
  warn "/etc/os-release not found — proceeding anyway."
fi

# Architecture check
ARCH="$(uname -m)"
log "Architecture: $ARCH"
[[ "$ARCH" =~ (x86_64|aarch64|arm64) ]] || die "Unsupported architecture: $ARCH"

# ── Detect WiFi Interface ────────────────────────────────────────────────────
detect_wlan_interface() {
  # If the user specified one, trust it
  if [[ -n "${WLAN_IFACE:-}" ]]; then
    log "Using user-specified WiFi interface: $WLAN_IFACE"
    return
  fi

  # Look for interfaces in AP (master) mode or that support it
  local iface
  for iface in /sys/class/net/*/; do
    iface=$(basename "$iface")
    # Skip loopback and ethernet
    [[ "$iface" == lo* || "$iface" == eth* || "$iface" == en* ]] && continue
    # Check it's a wireless interface
    if [[ -d "/sys/class/net/$iface/wireless" ]]; then
      # Verify AP mode is supported
      if iw phy "$(cat /sys/class/net/$iface/phy80211/name 2>/dev/null || echo phy0)" \
           info 2>/dev/null | grep -q "AP"; then
        WLAN_IFACE="$iface"
        ok "Auto-detected AP-capable WiFi interface: $WLAN_IFACE"
        return
      else
        warn "Interface $iface found but may not support AP mode."
        WLAN_IFACE="$iface"
      fi
    fi
  done

  if [[ -z "${WLAN_IFACE:-}" ]]; then
    warn "No WiFi interface auto-detected. Defaulting to wlan0."
    warn "If setup fails, re-run with: sudo bash setup.sh --interface <your_iface>"
    WLAN_IFACE="wlan0"
  fi
}

# ── Step 1: System Update & Dependencies ────────────────────────────────────
install_dependencies() {
  banner "Step 1/7 — Installing Dependencies"

  log "Updating package lists…"
  apt-get update -qq

  log "Installing base packages…"
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg lsb-release \
    python3 python3-pip \
    hostapd dnsmasq \
    nginx \
    iw wireless-tools rfkill \
    iproute2 iptables-persistent netfilter-persistent \
    jq \
    2>/dev/null

  # ── Docker CE ──────────────────────────────────────────────────────────
  if ! command -v docker &>/dev/null; then
    log "Installing Docker CE…"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/ubuntu/gpg" \
      | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
      https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
      > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
    ok "Docker installed: $(docker --version)"
  else
    ok "Docker already installed: $(docker --version)"
  fi

  # ── NVIDIA Container Toolkit (optional) ──────────────────────────────
  if command -v nvidia-smi &>/dev/null; then
    log "NVIDIA GPU detected — installing nvidia-container-toolkit…"
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
      | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
      | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
      > /etc/apt/sources.list.d/nvidia-container-toolkit.list
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nvidia-container-toolkit
    nvidia-ctk runtime configure --runtime=docker
    systemctl restart docker
    ok "NVIDIA container toolkit installed."
  fi

  ok "All dependencies installed."
}

# ── Step 2: Hardware Detection & Model Selection ─────────────────────────────
detect_and_select_model() {
  banner "Step 2/7 — Hardware Detection & Model Selection"

  # Copy detect_hardware.py into the install dir
  cp "$REPO_DIR/detect_hardware.py" "$INSTALL_DIR/detect_hardware.py"
  chmod +x "$INSTALL_DIR/detect_hardware.py"

  log "Running hardware probe…"
  HW_JSON=$(python3 "$INSTALL_DIR/detect_hardware.py") || die "Hardware detection failed."
  echo "$HW_JSON" > "$INSTALL_DIR/.hardware.json"

  OLLAMA_MODEL=$(echo "$HW_JSON"       | jq -r '.model.name')
  MODEL_DISPLAY=$(echo "$HW_JSON"      | jq -r '.model.display')
  MODEL_REASON=$(echo "$HW_JSON"       | jq -r '.model.reason')
  GPU_LAYERS=$(echo "$HW_JSON"         | jq -r '.model.gpu_layers')
  CONTEXT_LEN=$(echo "$HW_JSON"        | jq -r '.model.context_tokens')
  OLLAMA_THREADS=$(echo "$HW_JSON"     | jq -r '.ollama_env.OLLAMA_NUM_THREAD')
  OLLAMA_PARALLEL=$(echo "$HW_JSON"    | jq -r '.ollama_env.OLLAMA_NUM_PARALLEL')
  DOCKER_MEM_MB=$(echo "$HW_JSON"      | jq -r '.ollama_env._DOCKER_MEM_LIMIT_MB')
  GPU_TYPE=$(echo "$HW_JSON"           | jq -r '.hardware.gpu.type')

  # Convert MB to Docker memory string
  if (( DOCKER_MEM_MB >= 1024 )); then
    OLLAMA_MEM_LIMIT="$(( DOCKER_MEM_MB / 1024 ))g"
  else
    OLLAMA_MEM_LIMIT="${DOCKER_MEM_MB}m"
  fi

  ok "Selected model : $MODEL_DISPLAY"
  log "Reason        : $MODEL_REASON"
  log "GPU layers    : $GPU_LAYERS"
  log "Context       : $CONTEXT_LEN tokens"
  log "Threads       : $OLLAMA_THREADS"
  log "Memory limit  : $OLLAMA_MEM_LIMIT"
}

# ── Step 3: Create Directory Structure ────────────────────────────────────────
create_directories() {
  banner "Step 3/7 — Setting Up Install Directory"

  mkdir -p \
    "$INSTALL_DIR"/{models,webui-data,config,scripts,logs,packs,admin-state,emergency}

  # Copy project files from repo
  cp "$REPO_DIR/docker-compose.yml"   "$INSTALL_DIR/docker-compose.yml"
  cp -r "$REPO_DIR/config/"           "$INSTALL_DIR/config/"
  cp -r "$REPO_DIR/systemd/"          "$INSTALL_DIR/systemd/"
  cp -r "$REPO_DIR/survivorpack-admin/" "$INSTALL_DIR/survivorpack-admin/"
  cp    "$REPO_DIR/emergency/index.html" "$INSTALL_DIR/emergency/index.html"

  # Create an empty library.xml so kiwix-serve can start before any packs are installed
  # (app.py also creates this on first boot, but setup.sh creates it here so the
  # kiwix-serve container doesn't fail its healthcheck on a brand-new install)
  if [[ ! -f "$INSTALL_DIR/packs/library.xml" ]]; then
    cat > "$INSTALL_DIR/packs/library.xml" <<'XML'
<?xml version='1.0' encoding='utf-8'?>
<library version="20110515" />
XML
    ok "Created empty library.xml"
  fi

  # Create helper scripts directory
  cat > "$INSTALL_DIR/scripts/ap-up.sh" <<'APUP'
#!/usr/bin/env bash
# Bring up the WiFi Access Point.
# Called by survivalist-llm.service ExecStartPre.
set -euo pipefail
source /opt/survivalist-llm/.env

IFACE="$WLAN_IFACE"
AP_IP="$AP_IP"

# Unblock WiFi radio (in case rfkill is active)
rfkill unblock wifi 2>/dev/null || true

# Kill any conflicting wpa_supplicant on this interface
pkill -f "wpa_supplicant.*${IFACE}" 2>/dev/null || true
sleep 0.5

# Assign static IP to the AP interface
ip addr flush dev "$IFACE" 2>/dev/null || true
ip addr add "${AP_IP}/24" dev "$IFACE"
ip link set "$IFACE" up

# Start hostapd (in background; systemd will track child processes)
hostapd -B /opt/survivalist-llm/config/hostapd.conf \
  -f /opt/survivalist-llm/logs/hostapd.log

# Start dnsmasq
systemctl restart dnsmasq

# Start nginx reverse proxy
systemctl restart nginx

echo "[ap-up] Access Point is live on interface $IFACE at $AP_IP"
APUP

  cat > "$INSTALL_DIR/scripts/ap-down.sh" <<'APDOWN'
#!/usr/bin/env bash
# Tear down the WiFi Access Point.
# Called by survivalist-llm.service ExecStopPost.
set -euo pipefail
source /opt/survivalist-llm/.env

pkill hostapd 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true
ip addr flush dev "$WLAN_IFACE" 2>/dev/null || true
echo "[ap-down] Access Point stopped."
APDOWN

  cat > "$INSTALL_DIR/scripts/wait-healthy.sh" <<'WAIT'
#!/usr/bin/env bash
# Wait for all four services to become healthy before declaring startup complete.
set -euo pipefail

wait_for() {
  local name="$1" url="$2" max="${3:-120}"
  local elapsed=0
  echo "[wait-healthy] Waiting for ${name}…"
  while ! curl -sf "$url" >/dev/null 2>&1; do
    sleep 3; elapsed=$(( elapsed + 3 ))
    if (( elapsed >= max )); then
      echo "[wait-healthy] Timed out waiting for ${name}." >&2
      return 1
    fi
  done
  echo "[wait-healthy] ${name} ready after ${elapsed}s."
}

wait_for "Ollama"            "http://127.0.0.1:11434/api/tags"          120
wait_for "Open WebUI"        "http://127.0.0.1:8080/health"             120
wait_for "survivorpack-admin" "http://127.0.0.1:5000/health"            120
wait_for "kiwix-serve"       "http://127.0.0.1:8888/catalog/search"     60
WAIT

  chmod +x "$INSTALL_DIR/scripts/"*.sh
  chmod 700 "$INSTALL_DIR"          # Only root should read secrets
  ok "Directory structure created at $INSTALL_DIR"
}

# ── Step 4: Configure WiFi Access Point ────────────────────────────────────
configure_ap() {
  banner "Step 4/7 — Configuring WiFi Access Point"

  detect_wlan_interface

  # ── Tell NetworkManager to leave our AP interface alone ────────────────
  if command -v nmcli &>/dev/null; then
    log "Configuring NetworkManager to ignore $WLAN_IFACE…"
    mkdir -p /etc/NetworkManager/conf.d
    cat > /etc/NetworkManager/conf.d/99-survivalist-ap.conf <<NM
[keyfile]
unmanaged-devices=interface-name:${WLAN_IFACE}
NM
    systemctl reload NetworkManager 2>/dev/null || true
  fi

  # ── Disable systemd-resolved stub listener (conflicts with dnsmasq) ────
  if systemctl is-active --quiet systemd-resolved; then
    log "Adjusting systemd-resolved (disabling stub listener)…"
    mkdir -p /etc/systemd/resolved.conf.d
    cat > /etc/systemd/resolved.conf.d/99-no-stub.conf <<RESOLVED
[Resolve]
DNSStubListener=no
RESOLVED
    systemctl restart systemd-resolved
    # Re-link resolv.conf to use resolved without the stub
    ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf
  fi

  # ── Write hostapd.conf ─────────────────────────────────────────────────
  log "Writing hostapd.conf…"
  sed \
    -e "s|^interface=.*|interface=${WLAN_IFACE}|" \
    -e "s|^wpa_passphrase=.*|wpa_passphrase=${AP_PASSWORD}|" \
    -e "s|^ssid=.*|ssid=${AP_SSID}|" \
    -e "s|^channel=.*|channel=${AP_CHANNEL}|" \
    -e "s|^country_code=.*|country_code=${COUNTRY_CODE}|" \
    "$INSTALL_DIR/config/hostapd.conf" \
    > /etc/hostapd/survivalist.conf
  chmod 600 /etc/hostapd/survivalist.conf

  # Update hostapd default config to point to ours
  echo 'DAEMON_CONF="/etc/hostapd/survivalist.conf"' > /etc/default/hostapd

  # ── Write dnsmasq.conf ─────────────────────────────────────────────────
  log "Writing dnsmasq.conf…"
  DHCP_RANGE="${AP_DHCP_START},${AP_DHCP_END},255.255.255.0,12h"
  sed \
    -e "s|^interface=.*|interface=${WLAN_IFACE}|" \
    -e "s|^dhcp-range=.*|dhcp-range=${DHCP_RANGE}|" \
    -e "s|dhcp-option=3,.*|dhcp-option=3,${AP_IP}|" \
    -e "s|dhcp-option=6,.*|dhcp-option=6,${AP_IP}|" \
    -e "s|address=/#/.*|address=/#/${AP_IP}|" \
    -e "s|address=/llm.local/.*|address=/llm.local/${AP_IP}|" \
    -e "s|address=/chat.local/.*|address=/chat.local/${AP_IP}|" \
    "$INSTALL_DIR/config/dnsmasq.conf" \
    > /etc/dnsmasq.d/survivalist.conf

  # Disable the default dnsmasq config to avoid conflicts
  mv /etc/dnsmasq.conf /etc/dnsmasq.conf.bak 2>/dev/null || true
  touch /etc/dnsmasq.conf

  # ── Write nginx.conf ───────────────────────────────────────────────────
  log "Writing nginx reverse-proxy config…"
  cp "$INSTALL_DIR/config/nginx.conf" /etc/nginx/nginx.conf
  nginx -t 2>/dev/null && ok "nginx config valid." || warn "nginx config test failed — check /etc/nginx/nginx.conf"

  # ── Write .env file ────────────────────────────────────────────────────
  cat > "$INSTALL_DIR/.env" <<ENV
# Auto-generated by setup.sh — do not edit manually.
# Re-run setup.sh to regenerate.
WLAN_IFACE=${WLAN_IFACE}
AP_IP=${AP_IP}
AP_SSID=${AP_SSID}
AP_PASSWORD=${AP_PASSWORD}

# Model selection (from detect_hardware.py)
OLLAMA_MODEL=${OLLAMA_MODEL}
OLLAMA_NUM_PARALLEL=${OLLAMA_PARALLEL}
OLLAMA_MAX_LOADED_MODELS=1
OLLAMA_NUM_THREAD=${OLLAMA_THREADS}
OLLAMA_CONTEXT_LENGTH=${CONTEXT_LEN}
OLLAMA_MEM_LIMIT=${OLLAMA_MEM_LIMIT}
GPU_TYPE=${GPU_TYPE}
GPU_LAYERS=${GPU_LAYERS}
ENV
  chmod 600 "$INSTALL_DIR/.env"

  ok "Access Point configured: SSID='${AP_SSID}', Interface=${WLAN_IFACE}, IP=${AP_IP}"
}

# ── Step 5: Enable GPU in Docker Compose (if applicable) ─────────────────────
patch_compose_for_gpu() {
  if [[ "$GPU_TYPE" == "nvidia" ]]; then
    log "Patching docker-compose.yml for NVIDIA GPU…"
    # Add deploy.resources.reservations.devices block to ollama service
    python3 - <<'PATCH'
import re, sys

path = "/opt/survivalist-llm/docker-compose.yml"
with open(path) as f:
    content = f.read()

gpu_block = """    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
"""
# Insert after the ollama service's mem_limit line
content = re.sub(
    r'(  ollama:.*?)(  mem_limit:)',
    lambda m: m.group(1) + gpu_block + m.group(2),
    content,
    flags=re.DOTALL
)
with open(path, "w") as f:
    f.write(content)
print("  GPU block injected into docker-compose.yml")
PATCH
    ok "docker-compose.yml patched for NVIDIA GPU acceleration."
  fi
}

# ── Step 6: Pull Images & Download Model ─────────────────────────────────────
pull_images_and_model() {
  banner "Step 5/7 — Pulling Docker Images & Downloading LLM"

  log "Pulling Ollama image (0.18.2)…"
  docker pull ollama/ollama:0.18.2

  log "Pulling Open WebUI image (0.8.10)…"
  docker pull ghcr.io/open-webui/open-webui:0.8.10

  log "Pulling kiwix-tools image (3.8.2)…"
  docker pull ghcr.io/kiwix/kiwix-tools:3.8.2

  log "Building survivorpack-admin image…"
  docker build -t survivorpack-admin:latest "$INSTALL_DIR/survivorpack-admin/" \
    || die "survivorpack-admin image build failed."
  ok "survivorpack-admin image built."

  # ── Pre-pull the model ──────────────────────────────────────────────────
  log "Downloading model: ${OLLAMA_MODEL}"
  log "(This is a one-time download. Progress is shown below.)"

  # Start a temporary Ollama container for the pull
  docker run --rm \
    -v "$INSTALL_DIR/models:/root/.ollama" \
    ${GPU_TYPE:+--gpus all} \
    ollama/ollama:0.18.2 \
    ollama pull "${OLLAMA_MODEL}" \
    || die "Model download failed. Check your internet connection and model name."

  ok "Model '${OLLAMA_MODEL}' downloaded successfully."
}

# ── Step 7: Start Stack & Install systemd Service ─────────────────────────────
start_and_enable() {
  banner "Step 6/7 — Starting Services"

  # ── Bring up the AP first ────────────────────────────────────────────
  log "Bringing up WiFi Access Point…"
  bash "$INSTALL_DIR/scripts/ap-up.sh" || warn "AP bring-up had errors — check logs."

  # ── Start Docker stack ───────────────────────────────────────────────
  log "Starting Docker Compose stack…"
  docker compose -f "$INSTALL_DIR/docker-compose.yml" \
    --env-file "$INSTALL_DIR/.env" \
    up -d --remove-orphans

  # ── Wait for Ollama ──────────────────────────────────────────────────
  bash "$INSTALL_DIR/scripts/wait-healthy.sh" || warn "Ollama health check timed out."

  # ── Install systemd service ──────────────────────────────────────────
  banner "Step 7/7 — Installing systemd Service"
  log "Registering survivalist-llm.service for auto-start on boot…"

  # Patch the service file with the correct install dir
  sed "s|/opt/survivalist-llm|${INSTALL_DIR}|g" \
    "$INSTALL_DIR/systemd/survivalist-llm.service" \
    > /etc/systemd/system/survivalist-llm.service

  systemctl daemon-reload
  systemctl enable survivalist-llm.service
  ok "Service enabled — will auto-start on every boot."
}

# ── Final Summary ─────────────────────────────────────────────────────────────
print_summary() {
  banner "Installation Complete!"

  # Check if Open WebUI is responding
  sleep 5
  WEBUI_STATUS="checking…"
  if curl -sf "http://${AP_IP}/" >/dev/null 2>&1; then
    WEBUI_STATUS="${GREEN}ONLINE${NC}"
  else
    WEBUI_STATUS="${YELLOW}starting up (wait ~30s)${NC}"
  fi

  echo -e "${BOLD}"
  echo "  ┌─────────────────────────────────────────────────────────────┐"
  echo "  │  SURVIVALIST LLM SERVER READY                               │"
  echo "  │                                                             │"
  printf "  │  WiFi Network : %-44s│\n" "$AP_SSID"
  printf "  │  Password     : %-44s│\n" "$AP_PASSWORD"
  printf "  │  Model        : %-44s│\n" "$MODEL_DISPLAY"
  echo "  │                                                             │"
  echo "  │  FROM YOUR PHONE / TABLET:                                  │"
  printf "  │    1. Connect to WiFi: %-38s│\n" "\"$AP_SSID\""
  echo "  │    2. Open browser → http://llm.local/                      │"
  echo "  │       (or http://${AP_IP}/)                     │"
  echo "  │                                                             │"
  echo "  │  URLS:                                                      │"
  echo "  │    Chat:       http://llm.local/                            │"
  echo "  │    Admin:      http://llm.local/admin                       │"
  echo "  │    Library:    http://llm.local/library                     │"
  echo "  │    Emergency:  http://llm.local/emergency                   │"
  echo "  │                                                             │"
  echo "  │  MANAGE:                                                    │"
  echo "  │    sudo systemctl status survivalist-llm                    │"
  echo "  │    sudo journalctl -fu survivalist-llm                      │"
  echo "  │    sudo bash /opt/survivalist-llm/scripts/ap-up.sh          │"
  echo "  └─────────────────────────────────────────────────────────────┘"
  echo -e "${NC}"
  echo -e "  Web UI status: ${WEBUI_STATUS}"
  echo ""
  echo -e "  Logs: ${INSTALL_DIR}/logs/  |  Config: ${INSTALL_DIR}/.env"
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
  install_dependencies
  create_directories          # Must run before detect (copies detect_hardware.py)
  detect_and_select_model
  configure_ap
  patch_compose_for_gpu
  pull_images_and_model
  start_and_enable
  print_summary
}

main "$@"
