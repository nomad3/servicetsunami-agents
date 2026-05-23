# Modern Animal / Herriot / Sierra AI — Research Brief

**Date:** 2026-05-09
**Author:** Research session for Pet Health Concierge agent (Animal Doctor SOC tenant `7f632730-1a38-41f1-9f99-508d696dbcf1`)
**Why:** Dr. Angelo Castillo cited Modern Animal's "Harriet" agent (correct spelling: **Herriot**, after James Herriot the veterinary author) as the explicit benchmark for the independent-practice equivalent we're building. This doc grounds the Pet Health Concierge persona prompt and roadmap in what's actually shipped and what's claimed.

> **Naming note.** The discovery transcript and our internal docs used "Harriet." Public Modern Animal / Sierra material consistently uses **Herriot**. Persona prompts and roadmap docs should switch to "Herriot" when referencing the competitor.

---

## 1. Modern Animal — the company

### Founders, funding, footprint

- Founded in 2018 by **Steven Eidelman** (CEO) and Ben Jacobs in Culver City, CA. Eidelman previously sold the dog-collar company Whistle for $119M in 2016. [Crunchbase / Fortune] [1][2]
- Funding ladder (cumulative ≈ $229M per Crunchbase [1] as of March 2024, plus the Series D in Sept 2025; Fortune does not disclose a cumulative total directly):
  - Seed (Oct 2019): $13.5M, Founders Fund led
  - Series A + B (2020–21): $75M
  - Series C (Aug 2022): $75M
  - Series D (Sep 2025): $46M, led by Addition with Upfront, True Ventures, Founders Fund [Fortune] [2]
- **Footprint at acquisition:** 29 owned clinics across CA, TX, CO; ~100,000 member families; ≥60,000 pets served as of May 2024. [Chewy IR / Modern Animal blog] [3][4]
- **Chewy acquisition** announced Apr 2026: adds **>$125M ARR**, takes Chewy Vet Care from 18 → 47 locations. EBITDA-neutral 2026 pro forma, contribution starting 2027. Closing Q2 FY2026. Chewy explicitly cites the **"technology-forward platform"** and **24/7 virtual care** as core to the deal rationale, not just locations. [Chewy IR / BusinessWire] [3][5]

### Product surface

Modern Animal is built as a vertically integrated stack — clinics + membership + proprietary software — explicitly modeled as "tech-first vet, not a vet that bought tech." Their first hire on day one was Head of Engineering. [Slack customer story / Modern Animal blog] [6][7]

- **Membership tiers** (CA/CO pricing): Essential $99/yr, All Access $199/yr; pay-as-you-go $85/exam. All Access includes unlimited in-clinic visits + 24/7 chat/phone/video virtual care. [help.modernanimal.com] [8]
- **24/7 Virtual Care:** chat-first, phone, or video with the Modern Animal Care team. Constrained by VCPR rules — without an established Veterinarian-Client-Patient Relationship, the team "can't diagnose or prescribe" but can answer questions and triage. [modernanimal.com/services/24-7-virtual-care] [9]
- **Member app:** 4.9★ on App Store with 4,000+ reviews. Long-term-member criticism centers on growth-related operational gaps: medication refill misses, communication breaks between teams, intermittent app bugs. [App Store / Glassdoor / Trustpilot] [10]

### Internal tech stack — what's known

- **Proprietary EMR / PMS named "Claude"** (no relation to Anthropic's model — the name predates Anthropic's product or is coincidental). Connects clinic teams, the Virtual Care Center, and the consumer app inside one platform. [PR Newswire / dvm360] [4][11]
- May 2024 announcement bolted three AI features into Claude: **Patient Overview** (instant medical-history summaries pre-visit), **Visit Summarization** (vet-reviewed client-friendly summaries), **Lab Work Interpretation** (preliminary diagnostic analysis). Claimed savings: **~2 hours per doctor per day**. [PR Newswire] [4]
- Engineering org distributed across US / Poland / Argentina; standard SaaS toolchain (GitHub, Jira, Notion, Slack-as-coordination-layer). [Slack] [6]

### What they bought / what Chewy bought

The Chewy deal isn't about 29 locations (Chewy could have built those). It's about the integrated **member app + Claude PMS + 24/7 virtual care + Herriot agent** as a *retention engine* that 3-4× a member's annual revenue contribution vs. transactional pet-food customers. Chewy's investor deck explicitly forecasts a **15–20% net-sales-per-customer uplift** by cross-pollinating Modern Animal members into commerce. [Chewy IR] [3]

---

## 2. Herriot — the AI agent

### What it is

Modern Animal's pet-owner-facing AI agent, launched ~April 2026, **powered by Sierra**. Branded after James Herriot, the British vet whose memoirs (*All Creatures Great and Small*) are the cultural anchor for "the kindly old country vet" tone Modern Animal wants. [Sierra customer page] [12]

### What it actually does (from public material)

- **Triages symptoms** for pet owners
- **Answers routine questions** (presumably nutrition, behavior, basic post-op, medication side effects — Modern Animal hasn't published a full taxonomy)
- **Gathers context** before a human-team handoff (history, current symptoms, photos)
- **Routes to a human** care-team member when escalation is warranted
- **Channels:** in-app chat is the disclosed channel; voice/SMS not explicitly mentioned in Sierra's case study but is technically supported by Sierra Agent OS [12][13]

### Disclosed metrics

- "What used to take up to 30 minutes now happens in seconds" — i.e. shifts from a **human-first chat queue with up-to-1-hour SLA** [9] to AI-first instant response.
- **~30% of cases** are now handled end-to-end without requiring care-team involvement. [12]

### Grounding & medical-record awareness — what's actually disclosed vs. inferred

| Area | Public claim | Inference |
|---|---|---|
| Medical record access | Not disclosed publicly | **Inferred:** Herriot reads from Claude (their proprietary EMR), since both are owned by Modern Animal and Sierra explicitly says agents "integrate with… EHR, PMS, and CRM" [13]. The Sierra case study calls Herriot "clinically informed" — this requires record access, not just a knowledge base. |
| Identity / VCPR check | Not disclosed | **Inferred:** Members log into the app, so identity = authenticated session, and VCPR status is a Claude-side flag the agent likely consults to decide what answers it can give. |
| Disclaimer / "not a vet" | Not detailed in public material | **Inferred:** The 24/7 Virtual Care KB article spells out VCPR limits ("can't diagnose or prescribe") [9]. Herriot almost certainly inherits and surfaces that constraint. |
| LLM model | Not disclosed | **Inferred:** Sierra is provider-agnostic and routes to multiple frontier LLMs depending on use case; they've published τ-bench results testing GPT-4o and others [14][15]. Modern Animal almost certainly does not pick a model. |

### Pricing / availability

- Bundled into Modern Animal membership ($99–199/yr). Not a standalone SKU.
- **Corporate-only.** Independent practices cannot buy Herriot.
- This is the gap Dr. Castillo cited and the Pet Health Concierge agent fills.

### Member sentiment

The *general* Modern Animal app holds 4.9★, but reviews predate Herriot's broad rollout. Critical reviews (Trustpilot, Glassdoor) flag **"information doesn't transfer well between teams"** and **"medication refills missed"** as the top complaints — both are exactly the failure modes a record-aware agent should solve. Whether Herriot has actually closed those gaps is not yet measurable from public data. [10]

---

## 3. Sierra AI — the platform

### Company snapshot

- Founded by **Bret Taylor** (Salesforce co-CEO, Quip, CTO Facebook, co-creator of Google Maps) and **Clay Bavor** (18 yrs at Google, Google Labs, Project Starline). [Sierra About] [16]
- Founded Feb 2024; **$100M ARR in 7 quarters** (achieved Nov 2025); current valuation **$10B** on a $350M Greenoaks-led round (Sep 2025). [TechCrunch] [17]
- Customer roster spans regulated and consumer brands. Sierra's own About page renders only a small set of logos directly (CLEAR, Casper, Minted); the broader roster is reconstructed from the Sierra $100M-ARR blog post and TechCrunch coverage: **healthcare** (Sutter Health, Cigna, Clover Health, WeightWatchers, Cedar, Curative, Thriveworks), **fintech** (SoFi, Ramp, Rocket Mortgage, Prudential), **commerce** (Vans, Bissell, Casper, Minted, Wayfair, Sonos), **media** (DIRECTV, SiriusXM, Tubi, Discord), **mobility** (Rivian, Deliveroo). TechCrunch directly attributes Deliveroo, Discord, Ramp, Rivian, SoFi, Tubi, ADT, Bissell, Vans, Cigna, SiriusXM. Modern Animal is the only veterinary-vertical customer publicly listed. [Sierra $100M ARR blog, TechCrunch] [18][17]

### Product architecture (as publicly disclosed)

Sierra calls its platform **Agent OS** (v2.0 announced Nov 2025). Layers, in their language: [Agent OS 2.0 blog] [19]

1. **Agent Studio 2.0** — natural-language "Journeys" authoring. Non-engineers configure agent behavior; declarative.
2. **Agent SDK** — code-first for engineers. Express customer journeys as code, version-control them, attach deterministic guardrails ("orders can only be returned within 30 days"). [Develop Your Agent] [20]
3. **Agent Data Platform (ADP)** — memory layer. Unifies unstructured conversation data with structured systems (billing, EHR, CRM). This is the closest analog to AgentProvision's memory-first design.
4. **Integrations** — typed connectors into EHR/PMS/CRM/order-management. Not generic HTTP; integration-specific contracts. *(Sierra describes "integrations" but the Agent OS 2.0 launch announcement does not brand it as a discrete named product surface, and the related RAG-over-policy-docs pattern is described in their material but is not branded as a discrete "Knowledge engine" layer either — keeping it as a general capability rather than a named layer.)*
6. **Insights 2.0 / Explorer** — analytics + interaction-replay.
7. **Live Assist** — human-in-the-loop, hands the conversation off cleanly with a structured summary.

### The deterministic-vs-probabilistic story

This is the load-bearing claim. From Clay Bavor's Sequoia *Training Data* podcast: *"LLMs are non-deterministic"* — so the SDK gives engineers explicit deterministic guardrails the agent **cannot cross**, regardless of what the underlying LLM "wants" to do. Combined with: [21]

- **Conversation simulation** as a first-class testing primitive. Every annotated production conversation in the Experience Manager can be turned into a regression test that runs against mock APIs.
- **τ-bench / τ²-bench / τ³-bench** — Sierra's own published benchmark for tool-using agents. State-of-the-art LLMs only solve <50% of `τ-retail` tasks and pass^8 < 25% — i.e. raw LLMs aren't reliable, the surrounding architecture is what makes Sierra agents production-grade. [τ-bench paper, sierra-research GitHub] [14][15]

### Pricing & deployment

- **Outcomes-based pricing** — customers pay per resolved interaction, not per seat or per token. [TechCrunch] [17]
- **8–16 week deployment** including brand-voice training, integration buildout, eval. [Fini Labs comparison] [22]
- **Compliance posture:** SOC 2, HIPAA, GDPR, CCPA. PII encrypted and masked. No cross-tenant model training. [PixieBrix / Sierra Healthcare] [22][23]

### Why Sierra is the closest published analog to AgentProvision

| AgentProvision concept | Sierra equivalent |
|---|---|
| Memory-first design (`apps/api/app/memory/`) | Agent Data Platform |
| Dynamic Workflows (JSON DSL + Agent Studio Journeys) | Agent Studio 2.0 |
| MCP tool registry (90+) | Sierra "systems integrations" + knowledge engine |
| Auto Quality Scoring (Gemma 4 council) | Insights 2.0 + τ-bench style simulation |
| RL experience log + policy engine | Not directly disclosed by Sierra — closest is Insights "Explorer" + simulation regression |
| Multi-CLI runtime + autodetect/quota fallback | Not disclosed; Sierra owns provider choice silently |
| Agent Lifecycle Management (versioning, rollback, audit) | Workspaces + Agent SDK version control |

The architectural shapes are remarkably aligned. Two real differences:
1. **Sierra is closed/managed; we are extensible/multi-tenant/BYOL.** Sierra picks the LLM, customer doesn't see it. AgentProvision routes across Claude / Gemini / Codex / Copilot via tenant-owned OAuth — the explicit anti-Sierra move.
2. **Sierra's eval story is more mature.** τ-bench, conversation simulation as a regression primitive, mock-database deterministic tests. We have the Gemma 4 council and RL experiences, but no equivalent to "snapshot a customer conversation, run it against mock APIs, fail the build if behavior regresses."

---

## 4. What to copy / what to leapfrog — Pet Health Concierge action list

### Copy as-is (proven UX patterns we should match)

1. **First-response SLA framing.** Herriot's win is "30 minutes → seconds." Lead with that promise on the widget. Latency is the differentiated UX, not answer quality.
2. **30%-handled metric in onboarding pitch.** Set Dr. Castillo's expectation: ~30% deflection without vet involvement is the realistic win, not 100%. Anchors the value to a defensible, Sierra-validated number.
3. **VCPR-aware refusal.** Herriot doesn't try to diagnose without an established VCPR. Pet Health Concierge needs the same gate, sourced from a Pulse-side flag (or a tenant-config default if no VCPR data is exposed). Bake into the persona prompt: *"Never recommend or imply diagnosis or prescription absent VCPR. If VCPR=false, triage and book; if VCPR=true, expand to symptom guidance."*
4. **Human-handoff with structured summary.** Sierra's "Live Assist" hands conversation to humans with a pre-baked summary — staff don't re-read 40 messages. We have this scaffold (collaboration / blackboard); make sure the handoff payload from Pet Health Concierge to a clinic staff member is a one-screen summary, not a chat dump.
5. **24/7 framing as a *practice* feature, not just a chat widget.** Modern Animal members buy a relationship; the chat is the surface. Pet Health Concierge should be sold as "your practice has 24/7 coverage now" — that's what a VMG owner is paying for, not a chatbot.

### Leapfrog (where independent practices can win against Modern Animal corporate)

1. **Multi-PMS, not proprietary EMR.** Modern Animal grounds Herriot in *Claude (their EMR)* — possible only because they own it. We ground Pet Health Concierge in **Covetrus Pulse** (and later Avimark, ezyVet, etc.) — the PMS 2,000 VMG owners already use. This is the moat. Castillo doesn't switch PMS; he lets us read his existing one.
2. **Multi-location, single-agent, location-aware.** Modern Animal solves this by being one corporate brand. Independent owners with 3+ hospitals (Castillo) want one agent that knows which hospital the pet belongs to and routes accordingly. Filter Pulse queries by `location_id` (already designed in `Multi-Site Revenue Sync` workflow). Bake location-awareness into the persona prompt.
3. **Independent-practice brand voice.** Herriot is corporate-friendly, polished, slightly homogenized. Independent practices win on personality — "Dr. Castillo's clinic, but available 24/7" beats "Modern Animal corporate." Persona prompt should pull the practice's actual brand voice (tenant_branding) and use the lead vet's name, not a generic mascot.
4. **iMessage/SMS as a peer channel, not just an app.** Modern Animal's Herriot is locked into the member app (no app, no agent). US pet owners *expect* SMS for routine clinic communication. Twilio iMessage/SMS integration (queued in `2026-05-09-imessage-sms-integration.md`) is a structural advantage — Modern Animal cannot match it without rebuilding their lock-in moat.
5. **Outcome-based pricing in vet vertical.** Sierra invented outcomes-based for B2B SaaS. Independent vets are sole-prop businesses — they understand "$X per resolved triage" much better than seat licensing. This was already raised in the Castillo discovery; structurally lift Sierra's pricing model into the VMG offer.

### Match within ~1 quarter (table-stakes Sierra-style infrastructure)

1. **Conversation-replay regression eval.** Capture every Pet Health Concierge production conversation, let staff annotate the "correct" outcome, and run it as a regression test against mock Pulse APIs. This is τ-bench in miniature. The auto quality scorer (Gemma 4) gives us the score; we're missing the **mock-API harness** and the **annotation UI**. ALM platform's `agent_versions` + audit log is the substrate. Build on top.
2. **Deterministic guardrails as code.** Today the Pet Health Concierge persona prompt is a long string. Sierra's lesson: hard rules ("never prescribe", "always check VCPR", "always offer to book if symptom severity > X") belong in code, not in prose the LLM might paraphrase. Build the Concierge guardrails as Value Arbitration `tenant_norm` signals (see `docs/plans/2026-05-23-value-arbitration-design.md`) — "never prescribe" → `standing=tenant_norm, direction=veto, target=ResponseAction(suggests_prescription=True)`; "always check VCPR" → `standing=tenant_norm, direction=pursue, target=ToolAction(name=check_vcpr_status)`. ValueArbitration handles the cross-class reconciliation (e.g., "always check VCPR" vs a user pressuring for a fast response) and emits a reasoned audit trace. *(Original recommendation was to lift `agent_policies` migration 097 — that table was deleted in P0b 2026-05-23 after zero production usage across 42 tenants; ValueArbitration is the long-term home.)*
3. **Memory grounding parity with ADP.** Sierra's ADP unifies *conversation memory* + *systems-of-record*. Our `apps/api/app/memory/` (Phase 1 shipped) does conversation memory; the Pulse PMS is the systems-of-record side. The integration point is the recall hook that pre-loads pet/owner/visit context into the persona prompt before each turn. Verify Pet Health Concierge's recall path includes Pulse-driven knowledge entities (vaccines, last visit, current Rx) — not only conversation history.

### Three product moves to ship this week (concrete)

1. **Persona prompt update — VCPR-aware refusal block.** Add the Sierra-style hard guardrail to Pet Health Concierge's persona at the top, not the bottom. Reference `tenant_branding.lead_vet_name` and `pulse.vcpr_status` (placeholder until Pulse API research lands). Owner: persona-tuning sub-agent.
2. **Onboarding deck pitch correction.** Update the VMG / Castillo demo deck to use **"Herriot"** (not "Harriet") and lead with the **"30 minutes → seconds, ~30% deflection"** framing as the explicit benchmark we match-or-beat. Owner: GTM / Simon.
3. **Conversation-replay test harness — design spike.** Open a `docs/plans/2026-05-09-conversation-replay-eval-design.md` plan. Reuse `chat_messages` + `agent_versions` + `auto_quality_scorer` to ship a "snapshot a chat → re-run on the new agent version → score delta" loop. Sierra's τ-bench is the architectural reference. Owner: a planning sub-agent next session.

---

## 5. Honest gaps — what we couldn't determine

- **Herriot's exact LLM provider.** Sierra is provider-agnostic; not disclosed per-customer. Reasonable bets: Claude / GPT-4o-class / Gemini, possibly mixed.
- **Herriot's exact data model into "Claude" the EMR.** No public schema, no partner API. We're inferring "EHR/PMS integration" because Sierra's healthcare positioning explicitly lists it [13] and Modern Animal owns both ends.
- **Herriot's safety eval / clinical review process.** Modern Animal claims doctor review of Visit Summarization output [4]; whether Herriot's pet-owner-facing replies have a similar pre-send review is not disclosed. **Inference:** they likely do not — the "30 minutes → seconds" claim is incompatible with synchronous human review. Rely on policy guardrails + post-hoc audit instead.
- **Modern Animal's actual member NPS post-Herriot.** No published Herriot-specific data; existing Trustpilot reviews predate the launch.
- **Sierra's veterinary-vertical roadmap.** Modern Animal is the only public vet customer. Whether Sierra is courting Banfield, BluePearl, VCA — unknown. If they are, the independent-practice window narrows; if they aren't (B2B2C corporate-only is their pattern), VMG is wide open.
- **Whether Chewy plans to expose Modern Animal's tech (including Herriot) to Chewy Vet Care's other 18 locations or to its e-commerce members.** Chewy IR language suggests yes, but timing is post-2026 close.

---

## 6. Sources

1. [Crunchbase — Modern Animal Company Profile](https://www.crunchbase.com/organization/modern-animal)
2. [Fortune — Modern Animal raises $46M Series D](https://fortune.com/2025/09/16/exclusive-modern-animal-veterinary-clinic-network-raises-46-million-series-d/)
3. [Chewy Investor Relations — Chewy to Acquire Modern Animal](https://investor.chewy.com/news-and-events/news/news-details/2026/Chewy-to-Acquire-Modern-Animal-Accelerating-Evolution-to-a-Fully-Integrated-Healthcare-Ecosystem/default.aspx)
4. [PR Newswire — Modern Animal Unveils AI-Assisted Tools](https://www.prnewswire.com/news-releases/modern-animal-unveils-ai-assisted-tools-roadmap-for-next-generation-of-veterinary-care-302153167.html)
5. [BusinessWire — Chewy to Acquire Modern Animal](https://www.businesswire.com/news/home/20260408567488/en/Chewy-to-Acquire-Modern-Animal-Accelerating-Evolution-to-a-Fully-Integrated-Healthcare-Ecosystem)
6. [Slack Customer Story — Modern Animal](https://slack.com/customer-stories/modern-animal-revolutionizes-vet-industry)
7. [Modern Animal blog — Building from Scratch](https://blog.modernanimal.com/modern-animal-startup-to-storefront/)
8. [Modern Animal Help Center — chat-message expectations](https://help.modernanimal.com/kb/article/91-how-does-virtual-care-work-alongside-the-in-clinic-experience/)
9. [Modern Animal — 24/7 Virtual Care service page](https://www.modernanimal.com/services/24-7-virtual-care)
10. [Trustpilot — Modern Animal customer reviews](https://www.trustpilot.com/review/www.modernanimal.com)
11. [dvm360 — Streamlining administrative tasks with AI tools](https://www.dvm360.com/view/streamlining-administrative-tasks-with-ai-tools)
12. [Sierra Customer Story — Modern Animal / Herriot](https://sierra.ai/customers/modern-animal)
13. [Sierra — Healthcare industry page](https://sierra.ai/industries/healthcare)
14. [τ-bench paper (arXiv 2406.12045)](https://arxiv.org/abs/2406.12045)
15. [sierra-research/tau-bench on GitHub](https://github.com/sierra-research/tau-bench)
16. [Sierra — About](https://sierra.ai/about)
17. [TechCrunch — Sierra reaches $100M ARR](https://techcrunch.com/2025/11/21/bret-taylors-sierra-reaches-100m-arr-in-under-two-years/)
18. [Sierra blog — $100M ARR milestone](https://sierra.ai/blog/100m-arr)
19. [Sierra blog — Agent OS 2.0](https://sierra.ai/blog/agent-os-2-0)
20. [Sierra — Develop your agent (Agent SDK)](https://sierra.ai/product/develop-your-agent)
21. [Sequoia *Training Data* podcast — Clay Bavor on Sierra](https://sequoiacap.com/podcast/training-data-clay-bavor/)
22. [Fini Labs — AI support platforms for regulated industries](https://www.usefini.com/guides/ai-support-platforms-regulated-industries-compliance)
23. [PixieBrix — Sierra AI features and alternatives](https://www.pixiebrix.com/tool/sierra)
