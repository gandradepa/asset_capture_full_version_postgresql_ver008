# PRODUCT.md — Asset Capture App

## Users

UBC Facilities field workers. They:
- Work outdoors, often in poor light or weather, often wearing gloves
- Are usually walking between buildings carrying equipment, in a hurry
- Use a personal or shared mobile phone (iPhone or Android, often older hardware)
- May have spotty connectivity (basements, mechanical rooms)
- Repeat the same workflow dozens of times per shift — speed and predictability matter more than novelty
- Range from non-technical maintenance staff to experienced trades — the app must be approachable to both

## Purpose

Capture asset photos and metadata accurately and quickly so the central facilities database stays current. The app exists to **reduce data-entry friction**, not to gamify or instruct.

Core flow: **Start** (identify asset via QR scan or manual entry, pick building/location/type) → **Capture** (take the required photos for the asset type) → **Success** (confirm save, capture another or stop).

## Personality

**Warm, calm, professional.** The app should feel like a helpful coworker, not a corporate intake form and not a consumer game.

- **Warm**: Conversational copy, off-white surfaces (not clinical cool white), softened color palette
- **Calm**: Generous spacing, no urgent notification spam, gentle motion (no bounces, no confetti)
- **Professional**: Still trustworthy for institutional data capture — restrained, accurate, legible. Personality through small touches, not loud styling

The user should feel *welcomed and supported*, never *processed* or *condescended to*.

## Anti-references (what this app must NOT feel like)

- **Duolingo / consumer game**: No streaks, no celebration animations, no mascot, no encouragement copy ("You did it! 🎉")
- **Salesforce / enterprise SaaS sterility**: No dense data grids, no cold blue/white-only palette, no jargon-heavy labels ("Asset Setup", "Capture Process")
- **Material Design generic**: No FAB, no ripple effects on everything, no shadowed cards floating with no reason
- **Brutalist / experimental**: No raw inputs, no minimal-to-the-point-of-confusing UI — field workers don't have time to decode design
- **Government form**: No dense field grouping, no required-field-asterisk-soup, no walls of fine print

## Constraints

- **UBC Facilities branding**: Logo and "UBC Facilities" wordmark must remain prominent in the header. Blue identity should be preserved (institutional trust signal) but can be warmed.
- **Mobile-first**: 375px viewport is the design target. Tablet/desktop are secondary.
- **Accessibility**: WCAG AA contrast, ≥44px touch targets, screen reader support, keyboard nav. Currently meets this; redesign must not regress.
- **Performance**: No heavy fonts, no large image assets — workers may be on slow connections.
- **Dark mode**: Currently supported via `prefers-color-scheme` + manual `.dark-mode` class. Must remain functional after warm-palette shift.
- **No SPA frameworks**: Flask + Jinja + vanilla JS only. No React/Vue/Svelte introduction.

## Success criteria (what "better" looks like)

After the redesign:
1. A new field worker can complete one full capture flow without asking what a field means
2. The success page makes the user feel they did meaningful work, not that they fed a database
3. The app no longer reads as "internal IT tool" on first impression — it reads as "tool built *for* me"
4. All accessibility and performance baselines preserved
5. Existing functionality (QR scanning, photo upload, offline detection, recent scans, parameter change detection) untouched
