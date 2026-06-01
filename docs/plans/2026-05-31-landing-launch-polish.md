# Alpha landing — launch polish (Codex + Luna reviewed)

**Date:** 2026-05-31 → overnight to launch
**Status:** Build plan (Codex + Luna reviewed the PR #751 draft; findings converged)
**Owner:** Simon · **Lead:** Luna
**Branch:** `feat/landing-engines-story` (PR #751)
**Goal:** make the alpha landing launch-ready and *not look AI-generated*. The current motion (uniform `opacity:0,y:16` fade-up-on-scroll + `i*0.08` stagger on every card) is the template tell.

## Converged review findings (Codex + Luna agreed independently)

1. **Hero is too abstract.** "A network of AI agents that runs your operations" sounds big but is unbelievable without an ICP + what it replaces. **Luna's sharper ladder:**
   - Punch: **"The operating layer for agent teams."**
   - Differentiation: durable memory · affect signal · team structure · orchestration kernel.
   - Thesis: the missing coordination layer between isolated LLMs and real organizations.
   - Bound "runs operations" with human approval immediately: *"Agent teams that execute operational workflows with persistent memory, governed autonomy, and human approval where it matters."*
   - **One primary CTA** (Codex): `Install alpha` OR `Request access` — not signup + GitHub competing before trust is earned.

2. **The animation is the biggest craft tell** (both). Replace the fade-up recipe with **ONE signature, persistent network animation**: an SVG agent-network graph where signals propagate **command → orchestrator → specialist nodes → memory → approval gate**. Technique: SVG paths + animated `stroke-dashoffset`/gradient mask, node states via CSS vars, terminal lines reveal **causally** (not on scroll). Engines section: animate **signal propagation between the four engine nodes**, not the cards. Reduced-motion: static graph, no traveling pulses. Mobile: SVG (not particle canvas), deterministic, GPU-light. *This is the differentiator — the animation IS the product story (a living agent network), not decoration.*

3. **The #1 trust move** (both, independently): an honest **"Reality Ledger"** / live-vs-roadmap section — 3 columns:
   - **Live now:** memory, CLI-fleet routing, human approvals, Temporal workflows, team roles, audit trail, tenant isolation, BYO subscriptions.
   - **In alpha / guarded:** mood-influenced behavior, coalition formation, trust scoring, cross-agent state sharing.
   - **Research / next:** durable affect loops, adaptive team structure, long-running org learning.
   This proves we know shipped-system from thesis, and makes the ambitious parts *safer because labeled*. Doubles as Codex's "objection handling" (approval/audit/isolation/BYO).

4. **Factual fixes (must — embarrassing at launch):**
   - `AlphaPlatformPower.js:18` "Ollama Qwen2.5-Coder" → **Gemma 4** (matches repo docs).
   - Soften manifesto copy: "the substrate human organizations took millennia to evolve", "first platform", "running in production for months" → either evidence or restraint (Luna: "calm, technical, slightly insurgent — here is the architecture the market has been missing", not hype).
   - Hero terminal implies fleet-wide emotional coordination is **live** ("reading the room", "arousal high across the team") → keep in **Next** unless shipped (the Now/Next/Later honesty).

5. **"How it works" strip** (Codex): route task → form coalition → pause for approval → write audit trail. The proof layer today is internal metrics (4 / 90+ / 5.5s) which don't answer "why trust this for real work" — pair them with the flow + (when we have them) design-partner / named-workflow receipts.

## Build order (overnight)

1. **`AgentNetworkGraph.js`** — the signature SVG living-network animation (hero centerpiece). Nodes: command · orchestrator(alpha) · specialists(claude/codex/gemini/copilot) · memory · approval-gate. Animated signal pulses along edges; node state (idle/active/alert) via CSS vars; reduced-motion static; GPU-light, deterministic. *Hand-built, not a library default.*
2. **Hero copy** — Luna's ladder + one CTA; move fleet-emotion claim to honest framing.
3. **Reality Ledger section** (`AlphaRealityLedger.js`) — Now / Guarded / Next.
4. **Factual fixes** — Gemma 4; soften overclaims.
5. **De-template the motion** — replace per-card fade-up with the network-propagation idiom / causal reveals where it earns it; keep reduced-motion paths.
6. **Verify** — `react-scripts build` compiles; jest marketing suite; manual reduced-motion + mobile check.
7. **Re-review** — Codex + Luna on the rebuilt page before it's launch-called.

## Guardrails
- Honesty: human-in-the-loop prominent; Now/Next/Later intact; no autonomous-everything; don't claim roadmap as shipped.
- No invented metrics/logos. Real numbers only (4 engines, 4 CLI runtimes, 90+ MCP tools, ~5.5s p50).
- Match existing Ocean `--land-*` tokens, Framer patterns, i18n; keep section-header comments.
- PR stays open for Simon's sign-off; copy is first-draft to wordsmith; ES i18n needs a native pass.
