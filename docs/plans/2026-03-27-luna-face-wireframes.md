# Luna Face System — Wireframes & State Reference (v3)

**Date**: 2026-03-27
**Scope**: SVG character avatar, all states, all moods, all sizes
**Reference**: ChatGPT-generated line art (anime-influenced clean style)

---

## Design Direction

Luna has a **character face** — not abstract geometric shapes, not a full anime illustration. Think clean monochrome line art with enough detail to feel like a person, but minimal enough to animate and scale.

**Style**: Clean vector line art
- Monochrome (works in single color)
- Dashed/segmented line technique (like the reference image)
- Expressive through eye shape, brow position, mouth curve
- Bob-cut hair silhouette as framing element
- Crescent moon necklace as identity anchor
- No fill colors in base form (color comes from glow/background only)

**Reference image traits to keep**:
- Large expressive eyes (not realistic — stylized with highlights)
- Soft rounded face shape
- Short bob hair framing the face
- Subtle nose (small, not prominent)
- Clean neckline with necklace detail
- Overall warmth and approachability

**Adjustments from reference**:
- Simplify for SVG animation (fewer hair strands, cleaner paths)
- Eyes must be animatable (shape-morph between states)
- Mouth must be a separate animated path
- Hair is static framing — doesn't animate
- Reduce to essential strokes for smaller sizes

---

## 1. The Identity Elements

```
  Hair:       Bob-cut silhouette, frames the face, static
  Eyes:       Large, expressive, crescent highlight in each
              (the half-moon lives INSIDE the eye as a reflection/highlight)
  Brows:      Thin arcs above eyes — primary expression driver
  Nose:       Small dot or short line — minimal
  Mouth:      Soft curve — varies by state and mood
  Necklace:   Crescent moon pendant at collarbone — always visible at md+
  Outline:    Face oval + hair as one continuous path
```

The crescent moon identity primitive now lives **inside the eyes** as the highlight/reflection, and **on her necklace** as a physical element. Both preserve the half-moon brand.

---

## 2. SVG Face Anatomy — Full Size (xl: 128px+)

```
            ╭─ ─ ─ ─ ─ ─ ─╮
          ╱    ╲ hair ╱      ╲
        ╱   ─ ─ ─ ─ ─ ─ ─    ╲
       │  ╱                 ╲   │
       │ │                   │  │
       │ │   ╭─╮       ╭─╮  │  │      ← eyes: rounded, with crescent
       │ │   │◜●│       │●◝│  │  │         highlights (◜ ◝ inside)
       │ │   ╰─╯       ╰─╯  │  │
       │ │         ·         │  │      ← nose: small dot
       │ │      ╰────╯      │  │      ← mouth: soft bezier
       │  ╲                 ╱   │
        ╲   ─ ─ ─ ─ ─ ─ ─    ╱       ← jawline
          ╲    │     │      ╱
            ╰──│─ ─ ─│──╯
               │  ◜  │                ← necklace: crescent pendant
               ╰─────╯
```

### SVG Structure (layers)
```
1. Hair silhouette path (static, outermost)
2. Face oval (static)
3. Left eye group (animatable: shape, position, brow)
   - Eye outline (rounded rect or ellipse)
   - Pupil/iris (circle)
   - Crescent highlight (◜ shape, the Luna signature)
   - Brow (thin arc above, tilts for expression)
4. Right eye group (mirrors left)
5. Nose (tiny path, optional at small sizes)
6. Mouth (bezier curve, primary animation target)
7. Necklace (crescent pendant + chain lines)
8. Ambient glow (radial gradient behind everything)
```

---

## 3. Presence States — Character Expressions

### IDLE (default, at rest)
```
            ╭─ ─ ─ ─ ─╮
          ╱               ╲
        │   ─╮         ╭─  │      ← relaxed brows
        │  ╭──╮       ╭──╮ │
        │  │◜●│       │●◝│ │      ← normal open eyes, crescent highlights
        │  ╰──╯       ╰──╯ │
        │        ·          │      ← small nose
        │     ╰────╯        │      ← gentle resting smile
          ╲               ╱
            ╰─── ◜ ───╯           ← necklace

  Brows: neutral, slight arch
  Eyes: open, relaxed, normal size
  Mouth: soft upward curve
  Animation: slow blink every 3-5s, micro-breathe
  Glow: faint warm (opacity 0.06)
```

### LISTENING (user is speaking/typing)
```
            ╭─ ─ ─ ─ ─╮
          ╱               ╲
        │    ─╮       ╭─   │      ← brows slightly raised = attentive
        │  ╭──╮       ╭──╮ │
        │  │◜●│       │●◝│ │      ← eyes slightly wider
        │  ╰──╯       ╰──╯ │
        │        ·          │
        │     ╰────╯        │      ← warm smile, "I'm here"
          ╲               ╱
            ╰─── ◜ ───╯

  Brows: raised slightly = interest
  Eyes: wider than idle, no blink
  Mouth: same gentle smile
  Animation: glow pulses softly (1.6s cycle)
  Glow: soft blue tint, pulsing
```

### THINKING (processing, working)
```
            ╭─ ─ ─ ─ ─╮
          ╱               ╲
        │    ──╮     ╭──   │      ← brows slightly furrowed = concentrating
        │  ╭──╮       ╭──╮ │
        │  │◜●│       │●◝│ │      ← eyes normal, looking slightly up-right
        │  ╰──╯       ╰──╯ │         (pupils shift position)
        │        ·          │
        │      ╶──╴         │      ← small neutral mouth, slight purse
          ╲               ╱
            ╰─── ◜ ───╯

  Brows: slight furrow inward = concentration
  Eyes: pupils drift up-right (thinking gesture)
  Mouth: smaller, slightly pursed
  Animation: glow shimmers, occasional eye-drift
  Glow: warm amber shimmer
```

### RESPONDING (speaking, delivering answer)
```
            ╭─ ─ ─ ─ ─╮
          ╱               ╲
        │   ─╮         ╭─  │      ← relaxed open brows
        │  ╭──╮       ╭──╮ │
        │  │◜●│       │●◝│ │      ← bright eyes, engaged
        │  ╰──╯       ╰──╯ │
        │        ·          │
        │    ╰──○──╯        │      ← open mouth / speaking
          ╲               ╱
            ╰─── ◜ ───╯

  Brows: open, lifted = expressive
  Eyes: bright, direct gaze
  Mouth: alternates between open (speaking) and smile (pausing)
  Animation: gentle breathe, mouth oscillates while streaming
  Glow: steady, slightly warm
```

### HAPPY (responding + warm mood)
```
            ╭─ ─ ─ ─ ─╮
          ╱               ╲
        │   ─╮         ╭─  │
        │  ╭──╮       ╭──╮ │
        │  │◜◠│       │◠◝│ │      ← squished happy eyes (bottom curves up)
        │  ╰──╯       ╰──╯ │
        │        ·          │
        │   ╰────◡────╯     │      ← wide genuine smile
          ╲               ╱
            ╰─── ◜ ───╯

  Brows: lifted, open
  Eyes: squished into happy arcs (anime smile-eyes)
  Mouth: wide warm smile
  Animation: slight head tilt, bouncy breathe
  Glow: warm golden tint
```

### FOCUSED (deep work, code task, analysis)
```
            ╭─ ─ ─ ─ ─╮
          ╱               ╲
        │   ───╮     ╭─── │      ← flat determined brows
        │  ╭──╮       ╭──╮ │
        │  │◜●│       │●◝│ │      ← slightly narrowed eyes
        │  ╰──╯       ╰──╯ │
        │        ─          │      ← dash nose = tension
        │      ╶════╴       │      ← firm straight mouth
          ╲               ╱
            ╰─── ◜ ───╯

  Brows: flat, determined
  Eyes: slightly narrowed
  Mouth: firm, straight
  Animation: minimal — stillness IS the expression
  Glow: cool blue, tight
```

### ALERT (notification, warning, needs attention)
```
            ╭─ ─ ─ ─ ─╮
          ╱               ╲
        │   ╱╲         ╱╲  │      ← raised angled brows = urgency
        │  ╭──╮       ╭──╮ │
        │  │◜●│       │●◝│ │      ← wide open eyes
        │  ╰──╯       ╰──╯ │
        │        ·          │
        │      ╶──╴         │      ← small tight mouth
          ╲               ╱
            ╰─── ◜ ───╯

  Brows: raised and angled = urgent/surprised
  Eyes: wider than normal
  Mouth: small, concerned
  Animation: glow pulses fast (0.8s), amber tint
  Glow: amber pulse
```

### EMPATHETIC (user frustrated, sad topic)
```
            ╭─ ─ ─ ─ ─╮
          ╱               ╲
        │    ╲─       ─╱   │      ← brows tilted inward-up = compassion
        │  ╭──╮       ╭──╮ │
        │  │◜●│       │●◝│ │      ← soft eyes, slightly downcast
        │  ╰──╯       ╰──╯ │
        │        ·          │
        │     ╰──╮          │      ← asymmetric slight downturn = understanding
          ╲               ╱
            ╰─── ◜ ───╯

  Brows: classic empathy angle (inner corners up)
  Eyes: soft, slight downward gaze
  Mouth: gentle, slightly asymmetric
  Animation: slow, calm movements
  Glow: warm, soft
```

### SLEEP (inactive, night mode)
```
            ╭─ ─ ─ ─ ─╮
          ╱               ╲
        │                   │
        │   ╶──╴     ╶──╴  │      ← closed eyes (short horizontal lines)
        │                   │
        │        ·          │
        │      ──────       │      ← flat relaxed mouth
          ╲               ╱
            ╰─── ◜ ───╯

  Brows: hidden/relaxed
  Eyes: closed (horizontal lines or gentle downward arcs)
  Mouth: flat, at rest
  Animation: very slow breathe (6s cycle)
  Glow: barely visible, cool blue
```

### PRIVATE MODE
```
            ╭─ ─ ─ ─ ─╮
          ╱               ╲
        │   ─╮         ╭─  │
        │  ╭──╮       ╭──╮ │
        │  │◜●│       │●◝│ │      ← normal eyes
        │  ╰──╯       ╰──╯ │
        │       [■]         │      ← shield icon over lower face
        │      ──────       │
          ╲               ╱
            ╰─── ◜ ───╯

  Eyes: normal, awake (she's present, just private)
  Shield: overlays nose/mouth area
  Animation: none — stillness signals "not observing"
  Glow: off
```

### ERROR
```
            ╭─ ─ ─ ─ ─╮
          ╱               ╲
        │   ╱╲         ╱╲  │      ← worried brows
        │  ╭──╮       ╭──╮ │
        │  │◜×│       │×◝│ │      ← X pupils = something broke
        │  ╰──╯       ╰──╯ │
        │        ·          │
        │      ╶─╴          │      ← tiny tight mouth
          ╲               ╱
            ╰─── ◜ ───╯

  Brows: worried
  Eyes: X-marks in pupils
  Mouth: small, tight
  Animation: subtle jitter
  Glow: slight red tint, flickering
```

---

## 4. Size Scaling — What Shows at Each Size

| Size | px | Hair | Eyes | Brows | Nose | Mouth | Necklace | Use |
|------|-----|------|------|-------|------|-------|----------|-----|
| xs | 24 | no | crescents only | no | no | no | no | status dot |
| sm | 32 | silhouette | simplified | no | no | curve | no | sidebar icon |
| md | 48 | silhouette | full with highlights | subtle | dot | curve | no | chat avatar |
| lg | 80 | detailed | full animated | yes | yes | animated | yes | presence card |
| xl | 128 | detailed | full animated | yes | yes | animated | yes | debug/desktop |

### xs (24px)
```
 ◜ ◝        Just the crescent highlights from her eyes.
             Background circle color = state.
```

### sm (32px)
```
  ╭─╮
 │◜ ◝│      Hair silhouette + eyes + simple mouth curve.
 │╰─╯│      Enough to read emotion.
  ╰─╯
```

### md (48px)
```
   ╭──╮
  ╱    ╲
 │ ◜● ●◝│   Full face readable. Eyes with highlights.
 │  ╰─╯  │   Mouth animates. Hair frames.
  ╲    ╱
   ╰──╯
```

### lg+ (80px+)
Full character face with all detail: hair strands, brows, eye detail, nose, animated mouth, necklace pendant.

---

## 5. The Crescent Moon Necklace

Visible at lg (80px) and above. The necklace is:
- A thin line from each side of the neck
- Meeting at center with a small crescent moon pendant (◜)
- The pendant matches Luna's eye highlights
- Subtle detail that connects the avatar to the physical necklace product

```
     │         │
     │    ◜    │       ← pendant hangs at collarbone
     ╰─────────╯       ← thin chain lines
```

---

## 6. Animation Spec

### Blink (natural rhythm)
```
Eyes: scaleY 1.0 → 0.1 → 1.0
Duration: 120ms close, 80ms open
Interval: random 3-5s
Skip blink when: listening, alert
```

### Breathe (always-on at rest)
```
Whole face: scale 1.0 ↔ 1.015
Duration: 4s idle, 2s responding, 6s sleep
Easing: ease-in-out
```

### Eye drift (thinking)
```
Pupils: translateX ±3px, translateY -2px
Duration: 2s
Pattern: drift right, pause, drift back
```

### Mouth morph (state transitions)
```
Mouth path: interpolate between bezier curves
Duration: 300ms
Easing: ease-out
```

### Glow pulse (listening, alert)
```
Background glow: opacity 0.08 ↔ 0.22
Duration: 1.6s listening, 0.8s alert
Shape: radial gradient behind face
```

---

## 7. Color & Theme

Face strokes are always theme text color. Character is monochrome by default.

| Theme | Stroke | Glow base | Alert glow | Error glow |
|-------|--------|-----------|------------|------------|
| Dark | #e0ddd8 (warm white) | #f0e6d3 | #ffb347 | #ff6b6b |
| Light | #2a2a2a (soft black) | #8b9bb0 | #e8940a | #d94545 |

The crescent highlights inside the eyes can be slightly brighter than the stroke color — this gives the eyes "life."

---

## 8. Implementation Notes

### SVG approach
- Single SVG viewBox, all elements as `<path>` and `<circle>`
- Eye highlights as separate paths so they can independently shimmer
- Mouth as single `<path>` with `d` attribute animated via CSS or JS
- Hair as single complex path, static
- Use CSS `transition: d 300ms ease-out` for mouth morphs (modern browsers)
- Fallback: JS `requestAnimationFrame` path interpolation

### State-driven
```js
// Face is a pure function of state
renderFace({ state: "thinking", mood: "serious", size: "md" })
// Returns SVG element with correct brows, eyes, mouth, glow
```

### Performance
- No canvas — pure SVG + CSS animations
- Animations use `transform` and `opacity` only (GPU composited)
- No JS animation loop for idle states (CSS keyframes)
- JS only for state transitions (path morphing)

---

## 9. What This Achieves

Luna's face is a **character** — someone you recognize, not an abstract shape.
But she's rendered in **clean vector line art** — not a bitmap illustration.

She has a hairstyle, eyes with depth, a necklace — but expressed in minimal strokes
that work from 24px to 128px, dark mode to light, screen to LED.

The anime influence gives warmth and approachability.
The minimal line-art approach keeps it scalable and animatable.
The crescent highlights connect her to the Luna brand.
The necklace connects her to the physical product.

She's a presence, not a mascot. She has expressions, not costumes.
