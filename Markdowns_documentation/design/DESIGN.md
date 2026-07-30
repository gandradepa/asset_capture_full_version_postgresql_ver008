# DESIGN.md — Asset Capture App Visual Tokens

> **Register**: Product (a tool, not a brand site). Design serves the user's job — capturing asset data fast — and supports the [PRODUCT.md](PRODUCT.md) personality (warm, calm, professional).
>
> **Color strategy** (per Impeccable): **Restrained warm** — tinted warm neutrals + preserved institutional blue as the anchor, plus one warm accent (amber) for earned moments (success, completed captures, recent scans). No drenched color, no full palette.

---

## Color Tokens

All colors expressed in OKLCH for theming reliability. Hex fallbacks given for tooling compatibility (existing CSS variables already use hex — migration is value-swap, not API change).

### Light mode

```css
/* ---- Surface (warm off-white, NOT clinical cool white) ---- */
--color-surface:           oklch(98.5% 0.005 80);   /* warm paper, ~#fbfaf7 */
--color-surface-elevated:  oklch(100% 0 0);          /* pure white for cards on warm surface */
--color-surface-sunken:    oklch(96% 0.008 75);     /* slight warm tint, ~#f4f1ec */

/* ---- Text (warm-leaning gray, NOT cool slate) ---- */
--color-text-primary:      oklch(22% 0.015 60);     /* near-black, slight warm undertone */
--color-text-secondary:    oklch(40% 0.012 65);     /* readable secondary */
--color-text-muted:        oklch(58% 0.010 70);     /* placeholder / helper */

/* ---- Border (warm-tinted, softer than slate) ---- */
--color-border-subtle:     oklch(92% 0.008 70);
--color-border-default:    oklch(85% 0.010 70);

/* ---- Primary (preserved institutional blue, slightly warmed toward indigo) ---- */
--color-primary:           oklch(52% 0.18 265);     /* ~#3b5fd9 — still trust-blue, less icy */
--color-primary-hover:     oklch(46% 0.19 265);
--color-primary-soft:      oklch(94% 0.04 265);     /* tint for backgrounds, focus rings */

/* ---- Warm accent (the friendliness signal — used sparingly, earned moments only) ---- */
--color-accent:            oklch(72% 0.13 65);      /* soft amber, ~#d99a4b */
--color-accent-hover:      oklch(65% 0.14 60);
--color-accent-soft:       oklch(95% 0.04 70);      /* tinted bg for accent surfaces */

/* ---- Success (warmer green, less cold) ---- */
--color-success:           oklch(58% 0.14 145);     /* sage-leaning, ~#4d9c5e */
--color-success-hover:     oklch(52% 0.15 145);
--color-success-soft:      oklch(94% 0.04 145);

/* ---- Warning (preserve current — yellow signals caution clearly) ---- */
--color-warning:           oklch(75% 0.15 85);      /* ~#d4a821 — slightly more orange */
--color-warning-soft:      oklch(95% 0.05 85);

/* ---- Danger (slightly warmer red — less alarming, still clear) ---- */
--color-danger:            oklch(58% 0.18 28);
```

### Dark mode (warmed dark palette, NOT pure black or cold blue-gray)

```css
--color-surface:           oklch(18% 0.008 75);     /* warm dark-gray, ~#1d1c1a */
--color-surface-elevated:  oklch(22% 0.010 70);     /* slightly lighter card face */
--color-surface-sunken:    oklch(15% 0.010 60);

--color-text-primary:      oklch(95% 0.008 75);
--color-text-secondary:    oklch(78% 0.010 70);
--color-text-muted:        oklch(60% 0.012 70);

--color-border-subtle:     oklch(28% 0.012 60);
--color-border-default:    oklch(35% 0.013 60);

--color-primary:           oklch(68% 0.16 265);     /* brighter for dark surface contrast */
--color-primary-soft:      oklch(28% 0.06 265);

--color-accent:            oklch(76% 0.14 65);
--color-accent-soft:       oklch(28% 0.05 70);

--color-success:           oklch(68% 0.14 145);
--color-success-soft:      oklch(28% 0.05 145);
```

**Rule**: Never `#000` or `#fff`. Even pure surfaces get a hint of warmth.

---

## Typography

System stack is already correct — leave it. Adjust hierarchy:

```css
--font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;

/* Scale — slightly more generous than current */
--font-size-xs:   0.75rem;   /* 12px — captions only */
--font-size-sm:   0.875rem;  /* 14px — labels, helper text */
--font-size-base: 1rem;      /* 16px — body, inputs (iOS no-zoom floor) */
--font-size-lg:   1.125rem;  /* 18px — section headings */
--font-size-xl:   1.375rem;  /* 22px — page headings (up from 20px) */
--font-size-2xl:  1.75rem;   /* 28px — hero on success page (up from 24px) */

/* Weights — soften labels from 600 to 500; reserve 600 for headings */
--font-weight-regular: 400;
--font-weight-medium:  500;   /* labels, button text */
--font-weight-semibold: 600;  /* headings, emphasis */

/* Line heights — more generous body */
--line-height-tight: 1.25;   /* headings */
--line-height-base:  1.55;   /* body (up from 1.5) */
--line-height-relaxed: 1.7;  /* helper text, long copy */
```

**Body measure**: cap form-helper paragraphs at ~60ch on wide screens.

---

## Spacing

Existing 5-step scale is solid for mobile. Add one larger step for breathing room between major sections.

```css
--space-xs:  0.5rem;   /*  8px */
--space-sm:  0.75rem;  /* 12px */
--space-md:  1rem;     /* 16px */
--space-lg:  1.5rem;   /* 24px */
--space-xl:  2rem;     /* 32px */
--space-2xl: 2.5rem;   /* 40px — NEW: section breaks, page padding-top */
```

**Vary spacing intentionally** (per Impeccable layout principles). Don't apply `--space-md` to everything — it produces monotony. Use:
- `--space-sm` between tightly-related items (label + input)
- `--space-lg` between distinct field groups
- `--space-2xl` between page sections / above the success hero

---

## Border Radius

Slightly softer overall — friendliness signal.

```css
--radius-sm: 0.5rem;   /*  8px — small chips, badges (up from 6px) */
--radius-md: 0.75rem;  /* 12px — inputs, buttons (up from 8px) */
--radius-lg: 1rem;     /* 16px — cards (up from 12px) */
--radius-pill: 9999px; /* avatars, status dots */
```

---

## Shadows

Warmer shadow tone — replace cold black-alpha with warm-gray-alpha for a less digital feel.

```css
--shadow-sm: 0 1px 2px 0 oklch(20% 0.02 60 / 0.06);
--shadow-md: 0 4px 12px -2px oklch(20% 0.02 60 / 0.08), 0 2px 4px -1px oklch(20% 0.02 60 / 0.04);
--shadow-lg: 0 12px 32px -8px oklch(20% 0.02 60 / 0.12);
```

Avoid the SaaS-default `0 4px 6px -1px rgb(0 0 0 / 0.1)` — it reads as cold.

---

## Motion

Keep current timing — don't slow the app down for field workers.

```css
--transition-fast:   150ms cubic-bezier(0.4, 0, 0.2, 1);
--transition-normal: 200ms cubic-bezier(0.4, 0, 0.2, 1);
--transition-slow:   300ms cubic-bezier(0.4, 0, 0.2, 1);  /* only for layout reveals */
```

**Rules**:
- Ease-out curves only. No bounce. No elastic.
- Motion conveys state change (capture → captured, idle → scanning) — never decorative.
- Respect `prefers-reduced-motion` — disable scanner line sweep, fade only.
- Max 250ms for in-flow interactions.

---

## Touch Targets

Already correct in the current CSS — preserve:

```css
--touch-target-min:          44px;  /* absolute minimum */
--touch-target-comfortable:  48px;  /* default for buttons, dropdowns */
--touch-target-large:        52px;  /* primary CTAs */
```

---

## Copy Voice

Not a CSS token but design IS copy. Replace system-voice labels with conversational helpers:

| Current (system voice) | Warm replacement |
|---|---|
| "Asset Setup" | "Let's find your asset" |
| "Capture Process" | "What are you doing today?" |
| "Building Code" | "Which building?" |
| "Location" | "Where in the building?" |
| "Asset Type" | "What kind of asset?" |
| "Submit" | "Save and continue" |
| "Submission successful" | "Saved. Nice work." |
| "Tap to take photo" | "Tap to snap" |
| "Manual QR Entry" | "Type the code instead" |
| "Verify" | "Check this code" |
| "Capture Another" | "Capture another" (already fine) |
| "Review Photos" | "Look at the photos" |

**Voice rules**:
- Address the user directly ("you", "your") — never third person
- Active verbs, not nouns ("Save", not "Submission")
- No exclamation points (avoids Duolingo register)
- No em dashes (per Impeccable house rules) — use commas, colons, or periods
- Helper text under inputs should answer a question the user might have, not restate the label
- Error messages explain what to do, not what went wrong: "Try a 10-digit code" not "Invalid format"

---

## Component Patterns

### Cards
- `--radius-lg` (16px), `--shadow-md`, `--color-surface-elevated` background
- Padding: `--space-lg` (24px) inside
- Never nested cards
- Vary card sizes — don't force uniform grid for variety's sake

### Buttons
- **Primary**: `--color-primary` bg, white text, `--radius-md`
- **Secondary**: transparent bg, `--color-text-primary` text, `--color-border-default` border
- **Accent (earned moments only)**: `--color-accent` bg — use rarely (success page CTA, completed-capture confirmation)
- Hover: lift by 1px, shadow steps up by one size
- All states defined: hover, focus, active, disabled, loading

### Inputs
- `--radius-md`, `--color-border-default` 1.5px border, `--color-surface-elevated` bg
- Focus: `--color-primary` border, `--focus-ring`
- Min height: `--touch-target-comfortable` (48px)
- Label above (`--font-size-sm`, `--font-weight-medium`)
- Helper text below, `--color-text-muted`, `--font-size-sm`

### Status indicators
- Pending: `--color-text-muted`, no fill
- Active: `--color-primary`, soft pulse animation
- Complete: `--color-accent` (NOT success-green — accent signals earned, complete signals done-with-warmth)

### Photo capture cards
- Variable density allowed: required photos visually heavier, optional photos lighter
- Empty state: dashed `--color-border-subtle` border, helpful copy ("Snap the asset plate")
- Captured state: filled card, thumbnail, replace/delete affordances

---

## Anti-patterns (per Impeccable, to avoid)

- Side-stripe colored left-borders on alerts (>1px) — use full borders or soft tinted backgrounds
- Gradient text (`background-clip: text`) — solid colors, hierarchy through weight
- Glassmorphism as default — only for the QR scanner overlay where it's purposeful
- Hero-metric template on success page — current "Asset Summary" is already past that, keep it human
- Identical card grid on capture page — vary required vs optional density
- Modal as first thought — current app already avoids; keep avoiding
- Decorative motion — every animation must convey a state change
- Display fonts in UI labels — system stack only
- Heavy color on inactive states — muted gray for pending/disabled

---

## Migration Notes

The current CSS uses hex tokens already namespaced under `--color-*`. Migration is a **token-value swap**, not a structural rewrite:

1. Update `:root` in [static/css/styles.css](static/css/styles.css) lines 28–96 with new OKLCH values
2. Update dark mode block at line 1847+ with warm dark palette
3. Add new tokens: `--color-surface`, `--color-surface-elevated`, `--color-surface-sunken`, `--color-accent*`, `--color-text-*`, `--color-border-*`, `--space-2xl`
4. Replace ad-hoc `#ffffff` / `#000000` / hardcoded grays in CSS with token references
5. Copy changes happen in [templates/](templates/) — Jinja templates, no logic changes
