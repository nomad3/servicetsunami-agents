/**
 * Cardiology showcase — ONE high-trust depth example, deliberately NOT
 * the headline (Luna guardrail). Shows how the practice OS turns an
 * inbound echo study into a specialist-grade cardiac report DRAFT that a
 * cardiologist approves in minutes. The agent assembles; the specialist
 * decides. Step-flow visual, Framer Motion staged reveal.
 */
import { motion, useReducedMotion } from 'framer-motion';

const STEPS = [
  {
    num: '01',
    actor: 'Intake agent',
    title: 'Echo study arrives',
    body: 'An inbound echocardiogram hits the practice inbox and is matched to the right patient automatically — source and timestamp captured.',
  },
  {
    num: '02',
    actor: 'Diagnostics agent',
    title: 'Findings assembled',
    body: 'Measurements, prior history, and imaging are pulled from the unified record into a structured draft — every value traceable to its source.',
  },
  {
    num: '03',
    actor: 'Diagnostics agent',
    title: 'Report drafted',
    body: 'A specialist-grade cardiac evaluation draft is composed in your template — organized, cited, and ready for an expert eye.',
  },
  {
    num: '04',
    actor: 'Cardiologist',
    title: 'Specialist approves',
    body: 'Your cardiologist reviews, edits, and signs off in minutes — not hours. Nothing reaches the client until a licensed expert approves it.',
    gate: true,
  },
];

export default function VetCardiologyShowcase() {
  const prefersReducedMotion = useReducedMotion();
  return (
    <section className="vet-cardio" id="cardiology">
      <div className="vet-cardio__inner">
        <span className="vet-cardio__eyebrow">Depth example · Cardiology</span>
        <h2 className="vet-cardio__title">
          From inbound echo to approved report — in minutes.
        </h2>
        <p className="vet-cardio__subtitle">
          The same practice OS that runs your front desk goes deep where it
          counts. Here&rsquo;s one high-trust example: a cardiac report draft your
          cardiologist approves — assembled by an agent, decided by an expert.
        </p>

        <ol className="vet-cardio__steps">
          {STEPS.map((s, i) => (
            <motion.li
              key={s.num}
              className={`vet-cardio__step${s.gate ? ' vet-cardio__step--gate' : ''}`}
              initial={prefersReducedMotion ? false : { opacity: 0, y: 18 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-40px' }}
              transition={{ duration: 0.45, delay: i * 0.08 }}
            >
              <span className="vet-cardio__step-num" aria-hidden="true">{s.num}</span>
              <div className="vet-cardio__step-text">
                <span className="vet-cardio__step-actor">{s.actor}</span>
                <h3 className="vet-cardio__step-title">{s.title}</h3>
                <p className="vet-cardio__step-body">{s.body}</p>
                {s.gate && (
                  <span className="vet-cardio__step-badge">
                    <span aria-hidden="true">✓</span> Licensed specialist approves
                  </span>
                )}
              </div>
            </motion.li>
          ))}
        </ol>

        <p className="vet-cardio__footnote">
          Cardiology is one depth example. The same draft-then-approve pattern
          runs across diagnostics, documentation, billing, and beyond.
        </p>
      </div>
    </section>
  );
}
