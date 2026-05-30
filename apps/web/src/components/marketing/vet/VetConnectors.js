/**
 * "Your systems, finally working together" — the connectors section.
 *
 * Practice-OS thesis: AgentProvision sits ON TOP of the tools a clinic
 * already runs (no rip-and-replace) and unifies them into one
 * source-traceable record. Modeled on AlphaPlatformPower's grid, but
 * card content is the real-world veterinary stack a buyer recognises.
 */
import { motion, useReducedMotion } from 'framer-motion';

const CONNECTORS = [
  {
    icon: '🗂️',
    name: 'Covetrus Pulse',
    category: 'PIMS',
    body: 'Patients, visits, and invoices flow in so every agent works from the same medical record.',
  },
  {
    icon: '🎙️',
    name: 'ScribbleVet',
    category: 'AI Scribe',
    body: 'Exam-room transcripts land in the record, structured and source-tagged — never a loose note.',
  },
  {
    icon: '🧪',
    name: 'Antech',
    category: 'Reference Labs',
    body: 'Diagnostics arrive attached to the right patient, with result provenance preserved.',
  },
  {
    icon: '🔬',
    name: 'IDEXX',
    category: 'In-house Diagnostics',
    body: 'In-house panels and imaging sync automatically — no re-keying between machines.',
  },
  {
    icon: '📅',
    name: 'Google · Microsoft 365',
    category: 'Email & Calendar',
    body: 'Inbound studies, referrals, and appointments are triaged the moment they hit the inbox.',
  },
  {
    icon: '💬',
    name: 'SMS · WhatsApp',
    category: 'Client Messaging',
    body: 'Two-way client communication, logged to the record with full conversation history.',
  },
  {
    icon: '🧾',
    name: 'Accounting',
    category: 'Billing & Ledger',
    body: 'Invoices, payments, and collections reconcile against the same source of truth.',
  },
  {
    icon: '⭐',
    name: 'BrightLocal',
    category: 'Reputation',
    body: 'Reviews and local listings feed the marketing agent — surfaced, never auto-posted.',
  },
];

export default function VetConnectors() {
  const prefersReducedMotion = useReducedMotion();
  return (
    <section className="vet-connectors" id="connectors">
      <div className="vet-connectors__inner">
        <h2 className="vet-connectors__title">Your systems, finally working together.</h2>
        <p className="vet-connectors__subtitle">
          AgentProvision sits on top of the tools your practice already runs and
          unifies them into one source-traceable record. No rip-and-replace,
          no data migration project — connect what you have and keep working.
        </p>

        <div className="vet-connectors__grid">
          {CONNECTORS.map((c, i) => (
            <motion.div
              key={c.name}
              className="vet-connectors__card"
              initial={prefersReducedMotion ? false : { opacity: 0, y: 18 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-40px' }}
              transition={{ duration: 0.45, delay: i * 0.04 }}
            >
              <div className="vet-connectors__card-head">
                <span className="vet-connectors__card-icon" aria-hidden="true">{c.icon}</span>
                <span className="vet-connectors__card-cat">{c.category}</span>
              </div>
              <h3 className="vet-connectors__card-name">{c.name}</h3>
              <p className="vet-connectors__card-body">{c.body}</p>
            </motion.div>
          ))}
        </div>

        <p className="vet-connectors__footnote">
          Don&rsquo;t see your system? The same connector pattern wires up any
          source with an API or inbox — your record, one place.
        </p>
      </div>
    </section>
  );
}
