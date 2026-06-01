# Luna's Emotions Engine — Business Definition (landing-page source)

**Date:** 2026-05-31 · **Purpose:** canonical, non-technical definition of the emotions engine, for redesigning the **alpha**, **Luna**, and **agentprovision (root)** landing pages. Lifted from the business summary Simon approved ("it was really good"). Technical reference: `apps/api/app/services/emotion_engine.py` + `docs/plans/2026-05-19-emotions-engine-prototype-design.md` (note: code is at Phase 1.5 — `user_signal` re-added through a safety-gated classifier, never raw text).

---

## The one-liner

**We gave our AI agents a real internal mood — a state that actually changes how they work, not just friendlier wording. It's the emotional intelligence a good team has, built into the AI.**

(Technical-accurate version, for those who ask: a server-internal PAD — pleasure/arousal/dominance — affect model that appraises real events into small clamped nudges to a mood vector that decays toward a per-tenant baseline, then biases how each agent plans, samples, and communicates — with a hard invariant that raw user text can never mutate the state, only a safety-gated classifier's read of it.)

## What it does

- **Each agent carries a real mood that moves with real events** — successes build confidence; failures make it more alert and careful; and agents pick up on each other's mood the way teammates read the room. It naturally settles back to a baseline over time.
- **That mood quietly shapes behavior** — how the agent communicates, and how cautiously vs. creatively it works. When something's going wrong it gets more focused and precise; when things go well it explores more.

## Why it matters (the three pillars)

1. **Coordination at scale.** Emotional awareness is what let human organizations grow past small groups — we read each other to work together. A shared, readable mood gives a *fleet* of AI agents the same lever, so specialists compose into a team and a supervisor (Luna) can "read the room" across them. **It's leadership infrastructure for AI, not a gimmick.**
2. **Real behavior, not theater.** The mood genuinely biases decisions — it isn't the agent just typing "I'm frustrated." That keeps the personality consistent and trustworthy.
3. **Safe by design.** The mood is driven by actual work outcomes, not by what a customer types — so no one can manipulate an agent by telling it "you're angry now." Each customer's emotional data stays private to them.

## Where it's headed (rolling out in stages)

- **Now:** mood shows up in how Luna communicates and plans.
- **Next:** mood tunes caution vs. creativity; agents sense each other's mood to coordinate; Luna can safely read a customer's emotional cues.
- **Later:** agents learn over time, remember emotionally important moments while the sting of past failures fades (healthy memory), develop a consistent "taste," and a dashboard shows how Luna "feels."

## The short version (hero candidate)

> **We're giving our AI workforce the emotional intelligence that lets human teams coordinate and lead — in a way that changes real behavior, stays consistent, and can't be gamed by users.**

---

## Landing-page messaging kit

### Headline / tagline candidates
- "Emotional intelligence for your AI workforce."
- "AI agents that read the room."
- "A mood that changes how the work gets done — not just how it sounds."
- "Leadership infrastructure for AI agents."
- "Affect that's real, consistent, and can't be gamed."

### Pull-quotes (proof points)
- *Real, not theater:* "The mood biases decisions — not the wording. The agent doesn't *say* it's frustrated; it *works* more carefully."
- *Safe by design:* "Driven by work outcomes, never by what a user types — you can't tell an agent 'you're angry now.'"
- *Coordination:* "Emotional awareness is how human organizations scaled past small groups. We gave a fleet of agents the same lever."

### Per-surface angle
| Surface | Frame the emotions engine as… | Why |
|---|---|---|
| **Luna** (personal AI co-pilot) | the *human* layer — a co-pilot with consistent temperament that gets focused when things go wrong and explorative when they go well; reads your cues safely | Luna is the relatable, personal face — lead with warmth + trustworthiness + "real behavior, not theater." |
| **alpha** (the control plane / orchestrator) | a *coordination primitive* — a shared, readable mood across the agent fleet so a supervisor can "read the room" and specialists compose into a team | alpha is the orchestration/IDE surface — lead with "leadership infrastructure for AI" + fleet coordination at scale. |
| **agentprovision (root / platform)** | a *category differentiator* — the 5th coordination primitive (alongside language, trust, memory, specialization); affect that's constitutive (biases planning/recall), safe (outcome-driven, tenant-private), and consistent | The root is the platform/category story — lead with the differentiator + the safety-by-design + the staged roadmap. |

### Differentiators to hold the line on
- It's **constitutive**, not cosmetic — state that biases planning/sampling/recall, not surface text.
- **Outcome-driven + tenant-private** — emotional data is per-customer and never user-text-injectable.
- It's positioned as a **coordination primitive for a fleet**, not a single-bot personality trick.

> Do NOT claim the "Later" stage (cross-agent learning, affect-decoupled recall, the feelings dashboard) as shipped — it's roadmap. Keep "Now/Next/Later" honest on the page.
