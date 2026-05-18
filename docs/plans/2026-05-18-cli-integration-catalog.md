# CLI Integration Catalog

Date: 2026-05-18
Owner: Alpha platform
Status: Open / research

## Why this exists

Alpha is positioned as the agent OS for AgentProvision (Apple → macOS analogy: AgentProvision = platform, Alpha = OS, Luna = supervisor persona). The OS surface is the **Alpha CLI as kernel**, and the agent network behind it dispatches to a fleet of external CLIs per task — today: Claude Code, Codex, Gemini CLI, GitHub Copilot CLI, OpenCode. The product roadmap calls for two parallel expansions:

1. **Coding CLI fleet** — every leaf-agent CLI worth routing chat turns through. Each one is a swap-in for the others depending on cost, latency, and capability.
2. **Specialist CLIs** — MCP servers that expose non-coding capabilities (creative content, search, finance, ops). The agent network calls these as tools, not chat surfaces.

This doc tracks the candidates, their licensing, and the integration cost. It is the input to a sequencing decision (which to wire first).

## Business model gate — read first

The user's preferred direction is a **single-subscription Alpha experience** that fans out across N upstream CLIs with quota controls per provider. Two viable lanes:

- **Lane A — BYOK + orchestration value.** User provides their own subscription to upstream CLIs; Alpha provides routing, memory, coalition. Aligns with every existing CLI provider's ToS. No legal risk. Limits the "one bill" pitch.
- **Lane B — Open-weight / commercial-resale-permitted models.** Alpha holds the inference contract and meters per-tenant. License-compatible models only (DeepSeek, Qwen, GLM, Yi, Mistral, Llama 4). No ToS conflict.

**Lane A is ToS-safe for the four big CLIs today** (Claude Code, Codex, Gemini, Copilot). Reselling those is forbidden by their respective Commercial Terms; even if technically wrapped, providers detect via usage fingerprints (issuance cadence, IP distribution, model-call patterns) and revoke en masse. See [[2026-05-17-gemini-cli-picker-and-disk-pressure-session]] for the kind of cascade failure that happens when one provider tightens.

**Lane B is where the one-bill pitch lives.** Chinese / Asian open-weight code models plus a few Western OSS ones cover ~80% of common coding workloads at a fraction of the per-call cost, and the licenses permit reselling.

Both lanes ship in parallel. This catalog captures the candidates for each.

## Categories

- **CODE-CLI** — chat-turn surface; routes user prompts to a model, edits code, runs tests.
- **CREATIVE-CLI** — generates media (image/video/audio/storyboard); usually MCP-server-shaped.
- **SPECIALIST-MCP** — non-coding tools exposed via MCP (search, scrape, finance, ops).

Each candidate gets evaluated on:
- License (model + CLI separately if they differ)
- Resale OK? (whether Lane B works with this provider)
- Model quality (rough tier vs Claude Sonnet 4.5 baseline on SWE-bench / livebench coder)
- CLI maturity (production-ready vs experimental)
- MCP support (server-side, client-side, both, neither)
- Integration cost (LOC + days estimate against the existing `cli_executors/` pattern)
- BYOK or platform-hosted

## Already integrated (status quo)

| Name | Lane | Category | Status |
|------|-----:|---------:|--------|
| Claude Code | A (BYOK only) | CODE-CLI | shipped, prod |
| Codex (ChatGPT) | A (BYOK only) | CODE-CLI | shipped, prod |
| Gemini CLI | A (BYOK only) | CODE-CLI | shipped, prod (regex bug fixed 2026-05-17) |
| GitHub Copilot CLI | A (BYOK only) | CODE-CLI | shipped, prod |
| OpenCode | A (free / BYOK) | CODE-CLI | shipped, prod (routing floor) |

## Candidate — Chinese open-weight code CLIs (Lane B)

These are the legitimate arbitrage lane. Every model below is openly licensed for commercial use.

| Candidate | Model behind it | Model license | CLI | CLI license | Lane B viable? |
|-----------|-----------------|--------------:|-----|------------:|---------------:|
| **Qwen Code** | Qwen3-Coder | Apache 2.0 | `qwen-code` (npm) | Apache 2.0 | ✅ |
| **Tongyi Lingma CLI** | Qwen3 series | Apache 2.0 | official Alibaba CLI | Apache 2.0 | ✅ |
| **DeepSeek CLI** | DeepSeek V3/R1 | MIT | various community CLIs + official API | mixed | ✅ |
| **GLM Code / Zhipu CLI** | GLM-4.6 / GLM-Coder | Apache 2.0 | preview, official | Apache 2.0 | ✅ |
| **Yi-Coder via Aider/Continue** | Yi-Coder | Apache 2.0 | uses Aider as shell | Apache 2.0 | ✅ |
| **Moonshot Kimi K2** | Kimi K2 | Apache 2.0 | official CLI | Apache 2.0 | ✅ |
| **Doubao Pro-Code** | ByteDance Doubao | proprietary, commercial license | official CLI (Volcano Engine) | proprietary | Negotiate; pricing-heavy |
| **MiniMax abab** | abab series | proprietary | nascent CLI | proprietary | Negotiate |
| **InternLM** | InternLM2-Chat-Coder | Apache 2.0 | mostly research | Apache 2.0 | ✅ |

**Priority for Lane B first push:** Qwen Code (most mature CLI), DeepSeek (best per-dollar model), GLM (recent preview release worth tracking). Wire these three as new `cli_executors/{qwen,deepseek,glm}.py` following the existing pattern; each is a ~150-200 LOC executor.

## Candidate — Western OSS code CLIs (Lane A / BYOK)

| Candidate | License | Maturity | MCP | Notes |
|-----------|--------:|---------:|----:|-------|
| **Aider** | Apache 2.0 | mature | client | Multi-model, well-known, easy wrap |
| **Goose** (Block) | Apache 2.0 | mature | server+client | MCP-native; aligns perfectly with our model |
| **Plandex** | MIT | mid | client | Strong planning model |
| **OpenHands** (was OpenDevin) | MIT | mid | client | Full-agent; heavier integration |
| **SWE-agent** (Princeton) | MIT | research | n/a | Reference-grade; benchmark useful |
| **Open Interpreter** | AGPL | mature | n/a | AGPL is a license risk for us — skip or contain |

**Priority:** Aider + Goose first. Aider has the broadest user mindshare; Goose's MCP-native posture means it slots into Alpha's coalition model with minimal glue.

## Candidate — Creative-content CLIs (CREATIVE-CLI)

| Candidate | Models | License | CLI form | Lane? |
|-----------|--------|--------:|---------:|------:|
| **Higgsfield** | Sora 2, Kling 3.0, Veo 3, Nano Banana Pro, Cinema Studio 3.5, +25 | proprietary, per-credit | MCP server + CLI ("Turn Claude into a creative engine") | A (BYOK to Higgsfield) — resale terms unknown, needs research |
| **Runway** | Gen-3 Alpha, Gen-4 | proprietary | API + early CLI | A (BYOK) |
| **Pika** | Pika 1.5 | proprietary | API only today | A (BYOK) |
| **Suno** | Suno V4 | proprietary | API beta | A (BYOK) |
| **ElevenLabs** | TTS + sound effects | proprietary | mature CLI/SDK | A (BYOK) |
| **Replicate** (aggregator) | thousands | varies per model | mature CLI | A (BYOK to Replicate, mixed per-model resale) |

**Higgsfield integration vector:** their CLI exposes MCP tools that any MCP client can call. Alpha's marketing/sales specialist agent would gain ad-creative generation. Add as an MCP source per tenant (BYO Higgsfield account), not as a chat surface. ~80 LOC for the MCP registration + credential vault entry.

## Candidate — Specialist-MCP servers (SPECIALIST-MCP)

Not chat surfaces — pure tool sources. Wire as MCP per-tenant.

| Candidate | What it provides | License |
|-----------|------------------|--------:|
| **Exa MCP** | semantic web search | freemium API |
| **Perplexity MCP** | search + cite | API |
| **Tavily MCP** | research / scraping | API |
| **Linear MCP** | issue tracking ops | OAuth |
| **Notion MCP** | docs | OAuth |
| **Stripe MCP** | billing ops | API key |
| **Cloudflare MCP** | edge ops | API |

## Names I couldn't identify (need user input)

- **"Obsidia"** — closest matches I can think of: Obsidian (notes app, has a community `obsidian-cli` but it's not an agent CLI), Obsidius, Obsius. None of these clearly fit the catalog category. Please drop a URL or repo so I can evaluate.

## Sequencing proposal

User prioritization 2026-05-18: Higgsfield moves into Wave 1 because it unlocks marketing-agent use cases AND powers Alpha's own marketing content pipeline. The Lane B code-CLI work runs in parallel; they touch different code paths (chat-turn executor vs MCP source registration).

**Wave 1 — Marketing creative + Lane B foothold (parallel, 1-2 weeks):**
- **1a — Higgsfield MCP source.** Register Higgsfield's MCP server as a per-tenant tool source in the existing credential vault + MCP registry. Wire the Marketing/Sales specialist agent to discover its tools (Sora 2 video, Nano Banana image, Cinema Studio, Storyboard Generator) and call them via `call_mcp_tool`. ~120 LOC: credential schema entry + MCP server config + agent tool-group binding + integration card on the integrations page. BYOK to Higgsfield until commercial terms are confirmed.
- **1b — Qwen Code executor.** First Lane B code-CLI executor as the reference. ~200 LOC in `apps/code-worker/cli_executors/qwen.py` mirroring the existing claude/codex pattern. Apache 2.0, no resale gate. Default-on for new tenants on a `starter / open-weights` plan tier.

**Wave 2 — OSS code CLI parity + Lane B breadth (1-2 weeks):**
- DeepSeek + GLM executors (mirrors Qwen executor; different model endpoints).
- Aider + Goose executors (BYOK; broader user mindshare; Goose is MCP-native).

**Wave 3 — Specialist MCP suite (rolling):**
- Exa, Perplexity, Tavily, Linear, Notion, Stripe, Cloudflare via MCP. Lower priority; each is a few hundred LOC.

## Early-stage funding plan (founder-funded, 2026-05-18)

Founder is committing real spend to validate the lanes before they're product-tiered. Treat this as a stop-gap, not the steady-state architecture:

- **Higgsfield top-tier ($200/mo)** — founder's account funds Alpha's marketing content (Alpha's own marketing) AND backs the Marketing/Sales specialist agent for early tenants. The single shared account model is fine for friends-and-family scale but hits two ceilings: (a) Higgsfield's per-account rate limit will throttle at some N concurrent tenants, (b) account-sharing is a ToS grey area — depends on what Higgsfield's commercial terms allow. Verify those terms before the early-tenant count crosses ~10. The clean upgrade path is BYO-Higgsfield-key per tenant + a built-in fallback to the founder account on the included tier.
- **Codex $100-$200 tier** — increased budget on the founder's ChatGPT account; same friends-and-family scoping applies (ChatGPT subs are per-user; resale is a ToS violation; the founder uses it personally + Alpha uses it as a dev / dogfood key, not as a multi-tenant resale).
- **Gemini possibly $100-$200** — same model.
- **Chinese CLI test budget** — Kimi K2, DeepSeek, Qwen3-Coder paid tiers for benchmark + production-shape testing. Output of this becomes the Wave 1b prioritization (which model gets the default-on Lane B slot for the starter tier).

The pattern: founder pays top-tier on the BYOK-only CLIs (Claude, Codex, Gemini, Higgsfield) and uses those personally + for dogfooding the platform. Tenants either bring their own keys (production path) or get a starter-tier with Lane B (open-weight) models on Alpha's metered account (legal-resale path). The two patterns coexist cleanly because they live in different `cli_executors/` and different MCP-source registrations.

## Open questions before Wave 1

1. **Which open-weight models should Alpha host directly vs route to via Fireworks/Together/Groq?** Self-hosting DeepSeek on a single GPU (~$2k/mo all-in) is borderline at low scale; using a resale-licensed inference provider (Fireworks does resale for DeepSeek) is cheaper until ~50 paying tenants. Decision needed.
2. **Default routing policy.** When a new starter-plan tenant sends a chat turn, which Lane B CLI gets the call? Probably Qwen Code as default given mindshare + Apache 2.0 + most-mature CLI surface.
3. **Plan tier names.** "Open-weights starter" vs "BYOK pro" vs "Enterprise (BYOK + on-prem)". Three tiers covers the spectrum; product naming TBD.
4. **Subscription arbitrage UI signal.** When Alpha routes a turn to an open-weight model on the tenant's account, the InlineCliPicker should show that the call is on the platform-included tier (not a BYOK provider). Small UX work in the picker.

## Risks

- **Chinese model API access from outside China.** DeepSeek + Qwen + GLM official APIs are mostly accessible globally, but some Doubao / MiniMax routes are region-locked. If Alpha hosts the inference, this disappears. If Alpha routes to upstream, we need a fallback per model.
- **Higgsfield resale terms.** Their site advertises "MCP & CLI" but doesn't publish commercial terms publicly. Before wiring as a default, get explicit confirmation that BYO-customer-key use is permitted.
- **Lane B perception.** Some enterprise tenants will distrust Chinese-trained models for data residency reasons. Plan tier defaults should honor this — Western OSS (Llama 4, Mistral) as the Lane B default for enterprise tier, Chinese OSS for the starter tier.

## Next step

User signs off on the Wave 1 sequence + confirms what "Obsidia" was, then I open the implementation PR for Qwen Code first as a reference executor.
