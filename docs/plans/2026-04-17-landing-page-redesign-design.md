# Landing Page Redesign — Design Spec

**Date:** 2026-04-17  
**Status:** Approved  
**Approach:** Framer Motion + full component redesign (premium light aesthetic)

---

## Goal

Replace the current Bootstrap-heavy, generic landing page with a premium, conversion-focused page that looks like a $10,000 website. Aesthetic reference: Stripe, Loom, Notion. Stack: React 18 + Bootstrap grid (layout only) + Framer Motion (all animations).

---

## Design Decisions

- **Aesthetic:** Premium light — white backgrounds, near-black text, surgical use of color
- **Hero:** Product-led — headline + real product screenshot in browser chrome
- **Features:** Bento grid — asymmetric cards, large cards have mini UI mockups
- **Animations:** Full package — scroll-driven reveals + micro-interactions on every interactive element
- **Content flow:** Enterprise credibility — Hero → Product Demo → Bento Features → Metrics → Integrations → CTA → Footer

---

## Page Sections

### 1. Navigation

- Minimal sticky nav
- Logo left, 4 nav links centered (Platform, Features, Integrations, Pricing), Sign In text link + "Get Started" pill button right
- On scroll: background transitions to `rgba(255,255,255,0.9)` with `backdropFilter: blur(12px)` and hairline bottom border (`1px solid #e2e8f0`)
- Framer Motion: nav items fade in on load with 60ms stagger

### 2. Hero

**Layout:** Two-column, full viewport height (`100vh`)

**Left column (55%):**
- Eyebrow badge: small pill, `"Enterprise AI Orchestration"`, subtle border
- Headline: 72–80px, weight 800, letter-spacing `-0.03em`, near-black (`#0a0a0a`)
- Subheadline: 18px, muted gray (`#6b7280`), 2 lines max
- CTA row: solid blue primary button + ghost secondary with arrow icon
- Social proof micro-line: i18n key `landing:hero.socialProof` → `"Trusted by teams at..."` + 3–4 inline company logos, 24px height (or text-only fallback `landing:hero.socialProofFallback` → `"500+ teams using AgentProvision"` if logo assets aren't ready)

**Right column (45%):**
- Browser chrome mockup containing real product screenshot
- Subtle drop shadow, slow infinite float animation (translateY ±8px, 6s)
  - Use Framer Motion `animate` prop with `transition: { repeat: Infinity, repeatType: "reverse", duration: 6, ease: "easeInOut" }` — do NOT use CSS `@keyframes` since the Animation System rule says all animations use Framer Motion
- Screenshot: Dashboard view as default

**Background:** Pure white with ultra-subtle dot grid pattern (SVG background, 20px spacing, `#e2e8f0` dots at 0.5 opacity)

**Animations:**
- Left column: slides up + fades in on load, 500ms, ease-out
- Right column: slides in from right with spring physics (`stiffness: 100, damping: 20`)
- 200ms stagger between headline, subheadline, CTAs, social proof
- Triggered on mount (no scroll needed — above the fold)

### 3. Product Demo

**Background:** `#f8fafc` (very light gray)

**Layout:**
- Centered section headline + one-line subheading
- Large browser chrome mockup (full-width minus 80px padding)
- Five pill navigation tabs below mockup: Dashboard, Agent Memory, AI Command, Agent Fleet, Workflows

**Interactions:**
- Tab click swaps screenshot with `AnimatePresence` crossfade (opacity 0→1, 300ms)
- Active tab has sliding underline indicator animated via Framer Motion `layoutId="tab-indicator"`
- Mockup entrance: scales from `0.95` to `1.0` as it enters viewport (`useInView` with `once: true`)

### 4. Bento Grid Features

**Background:** White

**Layout:** CSS Grid, 3 columns, 3 rows, asymmetric. Grid uses 6 equal columns internally (`grid-template-columns: repeat(6, 1fr)`):

```
Row 1: [AI Command — col-span 4]  [Memory — col-span 2]
Row 2: [Multi-Agent — col-span 2]  [Workflows — col-span 2]  [Security — col-span 2]
Row 3: [Inbox Monitor — col-span 2]  [Code Agent — col-span 4]
```

CSS grid assignment example:
```css
.bento-ai-command   { grid-column: span 4; }
.bento-memory       { grid-column: span 2; }
.bento-multi-agent  { grid-column: span 2; }
.bento-workflows    { grid-column: span 2; }
.bento-security     { grid-column: span 2; }
.bento-inbox        { grid-column: span 2; }
.bento-code-agent   { grid-column: span 4; }
```

**Large cards:**
- Mini UI mockup rendered as styled HTML (not screenshot) inside the card
- Gradient accent top border (2px, blue→teal)
- Title + 2-sentence description
- Hover: lift (`translateY(-4px)`), enhanced shadow, spring physics

**Small cards:**
- React icon (32px) in a colored rounded square
- Title + 1-sentence description
- Subtle gradient background (`#f8fafc` to white)
- Same hover lift as large cards

**Animations:**
- Cards reveal with `useInView` + staggered entrance (80ms per card)
- Each card: slides up 20px + fades in, `duration: 0.5`, spring easing

### 5. Metrics Strip

**Background:** Dark navy (`#0a0f1e`) — deliberate contrast break

**Layout:** Four stat blocks in a row, centered, equal width

**Stats:**
- `81` — MCP Tools
- `25+` — Native Workflows
- `5.5s` — Avg Response Time
- `88%` — Faster Than Baseline

**Animations:**
- Count-up animation on viewport entry: custom hook using Framer Motion `useMotionValue` + `animate()`
- Duration: 1.5s per counter, ease-out
- Section slides up as a unit on scroll (`y: 40 → 0`, opacity 0→1)
- Number color: white, label color: teal (`#5ec5b0`)

### 6. Integrations Showcase

**Background:** White

**Layout:**
- Centered headline: "Connects to everything you already use"
- Two rows of logos in infinite horizontal marquee
  - Row 1: scrolls left (continuous, no pause)
  - Row 2: scrolls right
- Speed: 30s per full loop
- Left and right edge fade: CSS gradient overlay (`rgba(255,255,255,0)` → `rgba(255,255,255,1)`)

**Logos (grayscale → full color on hover):**
Google, GitHub, Meta Ads, WhatsApp, Jira, Gmail, Google Calendar, TikTok, Slack, HuggingFace, PostgreSQL, Redis

**Implementation:** Pure CSS `@keyframes` marquee (no JS scroll listeners). The logo array is rendered twice in sequence inside a flex container — the second copy creates a seamless loop when the first copy scrolls out of view. `overflow: hidden` on the wrapper clips the overflow; pointer events disabled on duplicate row items to prevent double-click targets.

```css
@keyframes marquee-left { from { transform: translateX(0); } to { transform: translateX(-50%); } }
@keyframes marquee-right { from { transform: translateX(-50%); } to { transform: translateX(0); } }
```

### 7. CTA Banner

**Background:** Diagonal gradient, blue → teal (`135deg, #2563eb, #5ec5b0`)
- Gradient slowly animates: hue shifts over 8s, infinite loop via `@keyframes gradientShift`

**Content:** Centered, large headline, one-line subtext, single "Get Started Free" white button

**Animations:**
- Entrance: fades in + scales from `0.98` to `1.0` on viewport entry
- Button: spring micro-interaction on hover (scale 1.02) and press (scale 0.98)

### 8. Footer

**Background:** `#f8fafc`

**Layout:** Three columns
- Left: Logo + one-line tagline
- Center: Nav links (Platform, Features, Docs, GitHub)
  - "Docs" — links to `/docs` or `#` placeholder (no docs site yet; use `#` and leave a TODO comment)
  - "GitHub" — external link to the platform's public GitHub org if public, otherwise `#` placeholder
- Right: Social icons (GitHub, Twitter/X, LinkedIn) — all `#` placeholders unless actual handles configured

**Style:** Hairline top border, minimal, no clutter. Copyright line centered below.

---

## i18n Keys

All new sections add keys to `apps/web/src/i18n/locales/en/landing.json` (and the matching `es/landing.json`). **Do not reuse the existing `metrics` key** — it has a different shape (array of objects with `label/value/description`). The new count-up stats go under a new `statsStrip` key:

```json
{
  "nav": { "platform": "Platform", "features": "Features", "integrations": "Integrations", "pricing": "Pricing", "signIn": "Sign In", "getStarted": "Get Started" },
  "hero": {
    "socialProof": "Trusted by teams at",
    "socialProofFallback": "500+ teams using AgentProvision"
  },
  "statsStrip": {
    "tools": { "value": "81", "label": "MCP Tools" },
    "workflows": { "value": "25+", "label": "Native Workflows" },
    "responseTime": { "value": "5.5s", "label": "Avg Response Time" },
    "improvement": { "value": "88%", "label": "Faster Than Baseline" }
  },
  "integrations": { "headline": "Connects to everything you already use" },
  "cta": { "headline": "...", "subtext": "...", "button": "Get Started Free" }
}
```

**Migration for existing `cta` key:** The key already exists as `{ "heading": "...", "description": "..." }`. Rename `description` → `subtext` and add `"button": "Get Started Free"`. Do not create a new key — edit the existing one.

**`ctaBanner` key:** The current `CTASection.js` reads from `landing:ctaBanner.*`. After the rewrite, it will use `landing:cta.*`. The `ctaBanner` key becomes unused — it can be deleted from both `en/landing.json` and `es/landing.json` once the new `CTASection.js` is wired in.

The existing `metrics`, `hero` keys remain unchanged. All `nav`, `statsStrip`, and `integrations` entries are **additive only** — do not replace the whole file, merge these keys in.

---

## Mobile Responsiveness

All sections must respond to these breakpoints (Bootstrap grid + CSS media queries):

| Breakpoint | Behavior |
|---|---|
| `< 768px` (mobile) | Hero becomes single-column; product screenshot stacks below headline. Bento grid becomes 1 column. Metrics strip wraps to 2×2. Marquee speed increases to 20s. Nav collapses to hamburger. |
| `768px–1024px` (tablet) | Hero stays 2-col but hero right column shrinks to 45%. Bento grid becomes 2 columns. |
| `> 1024px` (desktop) | Full 3-col bento grid, 2-col hero. |

All `motion.div` entrance animations must disable gracefully via `prefers-reduced-motion`: wrap animation variants with a `useReducedMotion()` check from Framer Motion and return `{ opacity: 1, y: 0 }` immediately when reduced motion is preferred.

```jsx
const prefersReducedMotion = useReducedMotion();
const variants = prefersReducedMotion
  ? { hidden: { opacity: 1 }, visible: { opacity: 1 } }
  : { hidden: { opacity: 0, y: 20 }, visible: { opacity: 1, y: 0 } };
```

---

## Pre-Implementation Checklist

Before starting implementation, the following must be resolved:

1. **Company logos for social proof**: The "Trusted by teams at..." row in the hero requires 3–4 real company logos (24px height, SVG or PNG). Either source real customer logos (with permission) or use placeholder text like "500+ teams" without the logos until real ones are available.
2. **Integration logos**: Grayscale SVG logos for the marquee (Google, GitHub, Meta, WhatsApp, Jira, Gmail, Google Calendar, TikTok, Slack, HuggingFace, PostgreSQL, Redis). These are open-source brand assets — download to `apps/web/public/logos/integrations/` and reference by path.
3. **Product screenshots**: 5 screenshots for the Product Demo tab switcher (Dashboard, Agent Memory, AI Command, Agent Fleet, Workflows). Take these from the running local environment before starting the component.
4. **framer-motion install**: Run `npm install framer-motion` in `apps/web/` before writing any component.

---

## Animation System

All animations use **Framer Motion**. No animate.css dependency (can be removed).

### Scroll reveal pattern (reusable)
```jsx
import { useInView } from 'framer-motion'; // framer-motion's built-in hook, NOT apps/web/src/hooks/useInView.js

const ref = useRef(null);
const isInView = useInView(ref, { once: true, margin: "-100px 0px" });

const variants = {
  hidden: { opacity: 0, y: 20 },
  visible: { opacity: 1, y: 0 }
};

<motion.div ref={ref} variants={variants} initial="hidden" animate={isInView ? "visible" : "hidden"}>
```

### Stagger pattern (for grids/lists)
```jsx
const container = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.08 } }
};
```

### Micro-interaction pattern (buttons, cards)
```jsx
<motion.div whileHover={{ y: -4 }} whileTap={{ scale: 0.98 }}
  transition={{ type: "spring", stiffness: 400, damping: 17 }} />
```

### Tab indicator (layoutId)
```jsx
{activeTab === id && (
  <motion.div layoutId="tab-indicator" className="tab-underline" />
)}
```

---

## Component Structure

Current `FeatureDemoSection.js` (561 lines) must be decomposed:

```
components/marketing/
  LandingNav.js          # Sticky nav with scroll blur
  HeroSection.js         # Two-column hero (replace existing)
  ProductDemo.js         # Browser mockup + tab switcher (replace InteractivePreview.js)
  BentoGrid.js           # Asymmetric feature grid
  BentoCard.js           # Individual card (large + small variants)
  MetricsStrip.js        # Dark stats section with count-up
  IntegrationsMarquee.js # Dual-row logo marquee
  CTASection.js          # Animated gradient CTA (replace existing)
  LandingFooter.js       # Minimal footer
  hooks/
    useCountUp.js        # Count-up animation hook (new — uses Framer Motion animate())
```

`LandingPage.js` becomes a thin orchestrator that imports and composes these components.

**useScrollReveal:** Do NOT create a new hook. The existing `apps/web/src/hooks/useInView.js` already provides IntersectionObserver-based `once: true` detection. Framer Motion components will use `useInView` from `framer-motion` directly (its built-in hook, not the local one) to drive scroll-triggered variants. The existing `hooks/useInView.js` can be kept for non-animation usage.

**InteractivePreview.js reuse:** The existing component's implementation (auto-rotate timer, dot indicators, `PremiumCard` wrapper) is incompatible with the new Framer Motion `AnimatePresence`/`layoutId` design. Write `ProductDemo.js` from scratch. The only reusable parts are the `screenshots` array definition and `useState(0)` for active index — extract those patterns, discard the rest.

---

## Dependencies

**Add:**
- `framer-motion` — scroll animations, micro-interactions, layout animations

**Remove (after migration):**
- `animate.css` — replaced by Framer Motion
- `FeatureDemoSection.js` — decomposed into smaller components

**Keep:**
- Bootstrap 5 — grid/layout only, no animation classes used
- react-bootstrap — structural components only
- i18next — all text stays in translation files

**Cleanup required during migration:**
- `apps/web/src/index.js`: Remove `import 'animate.css/animate.min.css';` at line 4 — **must be done before or at the same time as the package removal, otherwise the build fails** (module not found).
- `apps/web/package.json`: Remove `animate.css` from dependencies and run `npm uninstall animate.css`.
- `LandingPage.css`: Remove the custom scroll-animation CSS classes (`.n-on-scroll`, `.n-stagger`, `.n-slide-in`, etc.) — Framer Motion replaces these entirely.
- `LandingPage.js`: Remove the existing `scrolled` state and `window.addEventListener('scroll', ...)` — `LandingNav.js` handles blur-on-scroll internally.
- `components/marketing/HeroSection.js` full rewrite must **not** use `NeuralCanvas`. The current file imports it (`import NeuralCanvas from '../common/NeuralCanvas'`) and renders it as a background. The new hero uses a plain SVG dot grid (CSS background-image). After the new `HeroSection.js` is wired in and verified, check if `NeuralCanvas.js` is referenced anywhere else (`grep -r NeuralCanvas apps/web/src`); if unused, delete `apps/web/src/components/common/NeuralCanvas.js`.
- Existing marketing components (`FeaturesSection.js`, `FeatureDemoSection.js`, `InteractivePreview.js`) should only be deleted **after** their replacements are wired into `LandingPage.js` and visually verified. Safe deletion order: comment out old import in `LandingPage.js` → add new import → verify build → delete old file.

---

## Typography

- Headline: existing font stack, weight 800, size 72–80px, letter-spacing `-0.03em`
- Subheadline: weight 400, size 18px, line-height 1.7, color `#6b7280`
- Section titles: weight 700, size 40px
- Card titles: weight 600, size 18px
- Body: weight 400, size 16px

---

## Color System

| Token | Value | Usage |
|---|---|---|
| `--ap-bg` | `#ffffff` | Page background |
| `--ap-bg-subtle` | `#f8fafc` | Section alternates |
| `--ap-bg-dark` | `#0a0f1e` | Metrics strip |
| `--ap-text` | `#0a0a0a` | Headlines |
| `--ap-text-muted` | `#6b7280` | Body, descriptions |
| `--ap-blue` | `#2563eb` | Primary CTA, accents |
| `--ap-teal` | `#5ec5b0` | Secondary accent, stats |
| `--ap-border` | `#e2e8f0` | Card borders, dividers |

---

## Files Changed

| File | Action |
|---|---|
| `apps/web/src/LandingPage.js` | Refactor to thin orchestrator |
| `apps/web/src/LandingPage.css` | Major rewrite — remove animate.css patterns, add bento grid, marquee |
| `apps/web/src/components/marketing/HeroSection.js` | Full rewrite |
| `apps/web/src/components/marketing/InteractivePreview.js` | Replace with `ProductDemo.js` |
| `apps/web/src/components/marketing/FeatureDemoSection.js` | Delete — decompose into BentoGrid + BentoCard |
| `apps/web/src/components/marketing/CTASection.js` | Full rewrite |
| `apps/web/src/components/marketing/FeaturesSection.js` | Delete — replaced by BentoGrid |
| `apps/web/src/components/marketing/LandingNav.js` | New |
| `apps/web/src/components/marketing/BentoGrid.js` | New |
| `apps/web/src/components/marketing/BentoCard.js` | New |
| `apps/web/src/components/marketing/MetricsStrip.js` | New |
| `apps/web/src/components/marketing/IntegrationsMarquee.js` | New |
| `apps/web/src/components/marketing/LandingFooter.js` | New |
| `apps/web/src/components/marketing/hooks/useCountUp.js` | New (create `hooks/` subdirectory first) |
| `apps/web/src/components/common/NeuralCanvas.js` | Delete after verifying no other components import it |
| `apps/web/src/index.js` | Remove `import 'animate.css/animate.min.css'` (line 4) |
| `apps/web/package.json` | Add `framer-motion`, remove `animate.css` |
| `apps/web/src/i18n/locales/en/landing.json` | Add `nav`, `statsStrip`, `integrations` keys; rename `cta.description`→`cta.subtext`, add `cta.button`; delete `ctaBanner` |
| `apps/web/src/i18n/locales/es/landing.json` | Same mutations as `en/landing.json` |
