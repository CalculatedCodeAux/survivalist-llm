# Design System — SurvivorOS

## Product Context
- **What this is:** An offline AI assistant + library browser for survival and off-grid scenarios
- **Who it's for:** Weekend RVers, campers, and people spending extended time off-grid
- **Space/industry:** Emergency preparedness / outdoor tech
- **Project type:** Offline-first web app (chat, offline library, admin panel)

## Aesthetic Direction
- **Direction:** Field Guide / Trailhead — outdoor equipment manual meets aviation checklist
- **Decoration level:** Minimal — clarity and legibility over decoration
- **Mood:** Trustworthy and functional. Feels like a well-worn field guide: authoritative, direct, zero fluff. High contrast because it may be read in bright outdoor sunlight or under stress.

## Typography
- **Display/Nav:** Cabinet Grotesk — strong geometric sans with outdoor-equipment energy; not generic tech
- **Body:** Plus Jakarta Sans — high x-height, clear at small sizes on phone screens
- **UI/Labels:** Same as body (Plus Jakarta Sans)
- **Data/Tables:** Geist Mono — fixed-width for coordinates, measurements, quantities
- **Code:** Geist Mono
- **Loading:** Google Fonts CDN with `system-ui` fallback — app functions fully when offline
- **Scale:** 12 / 13 / 14 / 17 / 20 / 24 / 32px

## Color
- **Approach:** Restrained — one strong accent, neutrals do the work
- **Background:** `#F7F5F0` — warm white; reduces glare, warmer than clinical white
- **Surface:** `#FFFFFF` — cards and elevated content
- **Primary text:** `#1C1A17` — near-black with slight warmth
- **Muted text:** `#6B6456` — warm medium gray for secondary info
- **Border:** `#E0DDDA` — subtle warm gray
- **Accent / Primary:** `#D4570A` — burnt orange; high-visibility outdoor-tool color
- **Success:** `#2D6A4F` — forest green
- **Warning:** `#B45309` — amber
- **Error:** `#C0392B` — red
- **Dark mode background:** `#181714` / surfaces `#222018`
- **Dark mode text:** `#F7F5F0`

## Spacing
- **Base unit:** 4px
- **Density:** Comfortable — touch targets ≥ 44px, breathing room between elements
- **Scale:** 2xs(2) xs(4) sm(8) md(16) lg(24) xl(32) 2xl(48) 3xl(64)

## Layout
- **Approach:** Grid-disciplined — structured, predictable; admin uses max-width cards
- **Max content width:** 680px (admin), full-viewport (chat, library, emergency)
- **Border radius:** buttons:6px, cards:10px, badges:3-4px, inputs:6-8px

## Motion
- **Approach:** Minimal-functional — no decorative animation
- **Easing:** enter(ease-out) exit(ease-in) move(ease-in-out)
- **Duration:** micro(50ms) short(150ms) medium(250ms)

## Chrome Injection (nginx)
The nginx proxy injects SurvivorOS nav and disclaimer into **Open WebUI pages only**
(scoped to `location /`). Admin and emergency pages include their own matching chrome.

- **Top nav:** 44px fixed bar, `#1C1A17` bg, `#D4570A` left border accent, Chat/Library/Admin links
- **Bottom disclaimer:** 52px fixed bar — attorney-reviewed text, `border-top: 3px solid #c00`
- **CSS fix:** `#chat-container { padding-top: 44px }` and `#chat-pane { padding-bottom: 52px }`
  prevent the chat input from being covered by the injected bars

### Disclaimer copy (v1.0 — attorney-reviewed 2026-03-24)
> **⚠ NOT MEDICAL OR SAFETY ADVICE.** AI can be wrong or dangerous. Verify anything critical.
> Emergency? **Call 911.**

Do not change this copy without new legal sign-off.

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-25 | Initial design system | Created for weekend RVers and off-grid users; outdoor legibility is the primary constraint |
| 2026-03-25 | Warm white `#F7F5F0` | Reduces glare in outdoor sunlight; warmer than clinical white |
| 2026-03-25 | Burnt orange `#D4570A` accent | High-visibility outdoor-tool color; strong contrast on both light and dark |
| 2026-03-25 | sub_filter scoped to `/` only | Admin and emergency include own nav; prevents double-injection |
| 2026-03-25 | System font fallback | Device is offline; CDN fonts load only when internet is available |
| 2026-03-25 | Fixed disclaimer overlap fix | `#chat-pane` padding-bottom stops Open WebUI's chat input from sliding under the disclaimer bar |
