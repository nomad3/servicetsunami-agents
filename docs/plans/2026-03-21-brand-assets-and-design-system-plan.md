# Wolfpoint.ai Brand Assets & Design System Plan

> Extract, name, and integrate proper brand assets across landing page and internal platform pages.

**Date:** 2026-03-21
**Status:** Ready to execute

---

## 1. Current State (Problems)

### Assets
All 5 files in `apps/web/public/assets/` are **JPEGs disguised as PNGs** — no transparency, 640x640, RGB mode:
- `banner-art.png` — Wolf illustration (used nowhere currently)
- `favicon.png` — Wolf icon with checkered background baked in
- `logo-dark.png` — Wolf logo with "wolfpoint.ai" text (no transparency)
- `logo-light.png` — Same logo, light version (no transparency)
- `feature-1.png` — Feature illustration (used nowhere)

### Missing Assets
- No proper transparent logo (SVG or true PNG with alpha)
- No favicon.ico from the wolf design
- No app icons (logo192.png, logo512.png are still React defaults)
- No og:image for social sharing
- No dark/light mode variants that actually work
- No loading spinner branded asset

### Color Palette (from CSS)
Current gradient: `linear-gradient(135deg, #2b7de9, #5ec5b0)` — blue to teal
Accent: `#0ce9e9` (cyan)
Dark background: `#0a1628` (deep navy)
Text on dark: white / `text-soft` (muted white)

### Text Color Issues
- Navbar links go white-on-white when scrolled (partially fixed)
- Several section headings use `text-white` on light backgrounds
- `gradient-text` class renders teal/blue gradient — correct but needs to be used consistently

---

## 2. Brand Identity — Wolfpoint.ai

### Color Palette

| Token | Hex | Usage |
|-------|-----|-------|
| `--wolf-primary` | `#0ce9e9` | Cyan accent — CTAs, active states, glows |
| `--wolf-secondary` | `#005be6` | Deep blue — links, secondary buttons |
| `--wolf-gradient-start` | `#2b7de9` | Gradient headings start |
| `--wolf-gradient-end` | `#5ec5b0` | Gradient headings end |
| `--wolf-bg-dark` | `#0a1628` | Dark mode backgrounds |
| `--wolf-bg-surface` | `#0d1b2a` | Card/surface backgrounds (dark) |
| `--wolf-bg-light` | `#f0f4f8` | Light mode backgrounds |
| `--wolf-text-dark` | `#1a2332` | Text on light backgrounds |
| `--wolf-text-light` | `#ffffff` | Text on dark backgrounds |
| `--wolf-text-muted` | `#8899aa` | Muted/secondary text |

### Typography
- Headings: Inter or Geist, bold, gradient or `--wolf-text-dark`
- Body: Inter, regular, `--wolf-text-dark` (light mode) or `--wolf-text-light` (dark mode)
- Code: JetBrains Mono

---

## 3. Asset Generation Plan

### Phase 1: Extract & Convert Current Assets

| Task | Input | Output | Method |
|------|-------|--------|--------|
| 3.1.1 | `logo-dark.png` (JPEG) | `wolf-logo-dark.svg` | Trace in Figma/Inkscape or regenerate as SVG |
| 3.1.2 | `logo-light.png` (JPEG) | `wolf-logo-light.svg` | Same |
| 3.1.3 | `favicon.png` (JPEG) | `favicon.ico` + `favicon-32.png` + `favicon-16.png` | Remove checkered bg, convert |
| 3.1.4 | `logo-dark.png` | `logo192.png` + `logo512.png` | Resize with proper transparency |
| 3.1.5 | `banner-art.png` | `wolf-hero-bg.webp` | Optimize, convert to WebP for performance |

### Phase 2: Generate Missing Assets

| Asset | Size | Variants | Usage |
|-------|------|----------|-------|
| `wolf-icon.svg` | scalable | cyan on dark, dark on light | Navbar brand, favicon source |
| `wolf-logo-full.svg` | scalable | dark bg, light bg | Landing page, emails, docs |
| `wolf-wordmark.svg` | scalable | dark bg, light bg | Navbar text + icon combo |
| `og-image.png` | 1200x630 | one | Social sharing meta tag |
| `apple-touch-icon.png` | 180x180 | one | iOS home screen |
| `wolf-loading.svg` | 48x48 | animated | Loading spinners |
| `wolf-pattern.svg` | tileable | subtle | Section backgrounds |

### Phase 3: Naming Convention

```
public/assets/brand/
├── wolf-icon.svg                    # Icon only (scalable)
├── wolf-icon-cyan.svg               # Cyan variant
├── wolf-logo-full-dark.svg          # Full logo for dark backgrounds
├── wolf-logo-full-light.svg         # Full logo for light backgrounds
├── wolf-wordmark-dark.svg           # Text logo for dark backgrounds
├── wolf-wordmark-light.svg          # Text logo for light backgrounds
├── wolf-hero-bg.webp                # Hero section background
├── wolf-pattern-dots.svg            # Subtle dot pattern for sections
├── wolf-loading.svg                 # Animated loading spinner
├── og-image.png                     # Open Graph social preview
├── apple-touch-icon.png             # iOS icon
├── favicon.ico                      # Multi-size favicon
├── favicon-32.png                   # 32x32 favicon
├── favicon-16.png                   # 16x16 favicon
├── logo192.png                      # PWA icon
└── logo512.png                      # PWA icon large
```

---

## 4. Integration Plan

### Phase 4: Landing Page

| Component | Current Issue | Fix |
|-----------|-------------|-----|
| Navbar brand | Text only "wolfpoint.ai" | Replace with `wolf-icon.svg` + wordmark |
| Navbar scrolled | White text on white bg | Already fixed — dark text when scrolled |
| Hero background | NeuralCanvas animation only | Add `wolf-hero-bg.webp` as subtle overlay |
| Section headings | Mix of `text-white` and `gradient-text` | Standardize: dark bg → `gradient-text`, light bg → `--wolf-text-dark` |
| CTA buttons | Generic Bootstrap blue | Use `--wolf-primary` cyan gradient |
| Footer | Plain text | Add wolf icon + social links |

### Phase 5: Internal Platform Pages (Dashboard)

| Component | Current | Update |
|-----------|---------|--------|
| Sidebar logo | Text "ServiceTsunami" or "Wolfpoint" | `wolf-icon.svg` + brand name |
| Login page | Generic form | Wolf hero bg + branded login card |
| Register page | Generic form | Same wolf branding |
| Loading states | Bootstrap spinner | `wolf-loading.svg` |
| Empty states | Text only | Wolf icon + message |
| Favicon | Old React icon | `favicon.ico` from wolf |
| Page title | "ServiceTsunami" | "wolfpoint.ai" |
| Email templates | None | Wolf header + footer |

### Phase 6: Meta & SEO

| Tag | Value |
|-----|-------|
| `<title>` | wolfpoint.ai — The Distributed Agent Network |
| `og:image` | `/assets/brand/og-image.png` |
| `og:title` | wolfpoint.ai — A Network of AI Agents That Runs Your Business |
| `og:description` | Deploy specialized AI agents across your operations. Each agent owns a domain, shares memory, and coordinates automatically. |
| `theme-color` | `#0a1628` |
| `apple-touch-icon` | `/assets/brand/apple-touch-icon.png` |

---

## 5. Execution Order

```
Step 1: Generate/acquire proper SVG assets (needs designer or AI tool)
Step 2: Set up public/assets/brand/ directory with naming convention
Step 3: Create CSS custom properties for the full color palette
Step 4: Update landing page components to use new assets
Step 5: Update internal platform (sidebar, login, register, favicon)
Step 6: Update manifest.json, index.html meta tags
Step 7: Rebuild and test both light and dark modes
Step 8: Commit and deploy
```

## 6. Dependencies

- **SVG logo assets**: Need to be generated externally (Figma, AI image tool with SVG output, or manual trace). Claude Code cannot generate images.
- **Color palette CSS variables**: Can be done immediately in code.
- **Component updates**: Can be done immediately once assets exist.

## 7. What I Can Do Now (Without New Assets)

1. Set up CSS custom properties for the full color palette
2. Fix all text color issues (white text on light backgrounds)
3. Create the `public/assets/brand/` directory structure
4. Convert existing JPEGs to proper PNGs with transparency removed (background → transparent via Python/PIL)
5. Update manifest.json, index.html meta tags
6. Update component references to use new asset paths
7. Generate og-image from existing assets programmatically
