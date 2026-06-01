/**
 * Three-up callout for the platform power alpha taps into:
 * RL (auto-scoring + policy routing), Memory (knowledge graph + vector
 * recall), Workflows (Temporal). These are the things claude/codex
 * physically cannot ship as a leaf CLI — they need the orchestrator
 * tier alpha provides.
 */
import { motion, useReducedMotion } from 'framer-motion';

const PILLARS = [
  {
    icon: '🧠',
    title: 'Reinforcement learning from every response',
    body:
      'Every chat turn is scored by a council across 6 quality dimensions, recorded ' +
      'as an RL experience, and fed into the per-tenant, per-agent policy. Your team ' +
      'gets better routing decisions over time — no prompt-engineering bake-off needed.',
    proof: '6-dimension rubric · 5% exploration · local Gemma 4 (Ollama)',
  },
  {
    icon: '📚',
    title: 'Memory-first, not memoryless',
    body:
      'A real knowledge graph (entities, relations, observations) backed by 768-dim embeddings ' +
      'from nomic-embed-text-v1.5. Every agent on your account queries the same memory before ' +
      'every response. Tribal knowledge stops walking out the door.',
    proof: 'Entity graph · Vector store · alpha recall + alpha remember',
  },
  {
    icon: '⚡',
    title: 'Temporal workflows under the hood',
    body:
      'Durability without you writing distributed-systems code. Every alpha task runs as a ' +
      'Temporal workflow — automatic retries, cross-machine resume, deterministic replay, ' +
      'multi-agent coalitions on a shared blackboard.',
    proof: 'agentprovision-orchestration queue · CoalitionWorkflow · ChatCliWorkflow',
  },
];

export default function AlphaPlatformPower() {
  const prefersReducedMotion = useReducedMotion();
  return (
    <section className="alpha-power" id="platform">
      <div className="alpha-power__inner">
        <h2 className="alpha-power__title">
          The platform behind the network.
        </h2>
        <p className="alpha-power__subtitle">
          alpha is the surface; the substrate underneath is a real, running system —
          it learns from every response, remembers across the whole fleet, and runs
          durably on Temporal — always with human-in-the-loop approval gates and a full
          audit trail. The operating layer for an AI agent network, not
          autonomous-everything.
        </p>

        <div className="alpha-power__grid">
          {PILLARS.map((p, i) => (
            <motion.div
              key={p.title}
              className="alpha-power__pillar"
              initial={prefersReducedMotion ? false : { opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-40px' }}
              transition={{ duration: 0.5, delay: i * 0.1 }}
            >
              <div className="alpha-power__pillar-icon" aria-hidden="true">
                {p.icon}
              </div>
              <h3 className="alpha-power__pillar-title">{p.title}</h3>
              <p className="alpha-power__pillar-body">{p.body}</p>
              <p className="alpha-power__pillar-proof">{p.proof}</p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
