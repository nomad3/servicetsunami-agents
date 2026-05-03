# AgentProvision: Enterprise Agentic Orchestration Architecture

**Date:** 2026-04-11 (updated)
**Subject:** Technical Architecture & Protocol Guarantees
**Prepared for:** Levi Strauss & Co. Engineering Leadership

---

## 1. Executive Summary

AgentProvision is a **memory-first, workflow-orchestrated agent platform** designed for enterprise data sovereignty. Unlike standard chatbot wrappers, it treats memory and durable execution as first-class architectural pillars, enabling multi-agent teams to collaborate on long-horizon tasks with full auditability and cross-turn continuity.

---

## 2. The Three Pillars of Architecture

### 2.1. Memory-First Substrate
We employ a three-layer memory model that ensures agents always have the right context without expensive, manual "recall tool" calls.

```ascii
+-----------------------------------------------------------------------+
|                          MEMORY LAYER (Unified)                       |
+-----------------------------------------------------------------------+
|  WORKING MEMORY  |  EPISODIC MEMORY      |  SEMANTIC KNOWLEDGE        |
| (Context Window) | (Summarized Events)   | (Entities & Observations)  |
+------------------+-----------------------+----------------------------+
| Last 20-30 msgs  | Rolling conversation  | Knowledge Graph (Postgres) |
| (Live/Hot path)  | episodes (Temporal)   | Vector Search (pgvector)   |
+------------------+-----------------------+----------------------------+
         ^                    ^                       ^
         |                    |                       |
         +----------+---------+-----------------------+
                    |
             RECALL API (gRPC)
                    |
         +----------+----------+
         |   AGENT RUNTIME     |
         | (Gemini / Claude)   |
         +---------------------+
```

*   **Pillar Guarantee:** Every turn begins with a pre-loaded "Recall" operation. The agent sees relevant entities (e.g., "Levi's SRE Team"), past commitments, and related conversation snippets before processing the user's input.

### 2.2. Durable Workflow Engine (Temporal)
All complex operations (handoffs, multi-step integrations, async processing) are implemented as **Temporal Workflows**.

```ascii
[ User Action ] --> [ API ] --> [ Workflow Dispatch ]
                                       |
                                       v
                    +---------------------------------------+
                    |       TEMPORAL CLUSTER (Durable)      |
                    +---------------------------------------+
                    | - Retries with Exp. Backoff           |
                    | - State Persistence (Event Sourcing)  |
                    | - Timeout Management                  |
                    +---------------------------------------+
                               /       |       \
                    [ Ingestion ]  [ Memory ]  [ Business ]
                      Workers       Workers      Workers
```

*   **Pillar Guarantee:** Workflows are resilient to process crashes, network failures, and downstream API timeouts. If a task is started, it is guaranteed to run to completion or fail with a clear, auditable trace.

### 2.3. Reinforcement Learning (RWES)
The platform uses a **Reward-Weighted Experience Store (RWES)** to optimize routing and decision-making.

*   **Policy Engine:** Decisions (which agent to use, which tool to call) are ranked based on past success.
*   **Feedback Loop:** Explicit (user thumbs up) and implicit (task completion) signals update the policy nightly.
*   **Explainability:** Every "smart" decision includes an `explanation` block in the metadata, showing exactly why a specific path was chosen.

---

## 3. Agent-to-Agent (A2A) Protocol Guarantees

AgentProvision solves the "context loss" and "handoff reliability" problems common in multi-agent systems through a formal orchestration protocol.

### 3.1. Shared Blackboard Architecture
Handoffs are not just "message passing." We use a **Shared Blackboard** model where multiple agents read from and write to a common state-space.

```ascii
   AGENT A (Planner)        AGENT B (Critic)        AGENT C (Coder)
         |                      |                       |
         +----------+-----------+-----------+-----------+
                    |                       |
                    v                       v
        +-------------------------------------------------------+
        |                SHARED BLACKBOARD (Postgres)           |
        +-------------------------------------------------------+
        | - Proposed Plan                                       |
        | - Critiques & Evidence                                |
        | - Current Status (In-Progress / Verified)             |
        | - Reference Entities (Knowledge Graph Pointers)       |
        +-------------------------------------------------------+
```

### 3.2. Collaboration Patterns (State Machines)
The platform enforces strict collaboration patterns. A handoff is a state transition in a formal machine:

*   **Propose-Critique-Revise:** Agent A proposes, Agent B critiques, Agent A revises until a Consensus threshold is met.
*   **Plan-Verify:** One agent plans, another verifies against policy/security rules before execution.
*   **Research-Synthesize:** Distributed agents gather raw data; a supervisor synthesizes the final result.

### 3.3. Protocol Guarantees:
1.  **Atomicity:** A handoff only "completes" when the successor agent heartbeats into the session.
2.  **Shared Context:** The successor agent automatically inherits the full `Recall` state of the predecessor.
3.  **Traceability:** Every transition is recorded in the `execution_traces` table, including the specific reasoning provided by each agent during the handoff.

---

## 4. Security & Governance

### 4.1. Tenant Isolation
*   **Data Hard Boundary:** Every database query and vector search includes a mandatory `tenant_id` filter at the repository level.
*   **No Cross-Leakage:** Cross-tenant memory access is physically impossible by schema design.

### 4.2. OAuth & Credential Lifecycle
*   **Identity Provisioning:** Users connect via standard OAuth2 (Google, GitHub, etc.).
*   **Automatic Refresh:** The `CredentialVault` manages token TTL and refreshes tokens automatically before workflow execution.
*   **Short-Lived Access:** Tokens are injected into the agent's environment only for the duration of the specific activity execution and are never stored in plain text in logs.

### 4.3. Deployment & Hosting
*   **Kubernetes-Native:** The entire stack runs on Kubernetes (Rancher Desktop locally, any K8s cluster for enterprise). 11 pods: API, Web, Luna-Client, Code-Worker, Orchestration-Worker, Embedding-Service (Rust), Memory-Core (Rust), PostgreSQL, Redis, Temporal, Cloudflared.
*   **Helm-Based:** Single reusable Helm chart (`microservice`) with per-service values files. One `helm install` deploys the full platform.
*   **Cloudflare Tunnel (In-Cluster):** Runs as a K8s pod routing `agentprovision.com` to internal services by DNS name. No port-forwards or external load balancer needed.
*   **On-Premise Ready:** Each customer runs their own K8s cluster. Data stays in-cluster. No egress except the tunnel endpoint.
*   **GitOps:** Pushes to main auto-deploy via GitHub Actions to the self-hosted runner.

---

## 5. Core Capabilities (MCP Tools)

Agents have access to 90+ tools through the **Model Context Protocol (MCP)**:
*   **Communication:** Gmail, Slack, WhatsApp (integrated).
*   **Infrastructure:** Jira, GitHub, Jenkins, Nexus, SSH.
*   **Data:** SQL (DuckDB/Postgres), Analytics, Reports (Excel/openpyxl).
*   **Productivity:** Google Calendar, Drive, Sheets.
*   **Memory:** Knowledge search, entity extraction, lead scoring.
*   **Marketing:** Meta Ads, Google Ads, TikTok Ads, Competitor monitoring.

---

## 6. Memory-First Implementation Status

| Phase | Status | Deliverable |
|-------|--------|-------------|
| Phase 0 | Complete | gRPC IDL frozen, gold sets, baseline (p50=47s) |
| Phase 1 | **Shipped** | Python memory layer, recall/record/ingest, PostChatMemoryWorkflow, Gemma4 commitment classifier, entity extraction |
| Phase 2 | **In validation** | Rust embedding-service (fastembed/ONNX), memory-core (gRPC), dual-read comparison |
| Phase 3a | **Shipped** | K8s migration (Rancher Desktop), Helm charts, Cloudflare in-cluster, CI/CD |
| Phase 3b | Planned | Email/Calendar/Jira/GitHub source ingesters |
| Phase 4 | Planned | Rust federation daemon, cluster-to-cluster mesh |

**Performance (as of 2026-04-10):**
| Metric | Pre-Platform | Current | Target (Phase 3a) |
|--------|-------------|---------|-------------------|
| Chat p50 | 47.1s | **5.5s** | <2s |
| API endpoints | unmeasured | **80ms** | <200ms |
| Memory recall | 2-5s | **<200ms** | <500ms |
| Timeouts | 5% | **0%** | 0% |

---

## 7. Technical Stack
*   **Backend:** Python 3.11 (FastAPI), Rust (embedding-service via fastembed/ONNX, memory-core via tonic gRPC + sqlx).
*   **Database:** PostgreSQL 13 + pgvector (768-dim embeddings). 90+ SQL migrations.
*   **Orchestration:** Temporal.io (4 task queues: orchestration, postgres, code, business).
*   **Inference:** Claude Code CLI / Codex CLI / Gemini CLI / GitHub Copilot CLI (per-tenant OAuth, autodetect + quota fallback chain) + Gemma 4 via Ollama (local, zero cloud cost for scoring, summarization, extraction, triage).
*   **Embeddings:** nomic-embed-text-v1.5 (768-dim). Rust path: fastembed + ONNX Runtime. Python fallback: sentence-transformers.
*   **Infrastructure:** Kubernetes (Rancher Desktop), Helm, Cloudflare Tunnel, GitHub Actions CI/CD.

---

**Next Steps:**
We invite the Security and Architecture teams to a technical deep-dive where we can demonstrate the Temporal event traces, the Shared Blackboard transitions, the pgvector search isolation, and the new K8s deployment model in real-time.
