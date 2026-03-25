# TODOS — SurvivorOS

Items deferred from CEO Review (2026-03-24) and design session.

---

## P1 — Pre-Build Gates (block Phase 1 implementation)

### ~~Open WebUI API Spike~~ — RESOLVED (2026-03-24)
**Result:** No global system prompt endpoint exists. Correct approach is per-model custom entries.
**Architecture decision:**
- Flask admin creates one OW custom model per domain pack via `POST /api/v1/models/model/update`
- Each pack gets a custom model entry with `params.system` set to the pack's system prompt
- Pack activation = update that model's `params.system` (no container restart, no prompt clearing)
- Users select their active pack by choosing the model in the OW UI
- First-boot: Flask generates an admin API key and stores it at `/opt/survivalist-llm/.ow-api-key`
- Rollback is now trivially safe: each pack is a separate model entry; activation = model switch
- Relevant endpoints: `POST /api/v1/models/create`, `POST /api/v1/models/model/update`
- Requires: OW admin API key (Bearer token) — generated once at first-boot via OW admin account
**Unblocked:** Pack Switch Rollback (below) is now resolved by design — no "clear then set" race.

### ~~Pack Switch Rollback Behavior~~ — RESOLVED (2026-03-24)
**Result:** Per-model architecture eliminates the rollback problem entirely.
**Why resolved:** Original concern was a two-step "clear then set" race where step 2 failure left no active prompt. With per-model entries, activation is a single atomic model-selection write — there is no intermediate blank state. The previous pack model entry is never touched during activation of a new one. A baseline "no pack active" model entry (base model, no system prompt override) covers the deactivation case.

---

## P2 — Phase 2 Feature Work

### Voice Input (Whisper.cpp)
**What:** Add whisper.cpp Docker container to Compose stack. Voice input via browser microphone → transcription → Ollama query. Show transcribed text before sending so user can correct errors.
**Why:** Elena with greasy hands. Noise environments are the primary concern — fire camp, RV, outdoor use. Validate demand first: do Phase 1 buyers ask for it in support emails?
**Deferred because:** Noise environments degrade quality significantly; Pi 5 latency for Whisper.cpp transcription needs benchmarking on actual hardware before committing to UX.
**Blocks on:** Noise environment testing, Pi 5 latency benchmarking, demand validation from Phase 1 support emails.
**Effort:** M (human: 1 week / CC+gstack: 1-2h)
**Priority:** P2 — Phase 2 / SurvivorBox v2

### Offline Maps (OSM tile server)
**What:** Add tileserver-gl container + regional MBTiles file (~8GB for US coverage). Nginx route at `/maps`. Browse-only (no routing). 128GB SD card required.
**Why:** RV campers on BLM land would benefit from offline maps. But this doubles image size and the use case is browse-only — Gaia GPS and CalTopo already serve this market.
**Deferred because:** Doubles image size from ~20GB to ~28GB base; requires 128GB card ($15-20 more than 64GB). Maps use case is served by existing apps. Validate demand first.
**Blocks on:** Demand validation for maps use case (Phase 1 support emails / community requests).
**Effort:** L (human: 1-2 weeks / CC+gstack: 2-3h + MBTiles sourcing)
**Priority:** P2 — Phase 2 / SurvivorBox 128GB model

### Pack Content — Wildfire Pack
**What:** Create the first domain knowledge pack. ZIM file sourced from Kiwix (wildland fire, chainsaw repair, field safety, incident management reference content). System prompt tuned for fire crew context.
**Why:** Elena's primary use case. This is the pack that validates the pack system.
**Blocks on:** Pack system infrastructure (Phase 1), SME review by a wildland firefighter or fire behavior analyst before sale.
**Effort:** L (human: 2-3 weeks content + review / CC+gstack: content assembly is human-led)
**Priority:** P2 — after Phase 1 demand validated

### Pack Content — Medical Pack
**What:** Create a medical domain knowledge pack. ZIM file from Kiwix (Wikipedia Medicine, first aid, field triage reference content). System prompt tuned for field medicine context.
**Why:** The highest-stakes use case and the highest-liability use case. Medical pack must not be sold without qualified review.
**Blocks on:** Pack system infrastructure (Phase 1), review by a qualified medical professional before sale. Do NOT sell without SME sign-off.
**Effort:** L (human: 3-4 weeks content + review / CC+gstack: content assembly is human-led)
**Priority:** P2 — after Phase 1 demand validated

### ZIM File Size Validation
**What:** Before publishing "up to 10 packs" in any marketing materials, validate actual ZIM file sizes for the Wildfire and Medical domains against Kiwix's ZIM library. The CEO plan estimates 1-3GB per pack — real Medical/Wildfire ZIMs may be 5-15GB each, which changes the storage story and the "10 packs" claim.
**Why:** "Up to 10 packs" is a marketing claim. If packs are actually 8GB each, 10 packs = 80GB which doesn't fit on a 64GB card. Publishing wrong storage numbers erodes trust when buyers discover the reality.
**How to check:** Browse kiwix.org/en/library, filter by category (Medicine, Environment/Nature). Note file sizes. Cross-reference against the 39GB available on a 64GB card.
**Effort:** S (human: 30min / CC+gstack: 10min)
**Priority:** P2 — before any marketing copy mentions pack counts
**Depends on:** None — can do immediately

### Pack Distribution — Gumroad Listings
**What:** Create Gumroad product listings for each domain pack. Digital download (.survivorpack zip). Pricing: $15-25 per pack.
**Why:** Revenue. Pack revenue is the Phase 2 business model.
**Blocks on:** Pack content completion (Wildfire + Medical), Gumroad account setup.
**Priority:** P2 — after pack content is done

### SurvivorBox Hardware — Two Tiers

#### SurvivorBox Pi (Entry)
**What:** Raspberry Pi 5 8GB + enclosure + active cooler + USB-C power. Pre-flashed. Plug-and-go.
**Model:** Qwen 2.5 3B Q4 — forced by detect_hardware.py ARM tier cap (~6 t/s, 20-35s typical response)
**Price target:** ~$179 retail
**Why:** Entry price point. Works well for camping — you're not in a hurry.
**Blocks on:** Enclosure sourcing, FCC/CE cert if WiFi AP is baked in.
**Priority:** P2 — after Phase 1 demand validated

#### SurvivorBox Standard (N100)
**What:** Intel N100 mini PC (Beelink EQ12 or equivalent, 8GB+ RAM). Pre-flashed. Plug-and-go via 12V/19V barrel. Include adapter in box — most N100 units don't support USB-C PD.
**Model:** Qwen 2.5 3B Q4 (~20 t/s, 5-10s typical response)
**Price target:** ~$249 retail
**Why:** Recommended tier. Fast enough to feel instant for reference Q&A. x86 = existing Docker stack, no porting.
**Validation gate:** Benchmark Qwen 2.5 3B on N100 — median first-token must be <5s before ordering inventory.
**Priority:** P2 — after benchmark validation

#### Marketing Site
**What:** Static site at `marketing/index.html` — two-tier comparison with live speed demo animation.
**Status:** Built. Speed constants (PI_TTFT_MS, PI_TPS, N100_TTFT_MS, N100_TPS) are estimates — update after real benchmark.
**Next steps:** Run benchmark → update JS constants → deploy to static host (GitHub Pages / Netlify / Cloudflare Pages).
**Priority:** P2 — update constants after benchmark, deploy before any external marketing push

---

## P3 — Phase 3 / Future

### Community Pack Submissions
**What:** System for community members to submit `.survivorpack` files. Curation process, quality gate, Gumroad listing assistance.
**Why:** Flywheel. David's contribution layer. Marine, Mechanical, Amateur Radio packs.
**Priority:** P3

### FCC/CE Certification (SurvivorBox)
**What:** Budget $5-15K and 3-6 months for RF testing and certification. Required for any device that broadcasts WiFi (which SurvivorBox does as an AP).
**Why:** Legal requirement for hardware sales. Phase 1 (SD card image) is exempt — Pi 5 is already certified.
**Priority:** P3 — before SurvivorBox sales begin

---

## Resolved / Done

- ~~Pack install method~~ — RESOLVED: File-upload only (USB/local web upload)
- ~~System prompt injection approach~~ — RESOLVED: Per-model OW custom entries via `POST /api/v1/models/model/update` with `params.system`; no global endpoint exists
- ~~Emergency UI implementation~~ — RESOLVED: Option B (static wrapper HTML / iframe)
- ~~Open WebUI API Spike~~ — RESOLVED: Per-model approach; no "clear then set" race; rollback problem eliminated by design
- ~~Pack Switch Rollback Behavior~~ — RESOLVED: Per-model architecture makes this a non-issue
- ~~kiwix-serve SIGHUP Validation~~ — RESOLVED: Use `--monitorLibrary` flag; SIGHUP confirmed as fallback; no Docker socket needed
- ~~First-Boot OW API Integration Bugs~~ — RESOLVED: JWT signin via `POST /api/v1/auths/signin`; `_ow_request` helper with auto-refresh; config via env vars (no `/api/v1/configs/`); model create-first with 401="already registered" detection; `?id=` query param on update

---

## Engineering Review Additions (2026-03-24)

### ~~kiwix-serve SIGHUP Validation~~ — RESOLVED (2026-03-24)
**Result:** SIGHUP confirmed working in kiwix-tools ≥ 3.2.0. Better approach: use `--monitorLibrary` flag.
**Architecture decision:**
- Run kiwix-serve with `--library /data/library.xml --monitorLibrary` (flag `-M`)
- Flask pack installer writes updated `library.xml` atomically (tmp + rename)
- kiwix-serve detects file mtime change and reloads automatically within ~1s
- No Docker socket access needed — simpler Flask implementation
- SIGHUP (`docker kill --signal=SIGHUP survivalist-kiwix`) works as manual override if needed
- Must pin kiwix-tools image to ≥ 3.2.0 (SIGHUP + --monitorLibrary introduced in 3.2.0)
**Unblocked:** Add kiwix-serve to docker-compose.yml (below).

### Image Version Maintenance Procedure
**What:** Define how image versions in docker-compose.yml get updated after initial pin. When OW or Ollama ships a security fix, buyers need a path to update. For Phase 1 (SD card), this means a new image SKU. For Phase 2 (SurvivorBox appliance), this needs a USB update mechanism.
**Why:** Floating tags were the original approach; we pinned for stability. Pinned versions can fall behind on security fixes.
**Options:** (a) New SD card image SKU for each significant update, (b) USB update script that pulls new images on a connected machine and copies layers, (c) OTA update endpoint for LAN-connected devices.
**Effort:** M (design decision first, then implementation) 
**Priority:** P3 — before Phase 2 SurvivorBox ships (buyers expect hardware to be maintainable)
**Depends on:** Phase 2 hardware sourcing decision

### ~~Add kiwix-serve to docker-compose.yml~~ — RESOLVED (2026-03-24)
**Result:** kiwix-serve added to docker-compose.yml as `survivalist-kiwix` service. Pinned to `ghcr.io/kiwix/kiwix-tools:3.8.2`, running with `--library /data/library.xml --monitorLibrary` flags, 512m mem_limit, shared packs/ volume with admin service.

### Open WebUI Config Drift Protection
**What:** Flask admin service should validate that its expected config values are still present in Open WebUI on every startup (not just on first boot). If Open WebUI has reset its config (corrupt DB, version migration, container recreation), Flask should re-apply the configuration (model name, hidden selector, UI title) and log a warning.
**Why:** The sentinel file prevents re-running first-boot on normal restarts, but it doesn't protect against Open WebUI resetting its config. A buyer who upgrades their OW container would silently lose the SurvivorOS branding and have the raw model selector exposed.
**Implementation:** On every Flask start, after checking sentinel: call GET /api/v1/configs and compare expected values against actual. If mismatch, re-apply. Don't re-write sentinel (it's already there).
**Effort:** S (human: 2h / CC: 15min)
**Priority:** P2 — implement with Flask admin service build, before Phase 1 launch

### ~~First-Boot OW API Integration Bugs~~ — RESOLVED (2026-03-24)
**Result:** Fixed in `survivorpack-admin/app.py`. All 54 tests pass.
**Architecture decisions:**
- Replaced `_get_ow_api_key` / `OW_API_KEY_FILE` with JWT-based auth via `_ow_signin()` + `_ow_jwt_cache`
- Added `_ow_request(method, path, **kwargs)` helper that auto-refreshes JWT on 401 (token expiry, not "already registered")
- `_apply_ow_config()` is now a no-op (all OW config via docker-compose env vars; `POST /api/v1/configs/` returns 405 in OW 0.8.10)
- `_check_config_drift()` now checks `GET /api/v1/models` for model existence rather than `GET /api/v1/configs/` (which returns empty body in OW 0.8.10)
- `_create_or_update_ow_model()` fixed: try create first; OW returns 401 "already registered" for duplicates (not 400/409); update requires `?id=<model_id>` query param AND `id` in body; added `"capabilities": {}` to `meta` field
- `EXPECTED_OW_CONFIG` dict removed (dead code — config is env-var driven)

**Also found — healthcheck `curl` not in images:** Fixed in docker-compose.yml (2026-03-24):
- `ollama/ollama` → `CMD ollama list`
- `ghcr.io/kiwix/kiwix-tools` → `CMD wget -qO/dev/null`
- Python images → `CMD-SHELL python3 -c urllib`

### ~~Main Chat Interface Legal Disclaimer~~ — RESOLVED (2026-03-24)
**Result:** nginx `sub_filter` injects a persistent non-dismissible disclaimer bar into every Open WebUI page. Emergency UI also has an inline disclaimer. Attorney review of disclaimer copy still required before launch.
