#!/usr/bin/env bash
# ============================================================
# Survivalist LLM Server — Uninstaller
# ============================================================
# Usage:
#   sudo bash uninstall.sh
#   sudo bash uninstall.sh --keep-models   # skip model deletion prompt
#   sudo bash uninstall.sh --yes           # non-interactive (keeps models)
# ============================================================

set -euo pipefail
IFS=$'\n\t'

RED='\033[0;31m'  GREEN='\033[0;32m'  YELLOW='\033[1;33m'
BLUE='\033[0;34m' BOLD='\033[1m'      NC='\033[0m'

log()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()     { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()   { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()    { echo -e "${RED}[ERROR]${NC} $*" >&2; }
banner() {
  echo -e "\n${BOLD}${RED}"
  echo "  ┌─────────────────────────────────────────────────┐"
  printf "  │  %-47s │\n" "$*"
  echo "  └─────────────────────────────────────────────────┘"
  echo -e "${NC}"
}

INSTALL_DIR="/opt/survivalist-llm"
KEEP_MODELS=false
NON_INTERACTIVE=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --keep-models) KEEP_MODELS=true;      shift ;;
    --yes|-y)      NON_INTERACTIVE=true;  shift ;;
    --help|-h)
      echo "Usage: sudo bash uninstall.sh [OPTIONS]"
      echo "  --keep-models   Skip prompt and keep LLM model files"
      echo "  --yes           Non-interactive (implies --keep-models)"
      exit 0 ;;
    *) warn "Unknown argument: $1"; shift ;;
  esac
done

[[ $EUID -eq 0 ]] || { err "This script must be run as root (use sudo)."; exit 1; }

banner "Survivalist LLM — Uninstaller"

# ── Confirm ───────────────────────────────────────────────────────────────────
if [[ "$NON_INTERACTIVE" == false ]]; then
  echo -e "${YELLOW}${BOLD}This will remove the Survivalist LLM server from this machine.${NC}"
  echo "  • Stop and disable the survivalist-llm systemd service"
  echo "  • Remove Docker containers and images"
  echo "  • Remove all config files and install directory"
  echo "  • Restore network settings (NetworkManager, hostapd, dnsmasq)"
  echo ""
  read -rp "  Are you sure you want to uninstall? [y/N] " _CONFIRM
  case "$_CONFIRM" in
    [yY]|[yY][eE][sS]) ok "Proceeding with uninstall." ;;
    *) echo "Aborted."; exit 0 ;;
  esac
fi

# ── Model files decision ──────────────────────────────────────────────────────
MODEL_DIR="$INSTALL_DIR/models"
MODEL_SIZE=""
if [[ -d "$MODEL_DIR" ]] && [[ "$(ls -A "$MODEL_DIR" 2>/dev/null)" ]]; then
  MODEL_SIZE=$(du -sh "$MODEL_DIR" 2>/dev/null | cut -f1 || echo "unknown")
fi

if [[ "$NON_INTERACTIVE" == false && "$KEEP_MODELS" == false && -n "$MODEL_SIZE" ]]; then
  echo ""
  echo -e "${BOLD}LLM Model Files${NC}"
  echo "  The downloaded model files at '$MODEL_DIR' take ${MODEL_SIZE} of disk space."
  echo "  These files can take a long time to re-download (~hours)."
  echo ""
  read -rp "  Delete model files? [y/N] " _MODEL_CONFIRM
  case "$_MODEL_CONFIRM" in
    [yY]|[yY][eE][sS]) KEEP_MODELS=false ;;
    *) KEEP_MODELS=true ;;
  esac
fi

# ── Step 1: Stop and disable systemd service ──────────────────────────────────
banner "Step 1 — Stopping Services"

if systemctl is-active --quiet survivalist-llm 2>/dev/null; then
  log "Stopping survivalist-llm service…"
  systemctl stop survivalist-llm || warn "Service stop failed — continuing."
else
  log "Service not running."
fi

if systemctl is-enabled --quiet survivalist-llm 2>/dev/null; then
  log "Disabling survivalist-llm service…"
  systemctl disable survivalist-llm || warn "Service disable failed — continuing."
fi

if [[ -f /etc/systemd/system/survivalist-llm.service ]]; then
  rm -f /etc/systemd/system/survivalist-llm.service
  systemctl daemon-reload
  ok "systemd service removed."
fi

# ── Step 2: Tear down the WiFi Access Point ───────────────────────────────────
banner "Step 2 — Removing WiFi Access Point"

# Run ap-down.sh if it exists
if [[ -f "$INSTALL_DIR/scripts/ap-down.sh" ]]; then
  bash "$INSTALL_DIR/scripts/ap-down.sh" 2>/dev/null || true
  ok "AP brought down."
fi

# Kill hostapd if still running
pkill -f hostapd 2>/dev/null || true

# Remove hostapd config
if [[ -f /etc/hostapd/survivalist.conf ]]; then
  rm -f /etc/hostapd/survivalist.conf
  ok "Removed /etc/hostapd/survivalist.conf"
fi

# Restore /etc/default/hostapd
if grep -q 'survivalist' /etc/default/hostapd 2>/dev/null; then
  echo '# DAEMON_CONF=""' > /etc/default/hostapd
  ok "Restored /etc/default/hostapd"
fi

# Remove NetworkManager override
if [[ -f /etc/NetworkManager/conf.d/99-survivalist-ap.conf ]]; then
  rm -f /etc/NetworkManager/conf.d/99-survivalist-ap.conf
  systemctl reload NetworkManager 2>/dev/null || true
  ok "Removed NetworkManager override — WiFi adapter returned to NetworkManager."
fi

# Remove dnsmasq config
if [[ -f /etc/dnsmasq.d/survivalist.conf ]]; then
  rm -f /etc/dnsmasq.d/survivalist.conf
  ok "Removed dnsmasq survivalist config."
fi

# Restore /etc/dnsmasq.conf if we replaced it with a blank one
if [[ -f /etc/dnsmasq.conf.bak ]]; then
  mv /etc/dnsmasq.conf.bak /etc/dnsmasq.conf
  ok "Restored /etc/dnsmasq.conf from backup."
elif [[ -f /etc/dnsmasq.conf ]] && [[ ! -s /etc/dnsmasq.conf ]]; then
  rm -f /etc/dnsmasq.conf
fi

# Stop dnsmasq — it was started by setup.sh for the WiFi AP.
# Do NOT restart it: dnsmasq running with default config binds 0.0.0.0:53,
# which blocks systemd-resolved from rebinding its stub listener (127.0.0.53:53)
# in Step 5 below, breaking DNS resolution after uninstall.
systemctl stop dnsmasq 2>/dev/null || true
systemctl disable dnsmasq 2>/dev/null || true
ok "Stopped dnsmasq."

# Remove nginx config (restore default)
if [[ -f /etc/nginx/nginx.conf ]] && grep -q 'survivalist\|llm\.local' /etc/nginx/nginx.conf 2>/dev/null; then
  if [[ -f /etc/nginx/nginx.conf.bak ]]; then
    mv /etc/nginx/nginx.conf.bak /etc/nginx/nginx.conf
  else
    # Restore Ubuntu's default nginx.conf
    cat > /etc/nginx/nginx.conf <<'NGINX'
user www-data;
worker_processes auto;
pid /run/nginx.pid;
include /etc/nginx/modules-enabled/*.conf;

events {
    worker_connections 768;
}

http {
    sendfile on;
    tcp_nopush on;
    types_hash_max_size 2048;
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    ssl_protocols TLSv1 TLSv1.1 TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    access_log /var/log/nginx/access.log;
    error_log /var/log/nginx/error.log;
    gzip on;
    include /etc/nginx/conf.d/*.conf;
    include /etc/nginx/sites-enabled/*;
}
NGINX
  fi
  systemctl stop nginx 2>/dev/null || true
  systemctl disable nginx 2>/dev/null || true
  ok "Stopped and disabled nginx."
fi

# ── Step 3: Remove Docker resources ──────────────────────────────────────────
banner "Step 3 — Removing Docker Resources"

# Stop and remove all containers from our compose stack
if [[ -f "$INSTALL_DIR/docker-compose.yml" ]] && command -v docker &>/dev/null; then
  log "Stopping Docker Compose stack…"
  docker compose -f "$INSTALL_DIR/docker-compose.yml" \
    --env-file "$INSTALL_DIR/.env" \
    down --remove-orphans 2>/dev/null || \
  docker compose -f "$INSTALL_DIR/docker-compose.yml" \
    down --remove-orphans 2>/dev/null || \
  warn "docker compose down failed — containers may need manual removal."
  ok "Docker Compose stack stopped."
fi

# Remove survivorpack-admin image (we built it, so we own it)
if docker image inspect survivorpack-admin:latest &>/dev/null; then
  docker image rm survivorpack-admin:latest 2>/dev/null || warn "Could not remove survivorpack-admin image."
  ok "Removed survivorpack-admin Docker image."
fi

ok "Docker cleanup complete. (Pulled images like ollama/ollama and open-webui are NOT removed — run 'docker image prune' manually if desired.)"

# ── Step 4: Remove install directory ─────────────────────────────────────────
banner "Step 4 — Removing Install Directory"

if [[ -d "$INSTALL_DIR" ]]; then
  if [[ "$KEEP_MODELS" == true ]]; then
    # Save models to a temp location, remove everything else, put models back
    MODELS_TMP=$(mktemp -d)
    if [[ -d "$MODEL_DIR" ]] && [[ "$(ls -A "$MODEL_DIR" 2>/dev/null)" ]]; then
      log "Preserving model files…"
      mv "$MODEL_DIR" "$MODELS_TMP/models"
    fi
    rm -rf "$INSTALL_DIR"
    mkdir -p "$INSTALL_DIR"
    if [[ -d "$MODELS_TMP/models" ]]; then
      mv "$MODELS_TMP/models" "$INSTALL_DIR/models"
      rmdir "$MODELS_TMP" 2>/dev/null || true
      ok "Model files preserved at $INSTALL_DIR/models/ (${MODEL_SIZE})"
      echo -e "  ${YELLOW}To delete them later:${NC}  sudo rm -rf $INSTALL_DIR"
    fi
  else
    log "Removing $INSTALL_DIR (including model files)…"
    rm -rf "$INSTALL_DIR"
    ok "Removed $INSTALL_DIR"
  fi
else
  warn "$INSTALL_DIR not found — skipping."
fi

# ── Step 5: Remove systemd-resolved override (if ours) ───────────────────────
if [[ -f /etc/systemd/resolved.conf.d/99-no-stub.conf ]]; then
  rm -f /etc/systemd/resolved.conf.d/99-no-stub.conf
  systemctl restart systemd-resolved 2>/dev/null || true
  # Restore /etc/resolv.conf symlink to the stub resolver
  ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf 2>/dev/null || true
  ok "Restored systemd-resolved stub listener."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
banner "Uninstall Complete"

echo "  Survivalist LLM Server has been removed."
if [[ "$KEEP_MODELS" == true ]]; then
  echo ""
  echo -e "  ${YELLOW}Model files kept at:${NC} $INSTALL_DIR/models/"
  echo "  Re-run setup.sh to reinstall (models won't need re-downloading)."
fi
echo ""
echo "  Your WiFi adapter has been returned to NetworkManager."
echo "  You may need to reconnect to your WiFi network manually."
echo ""
