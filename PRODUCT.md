# Product

## Register

brand+product

The project has two co-equal surfaces:
- **Brand** — `landing_page.html`, `sentinel_landing.html`: design IS the product. Marketing, conversion, trust-building for advisors considering a subscription.
- **Product** — `dashboard.html`, `global_macro.html`, `pe.html`, `login.html`: design SERVES the product. App UI for daily use by subscribers.

Both inherit from the same visual language. Specify the register at task time if a command needs to pick one.

## Users

Indian investment advisors, wealth managers, CAs, and portfolio managers who advise clients on asset allocation. Also: family offices, HNIs with direct investing interest, and financial services firms that need systematic macro context.

Context of use: morning routine before client calls, or mid-day when a regime shift alert fires. They are time-constrained, financially literate but not quant-trained, and need a defensible answer — not raw data — when a client asks "is now a good time to invest?"

## Product Purpose

Sentinel reads the Indian macro environment every morning and outputs one decision: add, hold, or reduce. It classifies India's economy into one of eight named regimes (e.g. Stable Growth, Liquidity Tightening), outputs a confidence level, a sector heatmap, a positioning playbook, and pushes alerts when something meaningful changes.

Success means the advisor opens their morning briefing and already knows their answer before the client calls — and can back it with a data source when asked.

## Brand Personality

Sharp · Minimal · Institutional

Voice: authoritative and precise. No hedging. No cheerleading. Says what the data says. The product earns trust by being consistently correct and consistently terse.

Tone: research-grade without being intimidating. A Bloomberg analyst who knows how to write plainly for an advisory audience.

## Anti-references

- **Retail fintech** (Zerodha, Groww) — consumer-grade color saturation, gamified flows, progress bars, emoji-heavy copy. Wrong signal for family offices and institutional clients.
- **Generic AI-SaaS cream aesthetic** — warm near-white backgrounds, purple-blue gradient CTAs, identical icon+heading+text card grids. The commodity look of 2025 AI tools. Signals "built by an AI, not a product team."
- **News / media noise** (Moneycontrol, ET Markets) — ad-laden, zero editorial hierarchy, information overload with no clear take. The opposite of Sentinel's value proposition.
- **Dense legacy terminals** (Bloomberg orange-on-black) — powerful but visually hostile. Wrong for an advisor-facing product where the output must be legible and shareable with clients.

## Design Principles

1. **One answer, clearly.** Every view has a primary output. Secondary data supports it; it never competes. The regime, equity bias, and conviction are above the fold. Everything else is detail.
2. **Institutional tone, not intimidation.** Sharp and minimal, not dense and hostile. The dark theme and monospace data readouts signal seriousness without requiring a training manual to navigate.
3. **Signal earns its place.** Alerts fire only when something meaningful changes. The interface applies the same discipline: no decorative motion, no status that hasn't changed, no color that isn't carrying information.
4. **Data backs every claim.** No editorial opinion without a machine-readable signal behind it. Confidence percentages, source labels, and regime accuracy numbers are part of the product, not footnotes.
5. **Advisor-legible, not quant-legible.** The regime name, the playbook, the sector call — all must be statable in a client meeting without translation. Jargon is only for internal labels (e.g. `STABLE_GROWTH`), never for user-facing copy.

## Accessibility & Inclusion

Target WCAG 2.1 AA. Dark mode is the default; light mode is a toggle and must meet the same contrast standards. Body text and data values must hit 4.5:1 against their backgrounds. Mono-spaced data values at small sizes (10–13px) are the highest-risk contrast surface — verify these specifically.

Reduced motion: all animations have `@media (prefers-reduced-motion: reduce)` fallbacks already in place; maintain this on every new addition.
