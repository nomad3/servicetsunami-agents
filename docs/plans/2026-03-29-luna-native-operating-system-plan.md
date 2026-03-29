# Luna Native Operating System Plan

**Date**: 2026-03-29
**Status**: Active master plan
**Scope**: Turn Luna from a chat layer inside `agentprovision.com` into a native AI operating system where AgentProvision is the brain and native apps are Luna's persistent presence, sensors, and actuators.

---

## Core Thesis

`agentprovision.com` remains the system of record, orchestration engine, memory substrate, skill registry, and reinforcement learning loop.

Native apps are not a separate product. They are Luna's operating surface:

- Desktop app = command center, ambient presence, global shortcut, notifications, local system access
- Mobile app = pocket shell, voice relay, push surface, BLE bridge for wearables
- Wearables + devices = sensors and actuators
- Web app = universal fallback shell and control plane

The right architecture is:

`AgentProvision brain -> Luna presence layer -> Native shells -> Devices/tools`

---

## Repo Reality Check

Recent plans already point in the right direction, and important parts are no longer hypothetical.

### Already shipped or materially present

- CLI orchestration pivot is live: Luna already runs through the CLI orchestration path with MCP tools as the action layer.
- Presence foundation exists:
  - `apps/api/app/api/v1/presence.py`
  - `apps/api/app/services/luna_presence_service.py`
  - `apps/web/src/context/LunaPresenceContext.js`
  - `apps/web/src/components/luna/LunaAvatar.js`
  - `apps/web/src/components/luna/LunaStateBadge.js`
- Memory v2 is underway and partially shipped:
  - source attribution and contradiction detection landed
  - episodic memory landed and was followed by fixes for background priority, dedup cooldown, and owner/user injection
  - model present: `apps/api/app/models/conversation_episode.py`
- Long-horizon cognition primitives exist:
  - plans: `apps/api/app/models/plan.py`
  - goals, commitments, world state, collaborations, coalitions, learning experiments, simulations, proactive actions
- RL and quality scoring are already platform primitives in `CLAUDE.md`, not future ideas.
- Skill infrastructure already exists:
  - `apps/api/app/services/skill_manager.py`
  - `apps/api/app/services/skill_registry_service.py`
  - `apps/web/src/services/skills.js`
- Autonomous learning is already an active design direction, not a blank slate.

### What is still missing

- A single master plan that treats all of the above as one operating system program
- A native-shell roadmap tied to the existing AgentProvision architecture
- A progress-aware ordering that prioritizes persistence, trust, and local execution before novelty hardware
- A clean definition of what runs in the web brain vs what runs on device

---

## Strategic Product Frame

Luna should behave like an AI-first operating system with five tightly connected layers:

### 1. Brain

Lives in `agentprovision.com`.

- identity and routing
- memory and knowledge graph
- skills and tool registry
- plans, goals, commitments, world state
- RL experience store and policy updates
- autonomous learning and simulation

### 2. Presence

Lets Luna feel continuously available instead of session-based.

- presence state
- shell registry
- handoff across web, desktop, mobile, WhatsApp
- notification strategy
- active-device awareness
- privacy state and trust posture

### 3. Native Control

Gives Luna real leverage on the user's devices.

- menu bar / tray app
- global hotkey
- microphone, screenshots, files, clipboard
- local notifications
- background sync
- optional local automations and OS actions

### 4. Embodied Sensing

Lets Luna observe and act beyond chat.

- phone sensors
- BLE wearables
- camera streams
- robot presence
- ambient capture with explicit privacy controls

### 5. Self-Improvement

Lets the system get better every day.

- response scoring
- routing optimization
- learned memory recall
- policy candidates
- simulations and safety gates
- human feedback loop

---

## Design Rule: Brain vs Arms

This split should govern every implementation decision.

### AgentProvision.com owns

- tenant data and auth
- memory, embeddings, knowledge graph, observations
- skill discovery and execution policy
- planning, goals, commitments, proactive actions
- RL, quality scoring, simulation, policy promotion
- canonical conversation state
- cross-device identity and permissions

### Native apps own

- low-latency interaction
- OS integrations and background execution
- local sensors and device adapters
- notifications, overlays, hotkeys, tray state
- intermittent offline buffering
- secure relay of context, files, audio, screenshots, and device events back to the brain

If a capability is reusable across shells or needs tenant memory, it belongs in AgentProvision.
If it touches local hardware, interruption, or ambient presence, it belongs in native.

---

## How Existing Plans Map Into The OS Vision

### Presence system plan

Becomes the OS session layer. It should evolve from "presence API" into Luna's shell-state protocol and handoff model.

### Multi-platform client plan

Becomes the shell runtime plan. It should focus first on desktop and mobile control surfaces, not just UI portability.

### Memory v2 plan

Becomes Luna's continuity engine. Episodic memory, source attribution, contradiction detection, sentiment, and anticipation are what make native presence feel personal rather than stateless.

### RL framework + autonomous learning plans

Become the optimization core. Native Luna should learn which shell to use, when to interrupt, which skills to trigger, and how to tune trust boundaries.

### Skills marketplace and CLI orchestration pivot

Become the action bus. AgentProvision already has the beginnings of an execution substrate; native Luna should use that instead of inventing a parallel command system.

---

## Operating System Roadmap

## Phase 0 — Consolidate the Brain

Goal: make the current web platform the unquestioned source of truth before expanding shell count.

### Outcomes

- Treat the current AgentProvision app as Luna OS core
- Unify plan, memory, skill, RL, and presence terminology across docs
- Define one shell protocol for web, WhatsApp, desktop, and mobile

### Build

- Standardize a single `Luna Shell` concept across presence, chat, notifications, and handoff
- Add a shell capability model:
  - `can_listen`
  - `can_notify`
  - `can_capture_screen`
  - `can_capture_audio`
  - `can_connect_ble`
  - `can_run_local_actions`
- Add trust tiers for shell actions:
  - observe
  - recommend
  - act_with_confirmation
  - act_autonomously

### Why first

Without this, native apps become disconnected wrappers instead of first-class OS surfaces.

---

## Phase 1 — Desktop Presence First

Goal: make Luna feel continuously present on the user's primary machine.

### Outcomes

- menu bar / tray presence
- global summon shortcut
- notification delivery
- voice capture
- screenshot and file drop into AgentProvision memory
- always-on local shell connected to the web brain

### Build

- bootstrap `apps/luna-client/` as the desktop-first shell
- start with macOS as the reference environment
- implement:
  - tray icon
  - quick overlay
  - push-to-talk
  - screenshot capture
  - clipboard/file intake
  - shell heartbeat back to AgentProvision
- persist shell presence into Luna's existing presence service contract

### Definition of done

Luna can be summoned globally, see what the user shared, keep context between invocations, and push timely notifications without opening the browser.

---

## Phase 2 — Memory-Led Native Experience

Goal: make Luna feel like she lives with the user, not that she reboots every message.

### Outcomes

- cross-shell continuity
- episodic recall in native flows
- anticipatory reminders and meeting prep
- source-aware answers from native interactions
- tighter relation between desktop events and knowledge graph updates

### Build

- complete Memory v2 modules in the order that most improves native experience:
  1. episodic memory hardening
  2. source attribution everywhere
  3. anticipatory context
  4. emotional memory
  5. preference learning
- treat desktop/mobile interactions as first-class memory sources, not just chat variants
- record shell context on observations and episodes

### Definition of done

Native Luna can say what happened recently, why it matters now, and what she learned from previous interactions across devices.

---

## Phase 3 — Mobile Companion + Wearable Relay

Goal: extend Luna from desk presence to all-day presence.

### Outcomes

- mobile shell with push, voice, and background relay
- BLE bridge for necklace and future wearables
- meeting capture and ambient review flows with explicit consent
- seamless handoff between desktop and mobile

### Build

- mobile shell focused on:
  - push notifications
  - voice interaction
  - quick capture
  - BLE bridge
  - offline buffering
- keep business logic in AgentProvision; keep mobile thin
- make privacy mode obvious and interruptible

### Definition of done

Luna can follow the user away from the desktop while preserving memory, trust, and action continuity.

---

## Phase 4 — Local Actions and Native Automation

Goal: give Luna real operational power on device.

### Outcomes

- controlled local actions
- task automation on desktop/mobile
- context-aware suggestions grounded in recent behavior

### Build

- add native action adapters for:
  - open app / URL
  - create draft
  - save note
  - attach file
  - trigger workflow
  - run approved local command
- gate everything through trust profiles, safety policies, and action approval levels already emerging in the platform
- feed outcomes back into RL and proactive actions

### Definition of done

Luna does not just answer. She reliably executes bounded local work.

---

## Phase 5 — Embodied Devices

Goal: expand Luna into devices only after the brain, trust model, and shells are solid.

### Outcomes

- camera-aware context
- desk robot as embodied presence
- wearable sensing with privacy-first review
- unified shell/device registry

### Build

- device bridge
- camera ingestion
- robot command bus
- device event memory pipeline
- per-device privacy and capability controls

### Definition of done

Devices are extensions of Luna's operating system, not disconnected experiments.

---

## Cross-Cutting Requirements

### Trust and permissions

Native Luna only works if the user trusts her.

- every shell and device needs explicit capabilities
- every capability needs a trust tier
- every side effect should be reviewable, attributable, and reversible when practical
- privacy state must be visible in all shells

### Memory as default

Every native event should improve the graph.

- shell connects/disconnects
- notifications acted on or ignored
- voice captures
- screenshots/files explicitly shared
- device observations
- repeated workflows and preferences

### RL and self-improvement

The platform should learn:

- which shell gets the fastest response
- which notifications are useful vs ignored
- when to interrupt vs stay silent
- what level of autonomy the user tolerates
- what context increases task success

### Skills as the execution layer

Native Luna should trigger skills and workflows, not duplicate them.

- shell action requests should route into the existing skill/workflow substrate
- native adapters should be thin wrappers around platform actions when possible

---

## Recommended Build Order For The Repo

1. Create this as the master reference for Luna OS work.
2. Update the presence and multiplatform plans so they reference this document as the parent plan.
3. Ship desktop-first `apps/luna-client/` shell before investing in more hardware-specific work.
4. Finish Memory v2 items that improve continuity across shells.
5. Connect notifications, proactive actions, and RL signals to native shell behavior.
6. Add local action adapters with strong approval policy.
7. Only then expand into device bridge, robot, and wearables at scale.

---

## Near-Term Execution Sprint

The next implementation sprint should focus on the smallest set of work that makes Luna feel like a native operating system instead of a browser tab:

- desktop shell bootstrap
- shell heartbeat + handoff contract
- notification pipeline integration
- screenshot/file capture into memory
- memory v2 continuity improvements in native flows
- trust model for local actions

That sequence produces the first real OS-like experience while staying aligned with the architecture already present in the repo.

---

## Success Metric

Luna becomes a native operating system when the user stops thinking in terms of "opening AgentProvision" and starts thinking in terms of "Luna is already here, remembers everything important, and can act from wherever I am."
