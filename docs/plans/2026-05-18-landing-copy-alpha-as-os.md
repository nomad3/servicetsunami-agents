# Landing copy — reposition Alpha as the agent OS

Date: 2026-05-18
Owner: Alpha platform
Status: In review (PR #543)

## Problem

The landing copy lagged the product framing. Hero said "A Network of AI Agents That Runs Your Business" and led with Luna as the chief-of-staff persona. The actual product identity (per [[agentprovision_product_family]]) is:

- **AgentProvision** — the platform / runtime / company.
- **Alpha** — the agent OS surface (CLI + Tauri desktop). Apple → macOS analogy.
- **Luna** — the supervisor persona that runs *on* Alpha.

The old copy conflated "agent network" with the headline product, which left Alpha undersold and Luna over-fronted relative to where the product has moved.

## Approach

Refresh hero / CTA / footer copy in en + es to lead with Alpha as the OS. Keep Luna as the supervisor persona but move it out of the headline. Keep the agent-network message as supporting detail. Anchor the [[alpha_cli_kernel_principle]] (CLI-first by design) into one of the three hero highlights so the marketing surface matches the engineering rule.

Diff scope: i18n strings only — no component rewrites in this PR. The component wiring (rendering the new `hero.lead`, `hero.subHighlight`, etc. into HeroSection.js) lands as a follow-up commit on the same branch after the superpowers review caught that most of the new strings were orphaned in the React render path.

## Files touched

- `apps/web/src/i18n/locales/en/landing.json`
- `apps/web/src/i18n/locales/es/landing.json`
- `apps/web/src/components/marketing/HeroSection.js` — wire the rendered keys
- `apps/web/src/components/marketing/HeroSection.test.js` — assert renders

## Keys rewritten

- `hero.badge` — was "Distributed Agent Network" → now "Alpha — the Agent OS"
- `hero.title` — was "A Network of AI Agents That Runs Your Business" → now "Alpha: the agent operating system for your business"
- `hero.lead` — leads with `alpha` as the kernel, Tauri desktop as the second surface, AgentProvision as the underlying platform
- `hero.subtext` — repositions "from your terminal or the desktop app" as the entry vector
- `hero.subHighlight` — "AgentProvision is the platform. Alpha is the OS you actually use."
- `hero.spotlight.heading` — "One CLI. Every Agent."
- `hero.highlights.governedDataProducts` — "CLI-first, by design" — quotes real subcommands (`alpha run`, `alpha workspace clone`, `alpha recall`)
- `footer.tagline` — short, on-brand
- `cta.heading / subtext` — "Install Alpha. Run the OS." + Luna re-introduced as supervisor persona

## Review findings (superpowers, applied in-PR)

- **B1 — wrong CLI subcommands quoted.** First commit cited `alpha agent run` and `alpha sync push`; neither exists in `apps/agentprovision-cli/src/cli.rs`. Replaced with `alpha run` (run.rs), `alpha workspace clone` (workspace.rs:30 Clone), `alpha recall` (recall.rs).
- **I1 — orphaned i18n strings.** Most modified hero keys (`hero.lead`, `hero.subHighlight`, `hero.spotlight.*`, `hero.highlights.*`) were not rendered by any component. Wired into `HeroSection.js` using the existing `.hero-scroll__lead` CSS hook plus three new sub-elements for the highlights row.
- **I2 — es footer tagline overflowed `max-width: 260px`.** Shortened both en + es taglines.
- **I3 — "persona supervisora" (es)** misreads as "human supervisor" in LatAm Spanish. Changed to `supervisora IA`.
- **I4 — "escritorio listo" calque.** Changed to `listo para escritorio`.
- **N1, N4 NITs** — applied (cleaner es phrasing + capitalization).

## Verification

- `python3 -c "import json; json.load(...)"` parses both en + es files; key parity en↔es is clean (zero diffs in either direction).
- Local /landing render: hero lead + sub-highlight + three highlight blocks visible at 1280px and 375px viewports; footer tagline fits on one line at 260px.
- HeroSection.test.js asserts each rendered key is present and non-empty.

## Out of scope

- The dedicated `alpha.agentprovision.com` landing (separate `AlphaLandingPage.js`) — different surface, different rewrite. Tracked separately.
- Marketing-site nav / pricing / features tabs beyond the strings touched here.

## Status / next

PR #543 awaiting final CI + merge. Single deploy (api skipped via paths-filter; web build only).
