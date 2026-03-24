# TODOS — SurvivorOS

Items deferred from CEO Review (2026-03-24) and design session.

---

## P1 — Pre-Build Gates (block Phase 1 implementation)

### Open WebUI API Spike
**What:** 2-hour validation spike on a live Open WebUI container. Confirm `POST /api/v1/configs/default/system-prompt` or equivalent REST endpoint exists and supports runtime prompt switching without a container restart. If the endpoint doesn't exist, confirm the bind-mounted config file fallback and measure restart latency (target: <5s restart acceptable for pack switching?).
**Why:** Pack switching is the core UX of the domain packs system. If Open WebUI requires a container restart on every pack switch, the UX changes significantly (5-10s delay vs instant). The API surface is not stable across Open WebUI minor versions.
**How to start:** Pin the Open WebUI version in docker-compose.yml first. Then: `docker run -d open-webui:VERSION`, hit the API with curl, log the result.
**Effort:** S (human: 2h / CC+gstack: 30min)
**Priority:** P1 — blocks pack system build
**Owner:** Dev

### Pack Switch Rollback Behavior
**What:** Define and implement the rollback behavior for mid-activation failures. When switching packs, the current flow is: (1) clear current system prompt via OW API, (2) load new pack's system prompt. If step 2 fails, the LLM currently has no system prompt and behaves as a generic unconfigured assistant.
**Why:** A user in an emergency situation (Elena with a broken chainsaw) who tries to switch packs and hits an API failure is now talking to a generic LLM with no context — worse than before. This is a data-loss-adjacent UX failure.
**Options:**
  - A) Re-activate previous pack on failure (restore prior prompt via OW API)
  - B) Define a baseline fallback prompt ("I am a general reference tool. No domain pack is active.") that prevents blank behavior
  - Recommend: Both — try A, fall back to B if OW API is unavailable.
**Effort:** S (human: 2h / CC+gstack: 20min)
**Priority:** P1 — implement before pack system ships
**Depends on:** Open WebUI API Spike (need to know if runtime prompt switching is available)

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

### SurvivorBox Hardware
**What:** Source Pi 5 8GB + IP54 enclosure + active cooler + USB-C power setup. Pre-flash SD cards. Fulfill as complete plug-in appliance at $149-179.
**Why:** Gary's tier. No terminal, no setup. The real long-term product.
**Blocks on:** 100+ SD card sales (demand signal), FCC/CE certification if WiFi AP is baked in.
**Priority:** P2 — after Phase 1 demand validated

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
- ~~System prompt injection approach~~ — RESOLVED: OW REST API with fallback; spike required
- ~~Emergency UI implementation~~ — RESOLVED: Option B (static wrapper HTML)

---

## Engineering Review Additions (2026-03-24)

### kiwix-serve SIGHUP Validation (bundle with OW API spike)
**What:** Verify that kiwix-serve v3.x reloads library.xml when it receives SIGHUP. If it does, Flask can send SIGHUP via Docker socket for zero-downtime library updates. If not, fall back to `docker restart survivalist-kiwix` (2-5s downtime acceptable).
**Why:** The plan calls for SIGHUP-based reload, but this is not confirmed. If kiwix-serve ignores SIGHUP, the Flask code using it will silently fail — pack installs succeed but the ZIM doesn't appear in Kiwix until the next restart.
**How to check:** `docker run ghcr.io/kiwix/kiwix-tools:latest kiwix-serve --help | grep -i signal` OR run kiwix-serve with a library, add an entry to library.xml, send SIGHUP, verify the new ZIM is served.
**Effort:** S (bundle with OW API spike — same 2-hour session)
**Priority:** P1 — determines Flask→kiwix reload implementation
**Depends on:** None — run before Flask admin build

### Image Version Maintenance Procedure
**What:** Define how image versions in docker-compose.yml get updated after initial pin. When OW or Ollama ships a security fix, buyers need a path to update. For Phase 1 (SD card), this means a new image SKU. For Phase 2 (SurvivorBox appliance), this needs a USB update mechanism.
**Why:** Floating tags were the original approach; we pinned for stability. Pinned versions can fall behind on security fixes.
**Options:** (a) New SD card image SKU for each significant update, (b) USB update script that pulls new images on a connected machine and copies layers, (c) OTA update endpoint for LAN-connected devices.
**Effort:** M (design decision first, then implementation) 
**Priority:** P3 — before Phase 2 SurvivorBox ships (buyers expect hardware to be maintainable)
**Depends on:** Phase 2 hardware sourcing decision

### Add kiwix-serve to docker-compose.yml (P1 Build Blocker)
**What:** Add kiwix-serve as a service in docker-compose.yml with: correct image pin, --library flag pointing to /opt/survivalist-llm/packs/library.xml, mem_limit: 512m, shared packs/ volume with Flask admin service.
**Why:** The entire pack system (ZIM serving, library routing, Kiwix browser at /library) depends on kiwix-serve. Without it in the compose file, nothing in the pack system can be built or tested.
**Effort:** S (human: 1h / CC: 10min)
**Priority:** P1 — blocks all pack system work
**Depends on:** None — do this before anything else

### Open WebUI Config Drift Protection
**What:** Flask admin service should validate that its expected config values are still present in Open WebUI on every startup (not just on first boot). If Open WebUI has reset its config (corrupt DB, version migration, container recreation), Flask should re-apply the configuration (model name, hidden selector, UI title) and log a warning.
**Why:** The sentinel file prevents re-running first-boot on normal restarts, but it doesn't protect against Open WebUI resetting its config. A buyer who upgrades their OW container would silently lose the SurvivorOS branding and have the raw model selector exposed.
**Implementation:** On every Flask start, after checking sentinel: call GET /api/v1/configs and compare expected values against actual. If mismatch, re-apply. Don't re-write sentinel (it's already there).
**Effort:** S (human: 2h / CC: 15min)
**Priority:** P2 — implement with Flask admin service build, before Phase 1 launch

### Main Chat Interface Legal Disclaimer
**What:** The main chat interface (/) needs a persistent, non-dismissible disclaimer visible before the user submits any query. Currently only the emergency UI has this. Attorney must review the main interface disclaimer separately from (or as part of) the emergency UI review.
**Why:** Users running medical triage queries ("is this heat stroke?") will primarily use the main chat interface, not the emergency UI. The legal protection must be on the main UI.
**Implementation:** Options: (a) nginx sub_filter injects a disclaimer banner above the chat UI on every page load, (b) Open WebUI's built-in "system prompt visible" feature displays it, (c) The attorney confirms the Open WebUI terms/policy modal on first access counts as sufficient.
**Effort:** S-M depending on approach (confirm with attorney first)
**Priority:** P1 — required before taking money. Same gate as ToS review.
**Depends on:** Attorney engagement (Reviewer Concern #1)
