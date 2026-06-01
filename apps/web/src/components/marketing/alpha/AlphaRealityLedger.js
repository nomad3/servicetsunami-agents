/**
 * AlphaRealityLedger — the honest "what's live vs roadmap" section.
 *
 * The single biggest trust move at launch (Codex + Luna review, 2026-05-31,
 * independently): publish, on the landing page, the line between shipped system
 * and thesis. Three columns — Live now / In alpha (guarded) / Research (next).
 * This proves we know the difference, and it makes the ambitious parts SAFER
 * because they're labeled — more credible than another demo video. It also
 * doubles as objection-handling (human approval, audit, tenant isolation, BYO).
 *
 * Honesty guardrail (Luna, lead): nothing in "Live now" is aspirational; the
 * emotions/coalition/trust items that are partial live in "In alpha".
 */
import { motion, useReducedMotion } from 'framer-motion';

const COLUMNS = [
  {
    id: 'live',
    tag: 'Live now',
    accent: 'live',
    note: 'Running in production today.',
    items: [
      'Durable memory — knowledge graph + pgvector recall, pre-loaded every turn',
      'CLI-fleet routing across Claude Code, Codex, Gemini CLI, Copilot',
      'Human-in-the-loop approval gates on side-effecting actions',
      'Temporal-backed durable workflows + full audit trail',
      'Agent roles, hand-offs, and A2A coalitions on a shared blackboard',
      'Per-tenant isolation · bring-your-own CLI subscriptions',
    ],
  },
  {
    id: 'alpha',
    tag: 'In alpha · guarded',
    accent: 'alpha',
    note: 'Shipped, behind flags, expanding.',
    items: [
      'Mood-influenced behavior — a per-agent affect signal that biases tone & caution',
      'Trust scoring on hand-off outcomes (write-only first; never auto-routes yet)',
      'Coalition formation across patterns (incident, plan-verify, critique-revise)',
      'Source-grounded answers — labels what it knows vs infers vs proposes',
    ],
  },
  {
    id: 'next',
    tag: 'Research · next',
    accent: 'next',
    note: 'On the roadmap, not claimed as shipped.',
    items: [
      'Fleet-wide emotional coordination — agents reading each other&rsquo;s state',
      'Durable affect loops that bias what gets remembered and recalled',
      'Adaptive team structure & norms that self-correct from outcomes',
      'Long-running organizational learning across nights',
    ],
  },
];

export default function AlphaRealityLedger() {
  const reduced = useReducedMotion();

  return (
    <section className="alpha-ledger" id="reality">
      <div className="alpha-ledger__inner">
        <motion.div
          className="alpha-ledger__head"
          initial={reduced ? false : { opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: '-80px' }}
          transition={{ duration: 0.5 }}
        >
          <span className="alpha-ledger__eyebrow">The reality ledger</span>
          <h2 className="alpha-ledger__title">What&rsquo;s live. What&rsquo;s guarded. What&rsquo;s next.</h2>
          <p className="alpha-ledger__subtitle">
            Most AI pages blur the line between what ships and what&rsquo;s a pitch.
            We don&rsquo;t. Here&rsquo;s exactly where the system stands — because a
            teammate you can trust is one that tells you what it can&rsquo;t do yet.
          </p>
        </motion.div>

        <div className="alpha-ledger__grid">
          {COLUMNS.map((col, c) => (
            <motion.div
              key={col.id}
              className={`alpha-ledger__col alpha-ledger__col--${col.accent}`}
              initial={reduced ? false : { opacity: 0, y: 18 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-60px' }}
              transition={{ duration: 0.45, delay: reduced ? 0 : c * 0.1 }}
            >
              <div className="alpha-ledger__col-head">
                <span className={`alpha-ledger__pip alpha-ledger__pip--${col.accent}`} />
                <span className="alpha-ledger__tag">{col.tag}</span>
              </div>
              <p className="alpha-ledger__note">{col.note}</p>
              <ul className="alpha-ledger__list">
                {col.items.map((it, i) => (
                  // Items carry an &rsquo; entity in one string; render as HTML so
                  // the apostrophe shows correctly without splitting the copy.
                  <li key={i} dangerouslySetInnerHTML={{ __html: it }} />
                ))}
              </ul>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
