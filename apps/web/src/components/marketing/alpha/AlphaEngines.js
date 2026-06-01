/**
 * The centerpiece of the alpha landing redesign: the FOUR engines that
 * compose AgentProvision's coordination substrate — presented as ONE
 * fused system, not four bolt-on features.
 *
 * Narrative spine (grounded in
 *   docs/marketing/emotions-engine-business-definition.md and
 *   docs/plans/2026-05-31-core-systems-strengthening-plan.md):
 * a human organization IS three loops fused — durable shared state
 * (memory), an affect/coordination signal (emotion), and role/norm
 * structure (teamwork) — all orchestrated. AgentProvision is the first
 * platform to give an AI agent NETWORK that same substrate.
 *
 * Honesty guardrail (Luna, project lead): the emotions engine ships in
 * stages — we surface the "Now / Next / Later" roadmap verbatim and do
 * NOT claim the "Later" stage (cross-agent learning, the feelings
 * dashboard) as live. Human-in-the-loop + safe-by-design stay prominent.
 */
import { motion, useReducedMotion } from 'framer-motion';

// ── The four engines ───────────────────────────────────────────────
// Each is the business-level "what it does / why it matters", kept
// consistent with the approved emotions copy.
const ENGINES = [
  {
    id: 'memory',
    icon: '📚',
    name: 'Memory engine',
    tagline: 'Durable, shared, provenance-first state across the whole network.',
    what:
      'A real knowledge graph — entities, relations, observations — backed by ' +
      '768-dim pgvector recall. Every agent on the account queries the same memory ' +
      'before every turn, and writes back what it learns.',
    why:
      'Agents remember entities, observations, and commitments across turns, across ' +
      'agents, and across nights. Tribal knowledge stops walking out the door.',
  },
  {
    id: 'emotions',
    icon: '🫀',
    name: 'Emotions engine',
    tagline: 'A mood that changes how the work gets done — not just how it sounds.',
    what:
      'A server-internal PAD (pleasure / arousal / dominance) affect model. Real ' +
      'work outcomes nudge each agent’s mood, which biases how it plans, samples, ' +
      'and communicates — focused and careful when things go wrong, exploratory when ' +
      'they go well.',
    why:
      'It’s constitutive, not cosmetic, and safe by design: driven by work outcomes, ' +
      'never by what a user types — you can’t tell an agent "you’re angry now." ' +
      'It’s the lever a supervisor uses to read the room across a fleet. Leadership ' +
      'infrastructure for AI.',
    // Honest staged rollout — verbatim from the approved business copy.
    rollout: [
      { stage: 'Now', body: 'Mood shapes how Luna communicates and plans.' },
      { stage: 'Next', body: 'Mood tunes caution vs. creativity; agents sense each other to coordinate.' },
      { stage: 'Later', body: 'Agents learn over time and a dashboard shows how Luna "feels." (roadmap)' },
    ],
  },
  {
    id: 'teamwork',
    icon: '🤝',
    name: 'Teamwork engine',
    tagline: 'Specialists compose into teams with roles, trust, and hand-offs.',
    what:
      'Agents form A2A coalitions on a shared blackboard — incident investigation, ' +
      'plan-verify, propose-critique-revise — each with defined phases and roles. A ' +
      'supervisor (Luna) orchestrates; specialists collaborate.',
    why:
      'Role/norm structure is how human organizations scaled past small groups. The ' +
      'network gets the same structure: trusted hand-offs, durable team context, and ' +
      'a supervisor that can route work to the right specialist.',
  },
  {
    id: 'orchestration',
    icon: '⚡',
    name: 'Orchestration engine',
    tagline: 'The kernel — Alpha CLI — routes work across the whole fleet.',
    what:
      'Alpha CLI routes every task across a CLI fleet (Claude Code, Codex, Gemini CLI, ' +
      'Copilot), durable Temporal workflows, and 90+ MCP tools — with human-in-the-loop ' +
      'approval gates and full audit trails.',
    why:
      'One brain, many viewports. The same orchestrator backs the terminal, the web ' +
      'control center, and every leaf agent — so the other three engines have a single ' +
      'place to compose, retry, and stay accountable.',
  },
];

export default function AlphaEngines() {
  const prefersReducedMotion = useReducedMotion();

  return (
    <section className="alpha-engines" id="engines">
      <div className="alpha-engines__inner">
        {/* ── Section thesis: the MERGE is the moat ─────────────── */}
        <motion.div
          className="alpha-engines__lede"
          initial={prefersReducedMotion ? false : { opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-80px' }}
          transition={{ duration: 0.5 }}
        >
          <span className="alpha-engines__eyebrow">The coordination substrate</span>
          <h2 className="alpha-engines__title">
            Four engines. One fused system.
            <br />
            <span className="alpha-engines__title-accent">
              The merge is the moat.
            </span>
          </h2>
          <p className="alpha-engines__subtitle">
            Most tools bolt a vector DB onto an LLM. No affect signal, no durable
            team structure — so they can&rsquo;t close a learning loop across turns,
            agents, or nights. AgentProvision runs all four engines on{' '}
            <strong>one shared substrate</strong> — the coordination layer that&rsquo;s
            been missing between isolated LLMs and a real team:{' '}
            <strong>memory makes agents stateful, mood makes them empathic, roles
            make them a team, and trust keeps them honest.</strong>
          </p>
        </motion.div>

        {/* ── The four engine cards ─────────────────────────────── */}
        <div className="alpha-engines__grid">
          {ENGINES.map((e, i) => (
            <motion.article
              key={e.id}
              className={`alpha-engines__card alpha-engines__card--${e.id}`}
              initial={prefersReducedMotion ? false : { opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-40px' }}
              transition={{ duration: 0.5, delay: i * 0.08 }}
            >
              <div className="alpha-engines__card-head">
                <span className="alpha-engines__card-icon" aria-hidden="true">
                  {e.icon}
                </span>
                <h3 className="alpha-engines__card-name">{e.name}</h3>
              </div>
              <p className="alpha-engines__card-tagline">{e.tagline}</p>
              <p className="alpha-engines__card-what">{e.what}</p>
              <p className="alpha-engines__card-why">{e.why}</p>

              {/* Emotions engine carries the honest Now/Next/Later
                  rollout so we never imply roadmap features ship today. */}
              {e.rollout && (
                <ul className="alpha-engines__rollout" aria-label="Rollout stages">
                  {e.rollout.map((r) => (
                    <li
                      key={r.stage}
                      className={`alpha-engines__rollout-item alpha-engines__rollout-item--${r.stage.toLowerCase()}`}
                    >
                      <span className="alpha-engines__rollout-stage">{r.stage}</span>
                      <span className="alpha-engines__rollout-body">{r.body}</span>
                    </li>
                  ))}
                </ul>
              )}
            </motion.article>
          ))}
        </div>

        {/* ── Fused-system pull-quote ───────────────────────────── */}
        <motion.blockquote
          className="alpha-engines__quote"
          initial={prefersReducedMotion ? false : { opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-60px' }}
          transition={{ duration: 0.5 }}
        >
          &ldquo;Emotional awareness is how human organizations scaled past small
          groups. We gave a network of agents the same lever — fused with durable
          memory and real team structure, and orchestrated end to end.&rdquo;
        </motion.blockquote>
      </div>
    </section>
  );
}
