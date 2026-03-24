# Survivalist LLM Server

A self-contained, fully offline AI assistant that broadcasts its own WiFi network.
Connect any phone, tablet, or laptop — zero internet required after initial setup.

---

## Hardware Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| RAM | 4 GB | 8–16 GB |
| Storage | 10 GB free | 20 GB free |
| CPU | Any x86_64 / ARM64 | 4+ cores |
| WiFi adapter | AP-mode capable | AP + 5 GHz |
| GPU *(optional)* | — | NVIDIA 4 GB+ VRAM |
| OS | Ubuntu 20.04 | Ubuntu 22.04 / 24.04 |

> **How to check if your WiFi adapter supports AP mode:**
> ```bash
> iw list | grep -A 10 "Supported interface modes" | grep " AP"
> ```
> If it prints `* AP`, you're good.

---

## One-Line Install

> **Requires internet access for this step only.**

```bash
sudo bash setup.sh
```

Or with custom WiFi settings:

```bash
sudo bash setup.sh \
  --ssid "BunkerNet" \
  --password "MySecretPass123" \
  --country US \
  --channel 6
```

The installer will:
1. Install Docker, hostapd, dnsmasq, nginx
2. Auto-detect your RAM, CPU, and GPU
3. Choose the best quantized model for your hardware
4. Download the model (~2–9 GB depending on tier)
5. Configure and start the WiFi hotspot
6. Register a systemd service to auto-start everything on boot

**Estimated setup time:** 10–40 minutes depending on internet speed and hardware.

---

## Accessing the AI From Your Devices

### Phone / Tablet
1. Go to **WiFi Settings**
2. Connect to `SurvivalistLLM` (or your custom SSID)
3. Enter the password: `survival2025!`
4. Open any browser → navigate to **http://llm.local/**
   - Fallback URL: **http://192.168.50.1/**
   - On iOS/Android, the browser may open automatically (captive portal)

### Laptop / Desktop
Same WiFi steps, then open `http://llm.local/` in any browser.

> No app install needed. The interface is mobile-optimised.

---

## Model Selection Matrix

The installer auto-selects the best model. Here's what it chooses:

| RAM | GPU VRAM | Model | Quality |
|-----|----------|-------|---------|
| ≥10 GB | — | Llama 3.1 8B Q4_K_M | ★★★★ |
| 7–10 GB | — | Mistral 7B Q4_K_M | ★★★★ |
| 4–7 GB | — | Llama 3.2 3B Q4_K_M | ★★★ |
| <4 GB | — | Phi-3 Mini Q2_K | ★★ |
| Any | ≥10 GB NVIDIA | Llama 3.1 8B Q8_0 (GPU) | ★★★★★ |
| Any | 5–10 GB NVIDIA | Mistral 7B Q4_K_M (GPU) | ★★★★ |
| Any | 2.5–5 GB NVIDIA | Phi-3 Mini Q4_K_M (GPU) | ★★★ |

To override the auto-selection after install:
```bash
cd /opt/survivalist-llm
# Edit .env:  OLLAMA_MODEL=phi3:mini-instruct-4k-q4_K_M
sudo nano .env
sudo systemctl restart survivalist-llm
```

---

## Service Management

```bash
# Check status
sudo systemctl status survivalist-llm

# View live logs
sudo journalctl -fu survivalist-llm

# Restart everything (AP + containers)
sudo systemctl restart survivalist-llm

# Stop (AP goes down, containers stop)
sudo systemctl stop survivalist-llm

# Disable auto-start (manual control only)
sudo systemctl disable survivalist-llm

# Check individual containers
sudo docker ps
sudo docker logs survivalist-ollama
sudo docker logs survivalist-webui
```

---

## Changing the WiFi Password

```bash
sudo nano /etc/hostapd/survivalist.conf
# Edit:  wpa_passphrase=YourNewPassword

sudo nano /opt/survivalist-llm/.env
# Edit:  AP_PASSWORD=YourNewPassword

sudo systemctl restart survivalist-llm
```

---

## Changing the WiFi Channel

For less interference in a crowded environment:
```bash
sudo nano /etc/hostapd/survivalist.conf
# Edit channel= to 1, 6, or 11 (non-overlapping 2.4 GHz channels)
sudo systemctl restart survivalist-llm
```

For 5 GHz (faster, shorter range — not all adapters support this):
```bash
# In hostapd.conf:
#   hw_mode=a
#   channel=36  (or 40, 44, 48, 149, 153, 157, 161)
```

---

## Adding / Swapping Models

While connected to the internet:
```bash
# Pull an additional model
sudo docker exec survivalist-ollama ollama pull gemma2:2b-instruct-q4_K_M

# List available models
sudo docker exec survivalist-ollama ollama list

# Switch default in the UI: open http://llm.local → top-right model selector
```

Models are stored in `/opt/survivalist-llm/models/` and persist across restarts.

---

## Backup & Restore

**Backup models and chat history:**
```bash
sudo tar -czf survivalist-backup-$(date +%Y%m%d).tar.gz \
  /opt/survivalist-llm/models \
  /opt/survivalist-llm/webui-data \
  /opt/survivalist-llm/.env
```

**Restore on a new machine:**
```bash
sudo tar -xzf survivalist-backup-YYYYMMDD.tar.gz -C /
# Then run setup.sh — it will skip the model download if files already exist
sudo bash setup.sh
```

---

## Troubleshooting

### WiFi AP not showing up
```bash
# Check if hostapd is running
pgrep -a hostapd

# Check logs
sudo journalctl -u survivalist-llm --since "5 min ago"
cat /opt/survivalist-llm/logs/hostapd.log

# Verify your adapter supports AP mode
iw list | grep -A5 "Supported interface modes"

# Try specifying the interface manually
sudo bash setup.sh --interface wlan1
```

### AI responses are very slow
- CPU-only on <8 GB RAM is normal — expect 2–10 tokens/second
- Make sure no other heavy processes are running: `htop`
- Try a smaller/more quantized model (Q2_K uses ~40% less RAM than Q4_K_M)
- If you have an NVIDIA GPU, verify it's being used: `watch -n1 nvidia-smi`

### "Connection refused" or web UI not loading
```bash
# Check all containers are running
sudo docker ps

# Check nginx is running
sudo systemctl status nginx

# Test Ollama directly
curl http://192.168.50.1:11434/api/tags  # from the server itself
# or (from server):
curl http://127.0.0.1:11434/api/tags

# Restart the stack
sudo systemctl restart survivalist-llm
```

### Port 53 conflict (dnsmasq fails to start)
```bash
# Disable systemd-resolved stub
sudo systemctl stop systemd-resolved
sudo sed -i 's/#DNSStubListener=yes/DNSStubListener=no/' /etc/systemd/resolved.conf
sudo systemctl start systemd-resolved
sudo systemctl restart dnsmasq
```

### Out of disk space
```bash
df -h /opt/survivalist-llm
# Remove unused models:
sudo docker exec survivalist-ollama ollama rm <model-name>
# Prune unused Docker layers:
sudo docker system prune -f
```

---

## Power & Resource Tips

- **Idle power:** The stack uses ~5–15W at idle on a mini-PC / NUC.
- **Inference power:** Spikes to 25–65W during active generation (CPU-only).
- **Auto-unload:** Ollama unloads the model from RAM 10 minutes after last use — freeing memory for the OS.
- **UPS:** Connect to a small UPS (uninterruptible power supply) so unexpected outages don't corrupt model files mid-write.
- **Screen off:** Run headless (no monitor) to save 5–20W.

---

## File Layout

```
/opt/survivalist-llm/
├── .env                    ← Secrets / config (chmod 600)
├── docker-compose.yml      ← Container definitions
├── detect_hardware.py      ← Hardware probe script
├── models/                 ← Ollama model files (bind-mounted)
├── webui-data/             ← Open WebUI chat history
├── config/
│   ├── hostapd.conf        ← WiFi AP template
│   ├── dnsmasq.conf        ← DHCP/DNS template
│   └── nginx.conf          ← Reverse proxy
├── scripts/
│   ├── ap-up.sh            ← Bring up WiFi AP
│   ├── ap-down.sh          ← Tear down WiFi AP
│   └── wait-healthy.sh     ← Health-check helper
└── logs/
    └── hostapd.log

/etc/hostapd/survivalist.conf   ← Active hostapd config
/etc/dnsmasq.d/survivalist.conf ← Active dnsmasq config
/etc/nginx/nginx.conf           ← Active nginx config
/etc/systemd/system/survivalist-llm.service
```

---

## Emergency Quick-Start Cheatsheet

Print this and keep it with the hardware.

```
POWER ON THE SERVER
  → wait ~90 seconds for boot

CONNECT PHONE/TABLET
  WiFi: SurvivalistLLM
  Pass: survival2025!

OPEN BROWSER
  → http://llm.local/
  → (backup) http://192.168.50.1/

IF THE UI DOESN'T LOAD (on the server):
  sudo systemctl restart survivalist-llm
  (then wait 60 seconds and try again)
```
