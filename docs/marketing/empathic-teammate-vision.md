# North-star vision: empathic AI that turns stateless agents into trusted teammates

**Date:** 2026-05-31 (overnight, Luna leading) · **Owner:** Simon
**Source:** Simon's framing + Luna's synthesis of the `theartificialintelligence` / `timeinvestors` slides he sent (3-layers-of-creativity + metacognition), digested into knowledge.

## The one sentence (Luna, lead)

> **AgentProvision is the operating layer that turns stateless AI agents into trusted teammates.**

Why it works: **memory makes agents stateful, emotion makes them empathic, teamwork makes them useful, trust keeps them honest.** That's the whole platform in one breath, and it's honest (no autonomous-everything).

## The four engines, mapped to the vision

| Engine | Turns agents… | Today (honest) |
|---|---|---|
| **Memory** | stateless → **stateful** | live: KG + pgvector recall pre-loaded every turn |
| **Emotion** | reactive → **empathic** | live: PAD mood biases tone/caution; fleet-wide coordination is **Next** |
| **Teamwork** | isolated → **useful (a team)** | live: roles, hand-offs, A2A coalitions on a blackboard |
| **Trust** | ungoverned → **honest** | guarded: trust_level + fail-closed veto are the next product edge |

## What the screenshots added (a real product primitive, not decoration)

### 1. The 3 layers of creativity → an honesty/trust primitive
Luna should be **explicit about which kind of creativity she's using**, never presenting recombination as original insight:
- **Interpolation** — recombine known context (repos, docs, emails, prior PRs, memories, screenshots). *"This is an interpolation from your prior NFL Route 53 decision + current infra."*
- **Extrapolation** — extend known patterns into a new situation.
- **Invention** — propose a *new* structure/workflow/product behavior, **clearly marked speculative until tested** ("not just solving the game — creating the game").

**Product shape (Luna's proposal, adopt):**
- **Source-grounding on every strategic suggestion:** source/context · copied|adapted|inferred|speculative · confidence · risk-if-wrong.
- **Advice Adoption Pipeline / "Adoption Card"** for media (reel/screenshot/thread → extracted claims → classify tactical|strategic|product-behavior → platform change + owner + acceptance criteria + verification + PR). Track adopted/rejected/needs-evidence.

### 2. Metacognition → the safety + self-awareness layer
"Notice your thoughts · question your reactions · **interrupt emotional reflexes** · **update beliefs instead of defending them**."
- This is *literally* the emotions-engine invariant (the constitutive-vs-performative cap = "interrupt emotional reflexes") + our verification discipline ("update beliefs, don't defend the first answer").
- **Product shape:** a `reflection_step` before high-friction actions (tool failures, user frustration, personal/financial/legal advice, repo ops with uncommitted changes): *What did the user actually ask? What am I assuming? What evidence do I have? What must I verify before acting?*

## How this shapes tonight's work

- **Landing:** the hero spine is the one sentence above. The four-engine section becomes "stateful · empathic · useful · honest." The **metacognition/honesty** story is a *credibility asset* — it's why this isn't a hallucinating GenAI wrapper (it labels creativity, cites sources, updates beliefs, gates on humans). Fold "labels what it knows vs infers vs invents" into the trust/Reality-Ledger section.
- **Product (per `2026-05-31-core-systems-strengthening-plan.md`):** trust_level write-only + salience junction first; the **creativity-label + source-grounding + reflection_step** are natural next primitives that operationalize "trusted teammate" (and are buildable, honesty-positive, low-risk). Metacognition is the bridge between the emotion engine (felt state) and safe action (gated, belief-updating).
- **Tenant test fleet (Luna):** Luna Supervisor · Memory Steward · Emotion Appraiser · Trust Sentinel · Execution Agent · Coalition Coordinator — the smallest fleet that makes the empathic-teammate story demonstrable end-to-end.

## Honesty bar (non-negotiable)
Human-in-the-loop prominent; Now/Next/Later on emotions; label invention as invention; no autonomous-everything. The metacognition principle *is* the brand: a teammate that knows what it knows.
