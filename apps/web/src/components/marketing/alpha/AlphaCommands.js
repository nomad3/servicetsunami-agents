/**
 * Eight-up grid of the shipped alpha subcommands. Each card carries
 * the verb, a one-line elevator, and the actual command. Same Bento
 * spirit as the main landing's BentoGrid.js but card content is
 * static (no i18n yet — alpha landing is English-only at launch).
 */
import { motion, useReducedMotion } from 'framer-motion';

const CARDS = [
  {
    cmd: 'alpha run',
    title: 'Durable tasks',
    body: 'Kick off long jobs, close your laptop, resume on the desktop. Survives network drops + reboots.',
    example: 'alpha run "refactor auth" --background',
  },
  {
    cmd: 'alpha run --fanout',
    title: 'Multi-LLM consensus',
    body: 'Same prompt to N providers in parallel. Council-merged or first-wins.',
    example: 'alpha run "audit SQL" --fanout claude,codex,gemini',
  },
  {
    cmd: 'alpha recall',
    title: 'Semantic recall',
    body: 'Query every chat your team has ever had. Entity-graph + vector store.',
    example: 'alpha recall "FastAPI error handler pattern"',
  },
  {
    cmd: 'alpha remember',
    title: 'Tenant memory',
    body: 'Write a fact, embed it, share it across every agent on the account.',
    example: 'alpha remember "we use httpx, never requests"',
  },
  {
    cmd: 'alpha coalition',
    title: 'Multi-agent teams',
    body: 'Incident, debate, plan-verify, research-synthesize patterns out of the box.',
    example: 'alpha coalition run --pattern incident_investigation',
  },
  {
    cmd: 'alpha recipes',
    title: 'Helm charts for AI',
    body: 'Pre-built workflows: daily briefings, code reviews, deal pipelines. One install.',
    example: 'alpha recipes run daily-briefing',
  },
  {
    cmd: 'alpha usage / costs',
    title: 'Cost attribution',
    body: 'See exactly what each provider cost this month. Per-team, per-agent, per-day.',
    example: 'alpha usage --period mtd',
  },
];

export default function AlphaCommands() {
  const prefersReducedMotion = useReducedMotion();
  return (
    <section className="alpha-commands" id="commands">
      <div className="alpha-commands__inner">
        <h2 className="alpha-commands__title">
          Seven surfaces. One binary. Zero plumbing.
        </h2>
        <p className="alpha-commands__subtitle">
          Every command is backed by the AgentProvision platform —
          Temporal workflows, knowledge graph, Fernet credential vault,
          and the same RL scoring that grades every agent response.
        </p>

        <div className="alpha-commands__grid">
          {CARDS.map((c, i) => (
            <motion.div
              key={c.cmd}
              className="alpha-commands__card"
              initial={prefersReducedMotion ? false : { opacity: 0, y: 18 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-40px' }}
              transition={{ duration: 0.45, delay: i * 0.04 }}
            >
              <code className="alpha-commands__card-cmd">{c.cmd}</code>
              <h3 className="alpha-commands__card-title">{c.title}</h3>
              <p className="alpha-commands__card-body">{c.body}</p>
              <pre className="alpha-commands__card-example">$ {c.example}</pre>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
