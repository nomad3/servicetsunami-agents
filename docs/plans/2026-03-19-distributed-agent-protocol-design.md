# ServiceTsunami Protocol (STP) — Distributed Agent Network

> An open protocol where AI agents are owned digital assets that earn revenue, running on a distributed compute network powered by anyone's hardware and LLM subscriptions.

**Date:** 2026-03-19
**Status:** Design
**Author:** Simon Aguilera + Claude

---

## 1. Problem Statement

AI agent platforms today are either:
- **Centralized SaaS** — expensive cloud infra, single point of failure, race-to-bottom pricing
- **Open source frameworks** — no monetization for creators, no compute network, self-host or die
- **API credit models** — per-token billing punishes usage, developers eat cost

Meanwhile, millions of people pay for LLM subscriptions (Claude Pro/Max, ChatGPT Plus, Gemini Pro) that give them effectively unlimited tokens — but they use a fraction of that capacity.

**The opportunity:** Create a marketplace where subscription holders monetize their unused capacity by running AI agents for others.

---

## 2. Vision

ServiceTsunami Protocol is a **B2B2C** network:

```
Protocol (ServiceTsunami)
    ↕
Operators (hardware + LLM subscriptions)
    ↕
End Users (businesses + individuals who need AI agents)
```

Three actors, each bringing a different resource:

| Actor | Contributes | Earns |
|-------|------------|-------|
| **Creators** | Agent intelligence, skills, tools | 70% of every execution |
| **Operators** | Hardware + LLM subscriptions | 20% compute fee |
| **Users** | Tasks + credits | Results at a fraction of subscription cost |

**The flywheel:**
1. Creators publish agents → agents run on operator nodes → users pay per task
2. RL feedback improves agent quality → better agents get more traffic → more revenue for creators
3. More operators join → more capacity → lower prices → more users
4. More users → more revenue → more operators + creators join

---

## 3. Business Model: Subscription Arbitrage

### The Economics

| | API Credits | Subscription | STP Network |
|---|---|---|---|
| Claude Opus session | ~$5 per heavy task | ~$0.40 amortized (Max plan) | ~$2 user price |
| Who pays LLM cost | User (directly) | Operator (subscription) | Operator (subscription) |
| Operator margin | N/A | N/A | $2 - $0.40 = $1.60 per task |
| User savings | 0% | Must buy own sub | 60% vs API credits |

### Revenue Split Per Task

| Complexity | User Pays | Creator (70%) | Operator (20%) | Protocol (10%) |
|-----------|----------|---------------|----------------|----------------|
| Simple (chat, lookup) | $0.10 | $0.07 | $0.02 | $0.01 |
| Medium (email scan, report) | $0.50 | $0.35 | $0.10 | $0.05 |
| Heavy (code PR, deep analysis) | $2.00 | $1.40 | $0.40 | $0.20 |
| Premium (multi-hour workflow) | $5.00 | $3.50 | $1.00 | $0.50 |

### Operator Economics

- Operator pays: ~$200/mo for Claude Max subscription + electricity
- Operator handles: ~500 tasks/month on a gaming laptop
- Operator earns: $250-2,000/mo depending on task mix
- Break-even: ~400 simple tasks or ~100 heavy tasks
- Hardware amortization: gaming laptop already owned, marginal cost near zero

### User Economics

- No subscription commitment — pay only for tasks used
- $50-100/mo in credits replaces a $200/mo Claude Max plan
- Access to specialized agents they didn't build
- Agents improve over time via RL (network gets smarter)

---

## 4. Architecture

### 4.1 Network Topology

```
                    ┌─────────────────────┐
                    │   Registry Service   │
                    │  (Agent catalog,     │
                    │   node directory,    │
                    │   credit ledger)     │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
     ┌────────▼───────┐ ┌─────▼──────┐ ┌───────▼────────┐
     │  Node (MacBook) │ │ Node (PC)  │ │ Node (Server)  │
     │  ┌───────────┐  │ │ ┌────────┐ │ │ ┌────────────┐ │
     │  │ Agent      │  │ │ │ Agent  │ │ │ │ Agent      │ │
     │  │ Runtime    │  │ │ │ Runtime│ │ │ │ Runtime    │ │
     │  ├───────────┤  │ │ ├────────┤ │ │ ├────────────┤ │
     │  │ MCP Tools  │  │ │ │ MCP    │ │ │ │ MCP Tools  │ │
     │  ├───────────┤  │ │ ├────────┤ │ │ ├────────────┤ │
     │  │ CLI Session│  │ │ │ CLI    │ │ │ │ CLI Session│ │
     │  │ (Claude/   │  │ │ │ Session│ │ │ │ (Claude/   │ │
     │  │  Gemini/   │  │ │ │        │ │ │ │  Gemini/   │ │
     │  │  Codex)    │  │ │ │        │ │ │ │  Codex)    │ │
     │  └───────────┘  │ │ └────────┘ │ │ └────────────┘ │
     │  LLM: Claude Max│ │ LLM: GPT+  │ │ LLM: All three │
     └─────────────────┘ └────────────┘ └────────────────┘
              │                │                │
              └────────────────┼────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │   Tailscale Mesh     │
                    │   (private network)  │
                    └─────────────────────┘
```

### 4.2 Core Components

#### A. Registry Service (Lightweight — runs on any node)

The source of truth for the network. NOT a blockchain — a replicated database using Raft consensus (same as etcd/K3s). Any node can read, leader node handles writes.

Manages:
- **Agent Registry** — catalog of published agents with metadata, version, creator, quality score
- **Node Directory** — active nodes with capabilities (OS, RAM, GPU, LLM subscriptions available)
- **Credit Ledger** — account balances, transaction history, revenue splits
- **Task Queue** — pending tasks waiting for routing

```
registry_service/
├── agent_registry.py     # Agent CRUD, versioning, search
├── node_directory.py     # Node registration, heartbeat, capabilities
├── credit_ledger.py      # Balances, transactions, splits
├── task_router.py        # RL-powered routing: task → best agent × best node
└── consensus.py          # Raft leader election, state replication
```

#### B. Node Daemon (Runs on every operator machine)

A single binary/container that:
1. Registers with the network (advertises capabilities)
2. Heartbeats every 30s (proves liveness)
3. Accepts task assignments from the router
4. Runs agents via CLI sessions (Claude/Gemini/Codex)
5. Reports results + metrics back to registry
6. Pulls agent packages on demand

```
node_daemon/
├── daemon.py             # Main loop: register, heartbeat, accept tasks
├── agent_runner.py       # Download agent package, set up CLI session, execute
├── capability_probe.py   # Detect OS, RAM, GPU, installed CLI tools, active subscriptions
├── metric_reporter.py    # Execution time, success rate, resource usage
└── config.yaml           # Operator settings: which LLMs, max concurrent tasks, pricing
```

#### C. Agent Package (What creators publish)

An agent is a self-contained package:

```
my-agent/
├── agent.yaml            # Metadata: name, version, creator, pricing tier, platform affinity
├── skill.md              # Agent instructions (existing skill format)
├── mcp-tools.json        # Required MCP tools (subset of the 81+ available)
├── config/               # Any config files the agent needs
└── SIGNATURE             # Cryptographic signature proving creator ownership
```

Agents are content-addressed (SHA-256 hash of the package). Publishing = uploading the package to the registry + signing it with the creator's key.

#### D. Task Router (RL-Powered)

The brain of the protocol. Routes tasks to the optimal (agent, node) pair.

Factors:
- **Agent quality score** — RL-derived from user feedback (existing thumbs up/down)
- **Node capability match** — does the node have the required LLM subscription?
- **Node load** — current queue depth, CPU/memory utilization
- **Latency** — geographic proximity, network RTT
- **Price** — operator's configured rate vs user's willingness to pay
- **Creator reputation** — track record of the agent creator

```python
# Simplified routing logic
def route_task(task, available_agents, available_nodes):
    candidates = []
    for agent in match_agents(task):
        for node in match_nodes(agent.requirements):
            score = (
                agent.quality_score * 0.4 +       # RL quality
                node.capability_score * 0.2 +      # Can it run this?
                (1 - node.load) * 0.2 +            # Is it busy?
                (1 / node.latency) * 0.1 +         # Is it fast?
                node.price_competitiveness * 0.1   # Is it cheap?
            )
            candidates.append((agent, node, score))
    return max(candidates, key=lambda x: x[2])
```

### 4.3 Data Architecture

#### Replicated State (via Raft consensus)

| Data | Replication | Why |
|------|------------|-----|
| Agent registry | All nodes | Everyone needs to know what agents exist |
| Node directory | All nodes | Everyone needs to know what nodes are available |
| Credit balances | All nodes | Payments must be consistent |
| RL quality scores | All nodes | Routing decisions need current scores |

#### Local State (per node, not replicated)

| Data | Location | Why |
|------|----------|-----|
| PostgreSQL tenant data | Node that owns it | Multi-tenant data stays with the operator |
| CLI session files | Executing node | Ephemeral, per-execution |
| MCP tool cache | Each node | Cached locally for performance |
| Agent package cache | Each node | Downloaded on first use |

#### Knowledge Graph Sync

The knowledge graph (pgvector entities + relations) is per-tenant. When a tenant's task runs on a different node, the node needs access to their knowledge. Options:

- **Option A: Pull on demand** — node fetches relevant entities via API before execution. Simple, but adds latency.
- **Option B: Edge caching** — popular tenants' knowledge is pre-replicated to active nodes. Better performance, more storage.
- **Recommended: Option A** for Phase 1, Option B later.

### 4.4 Security Model

#### Agent Ownership

- Creators generate an Ed25519 keypair on registration
- Publishing an agent = signing the package hash with the creator's private key
- Anyone can verify ownership by checking the signature against the creator's public key
- Transfer of ownership = creator signs a transfer message to new owner's public key

#### Task Execution Trust

- Operator nodes run in sandboxed environments (Docker containers)
- Agent packages are verified (signature check) before execution
- Results are signed by the executing node (proves who ran it)
- Users can dispute results within 24 hours (credits refunded from operator's escrow)

#### Credential Isolation

- Operator's LLM tokens (Claude OAuth, GitHub, etc.) NEVER leave their machine
- The node daemon sets environment variables locally
- The protocol never sees, stores, or transmits operator credentials
- Agent execution happens inside the operator's environment

### 4.5 Node Networking

#### Tailscale Mesh (Private Network Layer)

All nodes join a Tailscale network (free for up to 100 devices):
- Every node gets a stable IP (100.x.x.x)
- Encrypted WireGuard tunnels between all nodes
- NAT traversal works through firewalls (no port forwarding needed)
- Works from home networks, coffee shops, mobile data

#### Cloudflare Tunnel (Public Access Layer)

For user-facing traffic:
- Any healthy node can run a Cloudflare Tunnel
- DNS failover: if primary tunnel node goes down, secondary takes over
- Cloudflare load balances across multiple tunnel origins

```
Internet → Cloudflare DNS (servicetsunami.com)
    → Tunnel Origin 1 (MacBook — primary)
    → Tunnel Origin 2 (Gaming PC — secondary)
    → Tunnel Origin 3 (Server — tertiary)
```

---

## 5. User Flows

### 5.1 Operator Onboarding

```
1. Download node daemon (single binary or Docker image)
2. Run: stp node start
3. Daemon probes machine: OS, RAM, GPU, installed CLIs
4. Daemon prompts: "Found Claude Code CLI. Authenticate? (Y/n)"
5. Operator authenticates their LLM subscriptions (OAuth, already built)
6. Daemon registers with network: "Node X online, Claude Max + 32GB RAM + GPU"
7. Node starts accepting tasks. Earnings appear in dashboard.
```

### 5.2 Creator Publishing an Agent

```
1. Creator writes agent (skill.md + agent.yaml)
   - Can use ServiceTsunami web UI (existing skill editor)
   - Or write locally and push via CLI: stp agent publish ./my-agent/
2. Agent gets signed with creator's key
3. Package uploaded to registry (content-addressed)
4. Agent appears in marketplace with quality score = 0 (new)
5. Early users try it → RL feedback → quality score increases
6. Higher quality → more routing → more revenue
```

### 5.3 User Executing a Task

```
1. User sends message via web chat, WhatsApp, or API
2. Protocol identifies intent → matches to best agent(s)
3. Task router selects (agent, node) pair based on RL scores
4. Task dispatched to selected node via Temporal
5. Node downloads agent package (if not cached)
6. Node sets up CLI session with operator's LLM subscription
7. Agent executes with MCP tools
8. Result returned to user
9. Credits deducted: 70% creator, 20% operator, 10% protocol
10. User rates result (thumbs up/down) → RL update
```

### 5.4 Failover Scenario

```
1. Node A (MacBook) goes offline (lid closed)
2. Heartbeat missed → 30 seconds
3. Registry marks Node A as "suspect" → 60 seconds
4. Registry marks Node A as "offline" → 90 seconds
5. All Node A tasks re-queued → routed to Node B (Gaming PC)
6. Cloudflare Tunnel fails over to Node B
7. PostgreSQL streaming replication: Node B promotes to primary
8. Total downtime: ~90 seconds
9. Data loss: zero (synchronous replication)
10. Node A comes back → rejoins as secondary, syncs state
```

---

## 6. Technology Stack

### Node Daemon
- **Language:** Go or Rust (single binary, cross-platform: Mac, Windows, Linux)
- **Networking:** Tailscale SDK for mesh, gRPC for inter-node communication
- **Consensus:** Raft (via etcd embedded or hashicorp/raft library)
- **Agent runtime:** Docker containers or direct CLI execution

### Registry Service
- **Database:** PostgreSQL with streaming replication (CloudNativePG on K3s, or manual)
- **Vector search:** pgvector (existing, for agent/skill matching)
- **Task queue:** Temporal (existing, proven durable)
- **RL engine:** Existing RL framework (rl_experience table + policy)

### Agent Packages
- **Format:** Existing skill.md format + agent.yaml metadata
- **Storage:** Content-addressed (SHA-256), distributed across nodes
- **Signature:** Ed25519 (fast, small keys, battle-tested)

### Payments
- **Credits:** Internal ledger (PostgreSQL), buy with Stripe
- **Settlements:** Operator payouts via Stripe Connect or crypto (optional)
- **Escrow:** 24-hour hold on credits for dispute resolution

### Infrastructure
- **K3s:** Lightweight Kubernetes across operator nodes (existing Helm charts work)
- **Tailscale:** Private mesh network (free tier, 100 devices)
- **Cloudflare Tunnel:** Public access with automatic failover
- **Docker:** Agent execution sandboxing

---

## 7. RL Integration (Existing Advantage)

The existing RL system becomes the protocol's quality engine:

| RL Component | Current Use | Protocol Use |
|---|---|---|
| `rl_experience` table | Logs agent/tool decisions | Logs every task execution across the network |
| Thumbs up/down feedback | Improves Luna's routing | Updates agent quality scores network-wide |
| `rl_policy_state` | Per-tenant learning | Per-agent learning across all users |
| Experience replay | Batch training | Network-wide agent ranking |

**Agent Quality Score formula:**
```
quality_score = (
    success_rate * 0.3 +           # % of tasks completed successfully
    avg_user_rating * 0.3 +        # Average thumbs up ratio
    speed_percentile * 0.1 +       # How fast vs similar agents
    recency_weight * 0.1 +         # Recent performance matters more
    execution_count_log * 0.2      # More executions = more trusted
)
```

Agents that perform better earn more traffic, which earns more revenue for their creators. Natural selection for agent quality.

---

## 8. Migration Path (Existing → Protocol)

### What Already Exists and Transfers Directly

| Component | Current | Protocol Role |
|---|---|---|
| FastAPI backend | Running on MacBook | Becomes the Registry Service API |
| 81 MCP tools | Running in mcp-tools container | Every node gets these, agents use them |
| Skill marketplace | 70+ skills loaded | Becomes the Agent Registry seed |
| CLI session manager | Routes to Claude Code | Becomes the Agent Runner |
| Agent router | Deterministic routing | Becomes RL-powered Task Router |
| RL experience system | Logs feedback | Becomes Quality Scoring Engine |
| Temporal workflows | Chat + code task workflows | Distributed task execution |
| OAuth credential vault | Stores LLM tokens | Stays local to each node (never shared) |
| Knowledge graph + pgvector | Entity/relation store | Per-tenant, synced on demand |
| Helm charts | GKE deployment (disabled) | K3s deployment across nodes |

### What Needs To Be Built

| Component | Effort | Priority |
|---|---|---|
| Node daemon (register, heartbeat, accept tasks) | Medium | Phase 1 |
| Agent package format + signing | Small | Phase 1 |
| Credit ledger + Stripe integration | Medium | Phase 1 |
| Raft consensus for registry replication | Medium | Phase 2 |
| Tailscale integration for node mesh | Small | Phase 1 |
| Cloudflare Tunnel failover | Small | Phase 1 |
| PostgreSQL streaming replication | Medium | Phase 2 |
| Marketplace UI (browse, purchase, publish agents) | Medium | Phase 2 |
| Operator dashboard (earnings, node health) | Medium | Phase 2 |
| Agent quality scoring from RL | Small (extends existing) | Phase 1 |
| Dispute resolution system | Small | Phase 3 |

---

## 9. Implementation Phases

### Phase 1: Multi-Node Foundation (Weeks 1-4)

**Goal:** ServiceTsunami runs across your MacBook + gaming laptops with automatic failover. No marketplace yet — just distributed infra.

- K3s cluster across 2-3 machines with Tailscale
- Migrate existing Docker Compose services to K3s (Helm charts exist)
- PostgreSQL HA with synchronous replication
- Cloudflare Tunnel failover across nodes
- Node daemon MVP (register, heartbeat, run tasks)
- Agent package format (extend existing skill.md)

**Success metric:** Close MacBook lid, site stays up, zero data loss.

### Phase 2: Marketplace + Credits (Weeks 5-8)

**Goal:** Creators can publish agents, users can buy credits and run tasks.

- Credit system (buy with Stripe, internal ledger)
- Agent publishing flow (sign, upload, list in marketplace)
- Marketplace UI (browse agents, see quality scores, one-click execute)
- Revenue split automation (creator/operator/protocol)
- Operator dashboard (earnings, node metrics)
- Agent quality scoring from RL feedback

**Success metric:** Someone publishes an agent, someone else pays to use it.

### Phase 3: Open Network (Weeks 9-12)

**Goal:** Anyone can join as an operator or creator. Network is self-sustaining.

- Public node registration (download binary, join network)
- Raft consensus for multi-node registry (no single point of failure)
- Agent transfer/licensing (sell your agent to another creator)
- Dispute resolution (user contests result, credits refunded from escrow)
- Rate limiting and abuse prevention
- Operator reputation scoring
- Android node support (Termux)

**Success metric:** 5+ external operators running nodes, 10+ agents in marketplace.

### Phase 4: Scale + Ecosystem (Month 4+)

- API for third-party integrations
- Agent composition (agents that call other agents)
- Workflow marketplace (multi-step automations as products)
- GPU computing support (image generation, fine-tuning)
- Enterprise tier (dedicated nodes, SLA guarantees)
- Mobile app for operator monitoring

---

## 10. Competitive Advantage

| Us | Them |
|---|---|
| Subscription arbitrage — operators monetize unused LLM capacity | Everyone else charges API credits |
| RL-driven quality — agents improve from usage | Static agents, no learning |
| Open protocol — anyone can run a node | Centralized platforms (vendor lock-in) |
| Owned agents — creators earn per execution | GPT Store gives creators nothing meaningful |
| Multi-LLM — Claude + Gemini + Codex in one network | Single-vendor lock-in |
| Existing 81 MCP tools | Others start from zero |
| 70+ skills as seed catalog | Empty marketplaces |

---

## 11. Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| LLM providers block subscription sharing | High | Frame as personal use on personal hardware. Support multiple providers so no single dependency. |
| Cold start — no operators, no users | High | Bootstrap with own hardware (gaming laptops). Seed marketplace with existing 70+ skills. |
| Agent quality is poor | Medium | RL system actively demotes bad agents. Minimum quality threshold for marketplace listing. |
| Operator machines are unreliable | Medium | Multi-node redundancy. Tasks re-queue on node failure. Operators earn reputation scores. |
| Payment fraud / disputes | Medium | 24-hour escrow. Rate limiting. Operator and user reputation scores. |
| Data privacy (tenant data on someone else's machine) | High | Credentials never leave operator's machine. Task execution is sandboxed. Tenant data accessed via API, not replicated. |

---

## 12. Open Questions

1. **Should agent packages include training data?** — If agents improve from RL, should the trained state be part of the package, or stored separately?
2. **Multi-tenant data isolation** — When a task runs on an operator's node, how much tenant context should be accessible? Pull-on-demand vs. full sync.
3. **Pricing discovery** — Should operators set their own rates, or should the protocol set dynamic pricing based on supply/demand?
4. **Governance** — Who decides protocol upgrades? Core team? Node operators vote?
5. **Legal entity** — Foundation? DAO? Company with open protocol?
