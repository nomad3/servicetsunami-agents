/**
 * The 5×N feature comparison table — alpha CLI vs Claude Code, Codex,
 * Gemini CLI, GitHub Copilot CLI.
 *
 * Frames the conversation around the 5 structural limits leaf CLIs
 * share (per docs/plans/2026-05-13-ap-cli-differentiation-roadmap.md):
 * single-vendor LLM, ephemeral session, single-user, no durable
 * orchestration, no cross-session memory. alpha solves all five.
 */
import { motion, useReducedMotion } from 'framer-motion';

const ROWS = [
  {
    capability: 'Multi-LLM orchestration',
    detail: 'Fanout, fallback, council consensus across providers',
    alpha: true,
    claude: false,
    codex: false,
    gemini: false,
    copilot: false,
  },
  {
    capability: 'Durable tasks (survive terminal close)',
    detail: 'Resume from any machine on the same account',
    alpha: true,
    claude: false,
    codex: false,
    gemini: false,
    copilot: false,
  },
  {
    capability: 'Multi-tenant + RBAC',
    detail: 'Per-tenant policies, audit log, cost attribution',
    alpha: true,
    claude: false,
    codex: false,
    gemini: false,
    copilot: false,
  },
  {
    capability: 'Cross-session memory',
    detail: 'Semantic recall over every chat your team has ever had',
    alpha: true,
    claude: false,
    codex: false,
    gemini: false,
    copilot: false,
  },
  {
    capability: 'Multi-agent coalitions',
    detail: 'Spin up 4 collaborating agents from one command',
    alpha: true,
    claude: false,
    codex: false,
    gemini: false,
    copilot: false,
  },
  {
    capability: 'Recipes (Helm-charts for AI workflows)',
    detail: 'Daily briefings, code reviews, deal pipelines — one install',
    alpha: true,
    claude: false,
    codex: false,
    gemini: false,
    copilot: false,
  },
  {
    capability: 'Live progress JSONL for agents/CI',
    detail: 'Machine-parseable event stream out of every long-running task',
    alpha: true,
    claude: false,
    codex: false,
    gemini: false,
    copilot: false,
  },
  {
    capability: 'Cost & token attribution per provider',
    detail: 'See exactly what your team spent, broken down by LLM',
    alpha: true,
    claude: false,
    codex: false,
    gemini: false,
    copilot: false,
  },
];

const CHECK = '✓';
const X = '—';

export default function AlphaDifferentiators() {
  const prefersReducedMotion = useReducedMotion();
  return (
    <section className="alpha-diff" id="differentiators">
      <div className="alpha-diff__inner">
        <motion.h2
          className="alpha-diff__title"
          initial={prefersReducedMotion ? false : { opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-80px' }}
          transition={{ duration: 0.5 }}
        >
          alpha is not competing with Claude Code, Codex, or Gemini CLI.
          <br />
          <span className="alpha-diff__title-accent">It orchestrates a network of them.</span>
        </motion.h2>
        <p className="alpha-diff__subtitle">
          A leaf CLI is one agent, one vendor, one ephemeral session — no shared
          memory, no affect signal, no durable team structure, so it can&rsquo;t
          coordinate a fleet or close a learning loop across turns. alpha is the
          substrate underneath: it gives a network of these CLIs the eight
          capabilities none of them ship alone.
        </p>

        <div className="alpha-diff__table-wrap">
          <table className="alpha-diff__table">
            <thead>
              <tr>
                <th className="alpha-diff__th alpha-diff__th--feature">Capability</th>
                <th className="alpha-diff__th alpha-diff__th--alpha">alpha</th>
                <th className="alpha-diff__th">Claude Code</th>
                <th className="alpha-diff__th">Codex</th>
                <th className="alpha-diff__th">Gemini CLI</th>
                <th className="alpha-diff__th">Copilot CLI</th>
              </tr>
            </thead>
            <tbody>
              {ROWS.map((r) => (
                <tr key={r.capability}>
                  <td className="alpha-diff__td alpha-diff__td--feature">
                    <strong>{r.capability}</strong>
                    <div className="alpha-diff__td-detail">{r.detail}</div>
                  </td>
                  <td className="alpha-diff__td alpha-diff__td--alpha-cell">
                    {r.alpha ? CHECK : X}
                  </td>
                  <td className="alpha-diff__td alpha-diff__td--other">
                    {r.claude ? CHECK : X}
                  </td>
                  <td className="alpha-diff__td alpha-diff__td--other">
                    {r.codex ? CHECK : X}
                  </td>
                  <td className="alpha-diff__td alpha-diff__td--other">
                    {r.gemini ? CHECK : X}
                  </td>
                  <td className="alpha-diff__td alpha-diff__td--other">
                    {r.copilot ? CHECK : X}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
