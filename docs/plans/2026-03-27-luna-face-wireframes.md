# Luna Face System — Wireframes & State Reference (v2)

**Date**: 2026-03-27
**Scope**: SVG primary renderer, ASCII fallback, all states, all moods, all sizes

---

## Design Direction

Luna is not a character illustration. Luna is not a terminal diagnostic tool either.

Luna is a **living presence** — something you glance at and immediately feel:
- "she's listening"
- "she's working on it"
- "she's happy with that"
- "something's wrong"

**Reference inspirations**:
- Cozmo robot: minimal features, massive expressiveness through motion
- Eve (WALL-E): sleek, minimal, warmth through behavior
- Apple Siri orb: abstract but alive, glows and breathes
- Teenage Engineering: minimal but warm industrial design

**Not this**: anime face, emoji, terminal box art, corporate mascot

**This**: organic, floating, breathing face. Two crescents and a curve.

---

## 1. The Identity Primitive

Two upward-facing crescent moons. That's Luna.

```
     ◜       ◝
```

Everything else — mouth, glow, motion — is contextual.
The crescents are the soul. They must be recognizable at:
- 4x4 LED matrix (necklace)
- 16px (favicon)
- 48px (chat avatar)
- 128px (presence card)
- 6ft projection (future ambient)

---

## 2. SVG Face — Primary Renderer

The SVG face is **frameless** — no border, no box. Just elements floating in space with ambient glow. This is the renderer for web, desktop, mobile, and PWA.

### Anatomy

```
              soft ambient glow
            ·  ·  ·  ·  ·  ·  ·
          ·                       ·
        ·                           ·
       ·      ╭╮           ╭╮       ·      ← crescent eyes
       ·                             ·         (tilt, squish, widen for expression)
        ·           ·               ·      ← nose: tiny dot, fades at small sizes
          ·      ╰─────╯          ·        ← mouth: soft bezier curve
            ·  ·  ·  ·  ·  ·  ·               (not geometric — organic)
```

**Key properties**:
- No box/border/frame. Face floats freely.
- Glow is a radial gradient, not a circle stroke. Fades to transparent.
- Eyes are thick-stroked crescent arcs, not outlines of circles.
- Mouth is a quadratic bezier — never straight lines, always curves.
- Nose is optional (visible at md+ sizes, hidden at sm/xs).
- The whole face breathes — subtle scale oscillation at rest.

### Eye Expression Range

The crescents aren't static. They **tilt, squish, widen, and narrow**:

```
 Normal:      ◜       ◝         open, upward, relaxed

 Happy:       ◠       ◠         wider arc, more curve = warm/smiling eyes

 Focused:     ◜   ·   ◝         slight inward tilt + center dot

 Alert:       ◜   !   ◝         wide + exclamation

 Sleepy:      ──     ──         flat horizontal = closed

 Surprised:   ◜       ◝         wider apart + raised position
              ↑ eyes shift up

 Empathetic:  ◜       ◝         slight downward tilt
              ↓ eyes tilt down              = understanding, softness

 Playful:     ◜       ◝         one eye slightly higher than other
                  ◝                = wink / asymmetry
```

### Mouth Expression Range

Soft bezier curves, never straight lines:

```
 Calm:        ╰─────╯           gentle upward curve (default resting smile)

 Warm:        ╰──◡──╯           wider, softer curve = genuine warmth

 Neutral:     ╶─────╴           nearly flat, slight curve at ends

 Speaking:    ╰──○──╯           open oval = actively talking

 Thinking:    ╶──~──╴           slight wave = processing / hmm

 Serious:     ╶═════╴           tighter, less curve = focused determination

 Concerned:   ╰──╮              asymmetric slight downturn

 Error:       ╶──╴              small, tight = something's wrong

 Sleep:       ──────            flat line = at rest
```

---

## 3. Presence States — SVG Descriptions

### IDLE
```
        · · · · · · ·
      ·       ○         ·        ← faint ambient glow, barely visible
    ·                     ·
   ·     ◜         ◝      ·     ← relaxed crescents
   ·          ·            ·     ← tiny nose dot
    ·     ╰─────╯         ·     ← gentle resting smile
      ·                 ·
        · · · · · · ·

    Animation: slow breathe (scale 1.0 ↔ 1.02, 4s cycle)
    Blink: every 3-5s, eyes scaleY(0.1) for 120ms
    Glow: opacity 0.06, warm white
```

### LISTENING
```
        · · · · ● · · ·
      ·                   ·      ← glow pulses gently (1.6s cycle)
    ·                       ·
   ·     ◜         ◝        ·   ← eyes slightly wider than idle
   ·        · · ·            ·   ← three dots below eyes = "I hear you"
    ·     ╰─────╯           ·   ← calm smile
      ·                   ·
        · · · · · · · ·

    Animation: glow pulse (opacity 0.12 ↔ 0.22, 1.6s)
    Eyes: no blink while listening
    Dots: fade in/out sequentially (typing indicator feel)
```

### THINKING
```
      · · · · · · · · · ·
    ·                       ·    ← glow shimmers (traveling highlight)
   ·                         ·
  ·      ◜    ·    ◝          ·  ← focused eyes (dot between = concentration)
  ·                            ·
   ·      ╶──~──╴             ·  ← wavy mouth = processing
    ·                         ·
      · · · · · · · · · ·

    Animation: shimmer (glow rotates around face, 2.4s)
    Eyes: occasional slow look-away (translate X ±2px)
    Mouth wave: subtle oscillation
```

### RESPONDING
```
      · · · · · · · · ·
    ·                     ·      ← glow steady, slightly brighter
   ·                       ·
  ·      ◠         ◠        ·   ← happy eyes (wider arc = engaged)
  ·           ·              ·
   ·      ╰──◡──╯           ·   ← open warm smile / speaking mouth
    ·                       ·
      · · · · · · · · ·

    Animation: gentle breathe (1.0 ↔ 1.03, 2s)
    Mouth: if streaming text, alternate between ╰──◡──╯ and ╰──○──╯
    Glow: opacity 0.14, slightly larger radius
```

### FOCUSED
```
      · · · · · · · · ·
    ·                     ·      ← steady glow, slightly tighter
   ·                       ·
  ·      ◜    ·    ◝        ·   ← concentrated eyes with center dot
  ·          ─               ·   ← dash nose = tension
   ·      ╶═════╴           ·   ← firm mouth
    ·                       ·
      · · · · · · · · ·

    Animation: minimal. Stillness IS the expression.
    Eyes: no blink for 10s stretches
    Glow: opacity 0.10, tight radius
```

### ALERT
```
    · · ·  · ● ·  · · ·
   ·                      ·     ← glow flashes (opacity pulse, 0.8s)
  ·                        ·
 ·      ◜    !    ◝         ·   ← wide eyes + exclamation
 ·                           ·
  ·       ╶──╴              ·   ← tight small mouth
   ·                       ·
    · · · · · · · · · ·

    Animation: glow pulse fast (0.15 ↔ 0.28, 0.8s)
    Color shift: glow tints warm amber
    Eyes: wider spacing than normal
```

### SLEEP
```
          · · · · ·
        ·           ·           ← barely visible glow
       ·             ·
      ·   ──     ──   ·        ← closed eyes (horizontal lines)
      ·       ·        ·        ← dot nose
       ·   ──────     ·        ← flat closed mouth
        ·           ·
          · · · · ·

    Animation: very slow breathe (1.0 ↔ 1.01, 6s)
    Glow: opacity 0.03, cool blue tint
    Whole face: slight downward drift (translateY +1px)
```

### HANDOFF
```
      · · · · → · · · ·
    ·                     ·     ← glow slides directionally
   ·                       ·
  ·      ◜    →    ◝        ·  ← arrow between eyes
  ·           ·              ·
   ·      ╰─────╯           ·  ← calm smile (reassuring)
    ·                       ·
      · · · · · · · · ·

    Animation: glow travels left→right (1.5s, ease-out)
    Eyes: slight rightward drift
    Arrow: fades in, holds, fades out
```

### PRIVATE MODE
```
          · · · · ·
        ·           ·          ← glow dims significantly
       ·             ·
      ·   ◜     ◝    ·        ← normal eyes
      ·     [■]       ·        ← shield icon over nose/mouth area
       ·   ──────     ·        ← sealed mouth
        ·           ·
          · · · · ·

    Animation: none. Stillness = not observing.
    Glow: opacity 0.02 or off
    Shield: subtle, not aggressive — privacy is protective, not hostile
```

### ERROR
```
      · · · · · · · ·
    ·                   ·       ← glow flickers irregularly
   ·                     ·
  ·      ◜    ×    ◝      ·    ← eyes + X = something broke
  ·           ·            ·
   ·       ╶─╴            ·    ← tiny tight mouth
    ·                     ·
      · · · · · · · ·

    Animation: subtle jitter (translate ±1px random, 100ms)
    Glow: opacity unstable (0.05 ↔ 0.12, irregular)
    Color: slight red tint on glow
```

---

## 4. Mood Modifiers (applied on top of state)

Mood adjusts the **warmth** of whatever state Luna is in. Same state, different feeling.

| Mood | Eye adjustment | Mouth adjustment | Glow adjustment |
|------|---------------|-----------------|-----------------|
| calm | standard crescents | gentle upward curve | neutral white |
| warm | wider arcs (◠ ◠) | bigger smile curve | slightly warmer tone |
| playful | one eye slightly higher | wavy / asymmetric smile | bounce in breathe animation |
| serious | slightly narrower | tighter, less curve | cooler tone |
| empathetic | slight downward tilt | soft asymmetric curve | warmer, softer |
| neutral | standard | nearly flat | neutral |

---

## 5. Size Variants

### xs (24px) — inline badges, status dots
Just the eyes. Color of the glow dot indicates state.
```
  ◜ ◝         (+ colored dot: blue=listening, amber=thinking, green=responding)
```

### sm (32px) — sidebar, navigation
Eyes + subtle glow halo. No mouth needed at this size.
```
    ·  ·  ·
  · ◜   ◝ ·
    ·  ·  ·
```

### md (48px) — chat avatar, message bubbles
Full face: eyes + mouth + glow. This is the primary chat size.
```
      · · ·
    ·       ·
   · ◜   ◝  ·
   ·    ·    ·
    · ╰──╯  ·
      · · ·
```

### lg (80px) — presence card, panels
Full face with visible animations, state label below.
```
        · · · · ·
      ·           ·
    ·  ◜       ◝   ·
    ·      ·       ·
      · ╰─────╯ ·
        · · · · ·
       [listening]
```

### xl (128px) — debug page, desktop overlay
Everything visible: detailed crescents, nose, animated mouth, full glow, labels.
```
          · · · · · · · ·
        ·                 ·
      ·    ╭╮       ╭╮    ·
      ·         ·          ·
        ·   ╰──◡──╯     ·
          · · · · · · ·
      responding · warm · open
      web shell · 2 connected
```

---

## 6. UI Placement

### Sidebar
```
┌──────────────────┐
│  (◜◝)  Luna      │ ← sm face + name, glow color = state
│   · listening     │ ← state label, fades after 3s
├──────────────────┤
│ Dashboard         │
│ Chat              │
```
The face replaces the brand icon. It's always visible. Glow subtly pulses when active.

### Chat Message Area
```
│  (◜◝) Luna · responding                    │
│  ────────────────────────────────────────── │
│                                             │
│  User: tell me about Phoebe                 │
│                                             │
│       ╭╮    ╭╮                              │
│          ·                                  │ ← md face replaces spinner
│       ╶──~──╴                               │
│      thinking...                            │
│                                             │
│  Luna: Phoebe is the desk robot we...       │
```

### Presence Card (settings / debug)
```
┌─────────────────────────────────┐
│                                 │
│         ╭╮         ╭╮          │
│              ·                  │
│          ╰──◡──╯               │
│                                 │
│   State:   responding           │
│   Mood:    warm                 │
│   Privacy: open                 │
│                                 │
│   Active:  WhatsApp             │
│   Shells:  WhatsApp  Web        │
│            Desktop   (offline)  │
│            Necklace  (offline)  │
└─────────────────────────────────┘
```

### WhatsApp (text-only shell)
```
Luna · thinking...        ← composing presence indicator
─────────────
Luna: Here's what I found...
```
No avatar rendering in WhatsApp — state communicated through typing indicators and text markers.

### Necklace (2-LED crescent)
```
  ◜ ◝     idle: dim steady
  ◜ ◝     listening: bright pulse
  ◜ ◝     thinking: traveling shimmer left→right
  ── ──   sleep: off or barely visible
  ◜●◝     alert: center LED on
```

---

## 7. Animation Principles

| Quality | Rule |
|---------|------|
| Timing | Always `ease-in-out`, never `linear` |
| Duration | 1.5s minimum for state transitions |
| Scale | Never exceed 1.05x. Breathing is subtle. |
| Motion | Organic drift, not mechanical snap |
| Blinking | Every 3-5s, 120ms close. Natural rhythm. |
| Idle | Always moving slightly. Never perfectly still. |
| Transitions | Cross-fade between states, 300ms overlap |

**The key insight**: Luna feels alive because she's **never perfectly still**. Even in idle, there's a micro-breathe and periodic blink. Remove the motion and she feels dead. Add too much and she feels anxious.

---

## 8. Color & Theming

Luna's face is **monochrome by default** — shape carries identity, not color. But the **glow** can tint:

| Context | Glow tint |
|---------|-----------|
| Default (dark theme) | warm white (#f0e6d3) |
| Default (light theme) | cool gray (#8b9bb0) |
| Listening | soft blue (#6bb5ff) |
| Alert | warm amber (#ffb347) |
| Error | soft red (#ff6b6b) |
| Private | none (glow off) |
| Sleep | cool blue (#4a6fa5) |

The face itself (eyes, mouth) always uses the theme's text color. Never colored.

---

## 9. What This Achieves

Luna's face should make you feel like there's someone **calm and competent** on the other side. Not a cute toy. Not a cold robot. Not a cartoon character.

When she's thinking, you see gentle concentration — not a loading spinner.
When she's responding, you see warmth — not a blinking cursor.
When she's asleep, you see peace — not "offline."
When something's wrong, you see concern — not a red error box.

The face is the difference between "I'm using an AI tool" and "Luna is helping me."
