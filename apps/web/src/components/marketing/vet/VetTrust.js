/**
 * Trust section — the guardrails that make an agent fleet safe inside a
 * licensed medical practice. Luna-led positioning: provenance on every
 * fact, a licensed human signs off, full audit trail, your data stays
 * yours. NO clinical-autonomy claims anywhere on this page; this
 * section is where we make that promise explicit. Pillar layout mirrors
 * AlphaPlatformPower.js.
 */
import { motion, useReducedMotion } from 'framer-motion';

const PILLARS = [
  {
    icon: '🧬',
    title: 'Provenance on every fact',
    body:
      'Every data point in the record carries its source, a timestamp, and a ' +
      'confidence signal. When an agent surfaces something, you can see exactly ' +
      'where it came from — no black-box answers, no untraceable claims.',
    proof: 'source · timestamp · confidence',
  },
  {
    icon: '✍️',
    title: 'A licensed human signs off',
    body:
      'Agents draft, triage, and prepare. They never diagnose, prescribe, or ' +
      'settle a bill on their own. Every clinical and financial decision routes ' +
      'to a licensed member of your team for approval before it counts.',
    proof: 'approval gate on every decision',
  },
  {
    icon: '🧾',
    title: 'Full audit trail',
    body:
      'Who approved what, when, and on what evidence — captured for every action ' +
      'the fleet takes. Reconstruct any decision after the fact for quality ' +
      'review, compliance, or peace of mind.',
    proof: 'who · what · when · why',
  },
  {
    icon: '🔐',
    title: 'Your data stays yours',
    body:
      'Per-practice isolation, encrypted credentials, and a record you own. ' +
      'AgentProvision connects your systems together — it does not take your ' +
      'data somewhere you can’t reach it.',
    proof: 'tenant isolation · encrypted vault',
  },
];

export default function VetTrust() {
  const prefersReducedMotion = useReducedMotion();
  return (
    <section className="vet-trust" id="trust">
      <div className="vet-trust__inner">
        <h2 className="vet-trust__title">Built to be trusted in a medical practice.</h2>
        <p className="vet-trust__subtitle">
          An agent fleet only belongs in veterinary medicine if it earns trust the
          way your team does — with evidence, sign-off, and a record of every call.
          This is not an AI veterinarian. It is the operating system your licensed
          team runs the practice on.
        </p>

        <div className="vet-trust__grid">
          {PILLARS.map((p, i) => (
            <motion.div
              key={p.title}
              className="vet-trust__pillar"
              initial={prefersReducedMotion ? false : { opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-40px' }}
              transition={{ duration: 0.5, delay: i * 0.1 }}
            >
              <div className="vet-trust__pillar-icon" aria-hidden="true">{p.icon}</div>
              <h3 className="vet-trust__pillar-title">{p.title}</h3>
              <p className="vet-trust__pillar-body">{p.body}</p>
              <p className="vet-trust__pillar-proof">{p.proof}</p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
