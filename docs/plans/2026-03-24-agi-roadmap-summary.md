# AGI Roadmap — Gap Summary & Prioritization

**Date**: 2026-03-24
**Status**: Design

## The Six Gaps

| Priority | Gap | Document | Key Deliverable | Builds On |
|----------|-----|----------|----------------|-----------|
| **1** | Safety & Trust | Gap 05 | Unified risk taxonomy, action policy engine | Consensus resilience, provider council |
| **2** | Self-Model & Goals | Gap 02 | Goal persistence, commitment tracking | Memory system, agent fleet |
| **3** | World Model | Gap 01 | Assertion layer, state projections | Knowledge graph, observations |
| **4** | Long-Horizon Planning | Gap 03 | Plan runtime, replanning engine | Dynamic workflows, CLI orchestration |
| **5** | Society of Agents | Gap 06 | Shared blackboard, coalition formation | Distributed protocol, consensus |
| **6** | Self-Improvement | Gap 04 | Learning control plane, experiment framework | RL framework, learned routing |

## Why This Order

**Gap 05 (Safety) first**: More autonomy without governance is a liability. The pre-execution safety gate we already built is a partial implementation. Completing the risk taxonomy gives every subsequent gap a trust foundation.

**Gap 02 (Goals) second**: Highest immediate ROI — goal and commitment persistence leverages existing memory infrastructure and directly improves user experience. Agents that remember what they committed to are more useful than agents that plan better.

**Gap 01 (World Model) third**: Transforms raw observations (4,823 existing) into structured state. Enables Gaps 3-6 to operate on reliable state instead of raw memory dumps.

**Gap 04 (Self-Improvement) last**: Requires the most accumulated data and infrastructure. The exploration mode (currently routing 90% to Codex) is already collecting the training data this gap needs. Let it run while building Gaps 1-3.

## Current Platform Foundation

These existing capabilities underpin the roadmap:

- 2,844 RL experiences with embeddings (100% coverage after backfill)
- Multi-provider review council (Claude + Codex + Qwen, verified E2E)
- Learned routing with RL feedback loop
- Exploration mode for training data collection
- Inference bulkhead (foreground/background isolation)
- Pre-execution safety gate (read-only local model)
- 81 MCP tools, knowledge graph, Temporal workflows
