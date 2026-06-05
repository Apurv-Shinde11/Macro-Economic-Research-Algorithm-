---
name: EconIq · Sentinel
description: India's daily macro intelligence terminal for investment advisors, family offices, and institutional clients.
colors:
  midnight-vault: "#04060f"
  surface-deep: "#080d1c"
  surface-panel: "#0d1428"
  surface-raised: "#111d38"
  signal-blue: "#4f83ff"
  signal-blue-light: "#7aa3ff"
  signal-indigo: "#6366f1"
  signal-violet: "#8b5cf6"
  signal-green: "#10d48a"
  signal-amber: "#fbbf24"
  signal-red: "#f87171"
  signal-gold: "#f5c542"
  signal-cyan: "#22d3ee"
  text-primary: "#e8eeff"
  text-secondary: "#7a8baa"
  text-ghost: "#2e3d58"
  light-bg: "#f4f6fc"
  light-surface: "#ffffff"
  light-panel: "#eaf0fb"
typography:
  display:
    fontFamily: "Cormorant Garamond, Georgia, serif"
    fontSize: "clamp(38px, 4.2vw, 60px)"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "-0.01em"
  hero:
    fontFamily: "Cormorant Garamond, Georgia, serif"
    fontSize: "clamp(58px, 6vw, 86px)"
    fontWeight: 600
    lineHeight: 1.0
    letterSpacing: "-0.02em"
  body:
    fontFamily: "DM Sans, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "JetBrains Mono, monospace"
    fontSize: "10px"
    fontWeight: 500
    letterSpacing: "0.14em"
  data:
    fontFamily: "JetBrains Mono, monospace"
    fontSize: "13px"
    fontWeight: 600
    letterSpacing: "0.06em"
rounded:
  xs: "4px"
  sm: "6px"
  md: "8px"
  lg: "14px"
  full: "9999px"
spacing:
  xs: "6px"
  sm: "10px"
  md: "16px"
  lg: "24px"
  xl: "36px"
  section: "108px"
components:
  button-primary:
    backgroundColor: "{colors.signal-blue}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "12px 28px"
  button-primary-hover:
    backgroundColor: "{colors.signal-blue-light}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "12px 28px"
  button-outline:
    backgroundColor: "transparent"
    textColor: "{colors.text-secondary}"
    rounded: "{rounded.md}"
    padding: "12px 28px"
  chip:
    backgroundColor: "{colors.surface-panel}"
    textColor: "{colors.text-secondary}"
    rounded: "{rounded.xs}"
    padding: "4px 10px"
  card:
    backgroundColor: "{colors.surface-deep}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.lg}"
    padding: "30px 26px"
  nav-link:
    backgroundColor: "transparent"
    textColor: "{colors.text-secondary}"
    rounded: "{rounded.sm}"
    padding: "6px 10px"
  nav-link-active:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.sm}"
    padding: "6px 10px"
---

# Design System: EconIq · Sentinel

## 1. Overview

**Creative North Star: "The Institutional Terminal"**

EconIq is a precision instrument for professionals who read data for a living. The interface behaves like a sealed intelligence room: dark, focused, and organized. Every element has a specific job. Nothing decorates; everything informs. The visual density is controlled — data-rich without feeling cluttered — because the audience are advisors who need a defensible answer in 30 seconds, not analysts who enjoy data exploration for its own sake.

The palette is a near-black navy anchored in Midnight Vault (`#04060f`) with a blue-tinted depth progression across four surface levels. Color appears only when it carries signal: green for positive regime conditions, amber for caution, red for stress, blue for primary actions and active state. Monospace type (JetBrains Mono) handles all live data values, percentages, labels, and regime codes — visually separating "machine output" from "human narrative." Cormorant Garamond serif provides institutional weight for headings; DM Sans delivers clean body copy at small sizes.

The system explicitly rejects: the consumer fintech aesthetic (colorful, gamified, progress-bar-heavy), the generic AI-SaaS cream aesthetic (warm near-white backgrounds, purple-gradient CTAs, identical card grids), news/media noise (no editorial hierarchy, information overload), and the dense legacy terminal look (orange-on-black, no whitespace, nothing legible on a first pass). This is not Bloomberg. It is a concise daily brief for advisors who trust the engine.

**Key Characteristics:**
- Dark-first, data-forward: dark mode is canonical; light mode mirrors the same token structure
- Two-font system: serif for authority, mono for data, sans for clarity — never crossed
- Signal colors carry information, never decoration
- Monochrome-adjacent surfaces with controlled accent saturation
- Institutional tone: no marketing flair in the product UI, no consumer patterns in either surface

## 2. Colors: The Midnight Vault Palette

A near-black navy foundation with exactly one primary accent (Signal Blue) and four semantic signal colors that activate only when data warrants them.

### Primary
- **Signal Blue** (`#4f83ff`): Primary action color. CTAs, active nav states, confidence bars, focus rings, regime engine accent. Never used for decoration.
- **Signal Blue (Light)** (`#7aa3ff`): Data values, hover targets, gradient partner to Signal Blue. Carries numerical data in the terminal view.

### Secondary
- **Signal Indigo** (`#6366f1`): Gradient partner to Signal Blue on display headings and hero glow effects. Not used standalone; only in multi-stop gradients.
- **Signal Violet** (`#8b5cf6`): Terminal/display gradient endpoint. Never used on body text or functional elements.

### Tertiary
- **Signal Gold** (`#f5c542`): Premium tier indicator. "Professional" badge, special callouts. One instance per view maximum.
- **Signal Cyan** (`#22d3ee`): Rare highlight. Live data feed indicator. Reserved for explicit "live/streaming" signals.

### Neutral
- **Midnight Vault** (`#04060f`): Body background. The deepest surface; everything floats above it.
- **Surface Deep** (`#080d1c`): Primary card background. First layer above Midnight Vault.
- **Surface Panel** (`#0d1428`): Secondary surface; sidebar, nested panels, modal backgrounds.
- **Surface Raised** (`#111d38`): Hover states, active/selected card states, elevated elements.
- **Text Primary** (`#e8eeff`): Body text, headings, data values. Blue-tinted near-white; never pure white.
- **Text Secondary** (`#7a8baa`): Labels, descriptions, secondary copy. Blue-gray, not neutral gray.
- **Text Ghost** (`#2e3d58`): Placeholder text, disabled states, inactive labels. Must not be used for readable body copy — fails 4.5:1 against most surfaces.

### Semantic signal colors
- **Signal Green** (`#10d48a`): Positive regime / RISK_ON / overweight equity. Always additive signal.
- **Signal Amber** (`#fbbf24`): Caution / watchlist / RBI pause. Neutral-to-watchful signal.
- **Signal Red** (`#f87171`): Stress / RISK_OFF / negative flow. Never used for branding, only for data-driven state.

### Light mode
Light mode inverts to a blue-tinted near-white (`#f4f6fc`) with the same token names at different values. Signal colors shift to their saturated equivalents (green `#059669`, blue `#2563eb`). Both modes must meet the same contrast standards — verify monospace label text (`text-ghost` on `light-panel`) specifically, as it is the highest-risk surface.

### Named Rules
**The Signal Color Rule.** Green, amber, red, and gold appear only when a data value warrants them. Never use these colors for decoration, background tints on non-signal elements, or marketing emphasis. A page element carrying Signal Green is making a claim about market conditions.

**The Blue Discipline Rule.** Signal Blue (`#4f83ff`) is the only accent on any given screen. Indigo and violet exist only inside gradient expressions (hero headlines, glow effects). On product surfaces, no gradient text, no violet standalone usage.

## 3. Typography: The Investment Research Report

**Display Font:** Cormorant Garamond (Georgia, serif fallback)
**Body Font:** DM Sans (system sans-serif fallback)
**Data / Label Font:** JetBrains Mono (monospace fallback)

**Character:** The pairing reads like a research note written by a senior analyst: Cormorant Garamond provides editorial authority in headings and section leads; DM Sans delivers information with operational clarity in body copy; JetBrains Mono handles every machine-generated value — regime names, confidence percentages, dates, tickers — creating a visible separation between human narrative and data output. Three families maximum; no substitution.

### Hierarchy
- **Hero** (Cormorant Garamond, 600, `clamp(58px, 6vw, 86px)`, lh 1.0, ls -0.02em): Landing page hero headlines only. Contains italic em emphasis for key phrases. Hard ceiling: 86px — no larger.
- **Display** (Cormorant Garamond, 600, `clamp(38px, 4.2vw, 60px)`, lh 1.1, ls -0.01em): Section headings on both brand and product surfaces. Apply `text-wrap: balance`.
- **Title** (Cormorant Garamond, 600, 24–28px, lh 1.2): Card headings, feature titles, regime name in the dashboard hero.
- **Body** (DM Sans, 400, 14–17px, lh 1.6–1.75): All descriptive copy. Cap at 65–75ch for readability. 400 weight at 14px on dark surfaces; bump to 500 if the container background is darker than Surface Deep.
- **Label** (JetBrains Mono, 500, 9–11px, ls 0.14–0.22em, uppercase): Section eyebrows, data field labels, category identifiers. Uppercase is permitted here and only here.
- **Data** (JetBrains Mono, 600, 13–15px, ls 0.06em): Live values — confidence percentages, index prices, regime codes, flow numbers. 500 weight for secondary data cells.

### Named Rules
**The Mono-Means-Machine Rule.** JetBrains Mono is reserved for data output and labels — items the engine generates. Narrative copy (descriptions, explanations, advice) is always DM Sans. Mixing mono into body sentences signals system malfunction, not style.

**The Serif Ceiling Rule.** Cormorant Garamond headings stop at 86px (`clamp` max). Above that the page is announcing, not informing. Hero heading italic (`em`) emphasis carries weight through style, not scale.

## 4. Elevation

This system uses a hybrid approach: tonal layering is the primary depth signal; shadow is reserved for floating elements and interactive state changes. There is no ambient shadow on static cards — surfaces differentiate through background progression (Midnight Vault → Surface Deep → Surface Panel → Surface Raised) and a 1px blue-tinted border (`rgba(99,132,255,0.10)`) rather than drop shadows.

Blur and backdrop-filter are used on the nav only (not on cards), creating a single elevated surface at the top of the z-stack.

### Shadow Vocabulary
- **Card resting** (`--card-shadow: 0 4px 24px rgba(0,0,0,0.4), 0 0 0 1px rgba(79,131,255,0.06)`): Applied to primary dashboard cards. The 1px blue-tinted ring is the visible indicator; the drop shadow provides physical depth.
- **Nav floating** (`0 1px 0 rgba(79,131,255,0.12), 0 8px 32px rgba(0,0,0,0.4)` + `backdrop-filter: blur(24px)`): Nav bar only when scrolled. The blur communicates elevation through obscuring the content below, not through shadow size.
- **Button hover** (`0 4px 16px rgba(79,131,255,0.30)`, elevated on hover to `0 8px 26px rgba(79,131,255,0.45)`): Interactive element lift. Color-matched to Signal Blue so the glow reads as the button's own light source.
- **Input focus** (`0 0 0 3px rgba(79,131,255,0.10)`): Focus ring expressed as a soft glow, not a hard outline.
- **Signal dot glow** (`0 0 6px var(--green)` / `0 0 8px var(--green)`): Live indicator pulse. Semantic green only.

### Named Rules
**The No-Ambient-Shadow Rule.** Static surfaces carry no drop shadow. Depth is communicated through the background progression and the 1px border ring. Shadows appear only in response to state (hover, floating, focus) or to separate overlapping surfaces (nav, modal, toast).

## 5. Components

### Buttons
- **Shape:** Gently curved (8px radius — `--radius`)
- **Primary:** Signal Blue background (`#4f83ff`), white text, DM Sans 500, padding `12px 28px`. On hover: lifts 2px (`translateY(-2px)`), shadow intensifies to `0 8px 26px rgba(79,131,255,0.45)`, background brightens to `#7aa3ff`.
- **Focus:** `0 0 0 3px rgba(79,131,255,0.10)` ring. Visible without color dependency (also uses a slight surface change).
- **Outline/Ghost:** Transparent background, `text-secondary` color, `1px solid rgba(79,131,255,0.20)` border. On hover: `text-primary` color, border brightens to Signal Blue, `rgba(37,99,235,0.06)` background tint.
- **Transition:** `opacity 0.2s, transform 0.18s, box-shadow 0.2s` — keep all three together for coherent lift.

### Chips / Pills
- **Style:** `surface-panel` background, `text-secondary` text, `xs` radius (4px), `4px 10px` padding.
- **Font:** JetBrains Mono, 10px, ls 0.06em. Chips are always data-labeling elements; mono is appropriate.
- **Border:** `1px solid rgba(99,132,255,0.20)` at rest. Brightens on hover.
- **Regime/signal chips:** When a chip indicates a regime or signal category, it receives the semantic color (green/amber/red) as a `background-dim` fill (`rgba(color, 0.10)`) and matching text color.

### Cards
- **Corner style:** `lg` radius (14px) for primary cards; `md` (8px) for nested elements and compact cards.
- **Background:** `surface-deep` (`#080d1c`) at rest. Never pure black; never transparent on dark backgrounds.
- **Shadow:** `--card-shadow` (see Elevation). Applied on all primary dashboard cards.
- **Border:** `1px solid rgba(99,132,255,0.10)`. The ring is the card boundary; the shadow is secondary depth.
- **Internal padding:** `30px 26px` standard. `40px 34px` for feature/marketing cards. `22px` for tight data panels.
- **Hover:** Background shifts to `surface-raised` (`#111d38`). No border animation, no shadow jump — subtle acknowledgment, not a theatric change.
- **Nested cards are prohibited.** A card inside a card introduces two competing frames. Use a row/strip layout or a plain divider instead.

### Inputs / Fields
- **Style:** `surface-panel` background, `1px solid rgba(99,132,255,0.20)` border, `md` radius (8px).
- **Focus:** Border brightens to `rgba(79,131,255,0.50)`, soft glow ring `0 0 0 3px rgba(79,131,255,0.10)`.
- **Error state:** Border becomes Signal Red (`#f87171`), matching glow ring.
- **Disabled:** `text-ghost` text, border drops to `0.06` opacity, cursor `not-allowed`.
- **Font:** DM Sans 14px for input values. Label above in JetBrains Mono 10px uppercase.

### Navigation
- **Style:** `backdrop-filter: blur(24px) saturate(180%)`, `bg-glass` background, `68px` height. Sticks to top; scrolled state gains `--card-shadow`.
- **Logo:** JetBrains Mono, 700, 15px, letter-spacing 0.14em. All-caps product name.
- **Nav links:** JetBrains Mono 11px, letter-spacing 0.06em, `text-secondary` default. On hover: `text-primary`. Active page: `surface-raised` background pill.
- **Mobile:** Hamburger toggle. Menu slides down as a full-width block. Same token set.

### Regime Hero (Signature Component)
The dashboard's primary display: regime name in Cormorant Garamond (24–32px, 600), confidence bar in Signal Blue, equity bias / conviction / RBI signal in a 2×2 data grid using JetBrains Mono. Signal color applied to equity bias value only (green for RISK_ON, red for RISK_OFF, amber for NEUTRAL). The entire hero card uses `surface-deep` background with `--card-shadow`.

This is the one component where Cormorant Garamond appears in the product UI — because the regime name is a named judgment, not a data value.

### Terminal Display (Signature Component)
Used on the landing page hero: glass card with `backdrop-filter`, `surface-glass` background, floating animation (`floatY 7s ease`), scanline effect. Terminal header shows macOS-style traffic dots (non-functional, decorative). Data in JetBrains Mono throughout. This aesthetic is landing-page only; do not replicate the terminal chrome inside the product dashboard.

## 6. Do's and Don'ts

### Do:
- **Do** use Cormorant Garamond exclusively for headings (Display / Title hierarchy). Serif body text is prohibited.
- **Do** use JetBrains Mono for all machine-generated values: percentages, prices, regime codes, dates, tickers, label text.
- **Do** apply semantic signal colors (green/amber/red) only when a data value warrants the color. A green element is making a claim about market conditions.
- **Do** express card depth through the background progression (Midnight Vault → Surface Deep → Surface Panel → Surface Raised) and the 1px blue border ring. That is the system's depth language.
- **Do** maintain `backdrop-filter: blur(24px)` exclusively on the nav. Glass effects on cards are decorative, not structural.
- **Do** provide `@media (prefers-reduced-motion: reduce)` fallbacks on every animation, consistent with the existing pattern in all HTML files.
- **Do** use `text-wrap: balance` on all Display and Title headings to prevent orphans.
- **Do** verify contrast on monospace label text at 9–11px — this is the highest-risk surface in both dark and light modes. Text Ghost (`#2e3d58`) fails 4.5:1 on Surface Deep; never use it for readable copy.
- **Do** keep Signal Blue as the single accent on any product screen. Indigo and violet are gradient-only.

### Don't:
- **Don't** use retail fintech patterns (Zerodha/Groww aesthetic): color saturation on navigation, progress-bar reward loops, emoji-heavy copy, rounded pill buttons with gradients as the default button style.
- **Don't** use the generic AI-SaaS cream aesthetic: warm near-white backgrounds (`#faf7f2`, `#f5f0ea`, `--paper`, `--sand`), purple-to-blue gradient CTAs, identical icon+heading+text card grids, uppercase eyebrow on every section as reflexive scaffold.
- **Don't** reproduce news/media noise: no sidebar of unrelated tickers, no ad-format content blocks, no zero-hierarchy information dumps.
- **Don't** replicate the Bloomberg terminal aesthetic: orange-on-black, extreme density with no whitespace, all-uppercase data tables filling the full viewport with no visual breathing room.
- **Don't** use `background-clip: text` gradient text. Gradient text is an absolute ban across both surfaces. Italic Cormorant Garamond in Signal Blue (solid) is the emphasis pattern for display headings.
- **Don't** use `border-left` or `border-right` greater than 1px as a colored accent stripe on cards, callouts, or list items. The `feature-detail` side-stripe (currently `border-left: 2px solid var(--blue)`) is a legacy pattern and must not be extended.
- **Don't** apply `box-shadow: drop-shadow` or ambient shadow to static resting cards. Shadows communicate state change (hover, floating). Resting depth is communicated through surface color and the 1px ring.
- **Don't** nest cards. A card inside a card creates two competing containers. Use a divider, a data row, or a plain background-shift to separate grouped items within a card.
- **Don't** introduce a fourth typeface. The three-family system (Cormorant Garamond / DM Sans / JetBrains Mono) is complete. A fourth signals indecision.
- **Don't** use Text Ghost (`#2e3d58`) for any body copy or legible label. It is a placeholder/disabled-state color only — it fails 4.5:1 contrast on Surface Deep.
