/**
 * "An agent for every part of the practice" — the agent-fleet section.
 *
 * Each agent owns one slice of the operation and carries an explicit
 * approval gate. Luna guardrail: NO clinical autonomy. Agents draft,
 * triage, and prepare — a licensed human signs off on anything
 * clinical or financial. Card grid mirrors AlphaCommands.js.
 */
import { motion, useReducedMotion } from 'framer-motion';

const AGENTS = [
  {
    icon: '🛎️',
    title: 'Front Desk & Scheduling',
    body: 'Prepares booking, confirmation, and reschedule workflows; fills cancellations from approved rules.',
    gate: 'Staff confirms every booking change',
  },
  {
    icon: '📝',
    title: 'Clinical Documentation',
    body: 'Turns exam-room audio and notes into structured SOAP drafts, attached to the record.',
    gate: 'Clinician approves every note',
  },
  {
    icon: '🔬',
    title: 'Diagnostics & Specialist Reports',
    body: 'Assembles labs and imaging into specialist-grade report drafts for review.',
    gate: 'Specialist approves every report',
  },
  {
    icon: '💬',
    title: 'Client Communication',
    body: 'Drafts reminders, results explainers, and follow-ups across SMS, WhatsApp, and email.',
    gate: 'Team approves before anything sends',
  },
  {
    icon: '🧾',
    title: 'Billing & Collections',
    body: 'Reconciles invoices, flags unpaid balances, and prepares statements and payment plans.',
    gate: 'Manager approves every adjustment',
  },
  {
    icon: '📦',
    title: 'Inventory & Pharmacy',
    body: 'Tracks stock, predicts reorders, and drafts purchase orders before you run dry.',
    gate: 'Lead approves every order',
  },
  {
    icon: '📣',
    title: 'Marketing & Reputation',
    body: 'Surfaces reviews, drafts responses, and prepares campaigns from real practice signals.',
    gate: 'You approve before anything posts',
  },
  {
    icon: '🤝',
    title: 'Referrals',
    body: 'Routes cases to the right specialist, packages records, and tracks the loop closed.',
    gate: 'Vet approves every referral',
  },
];

export default function VetAgentFleet() {
  const prefersReducedMotion = useReducedMotion();
  return (
    <section className="vet-fleet" id="fleet">
      <div className="vet-fleet__inner">
        <h2 className="vet-fleet__title">An agent for every part of the practice.</h2>
        <p className="vet-fleet__subtitle">
          Not one chatbot trying to do everything — a coordinated fleet of
          specialists, each owning a slice of the day-to-day. Every agent
          drafts and prepares; a person on your team approves the decisions
          that matter.
        </p>

        <div className="vet-fleet__grid">
          {AGENTS.map((a, i) => (
            <motion.div
              key={a.title}
              className="vet-fleet__card"
              initial={prefersReducedMotion ? false : { opacity: 0, y: 18 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-40px' }}
              transition={{ duration: 0.45, delay: i * 0.04 }}
            >
              <div className="vet-fleet__card-icon" aria-hidden="true">{a.icon}</div>
              <h3 className="vet-fleet__card-title">{a.title}</h3>
              <p className="vet-fleet__card-body">{a.body}</p>
              <p className="vet-fleet__card-gate">
                <span className="vet-fleet__card-gate-icon" aria-hidden="true">🔒</span>
                {a.gate}
              </p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
