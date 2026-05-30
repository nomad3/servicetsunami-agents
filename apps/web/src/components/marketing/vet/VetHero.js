/**
 * Hero section for vet.agentprovision.com.
 *
 * Positioning (Luna-led): "the operating system for veterinary
 * practices." NOT a chatbot wrapper, NOT clinical autonomy. The shape
 * mirrors AlphaHero (two-column, Framer Motion entrance) but swaps the
 * terminal panel for a "unified record" visual — every system the
 * practice already runs feeding one source-traceable record, with a
 * human approval gate on every clinical/financial decision.
 */
import { motion, useReducedMotion } from 'framer-motion';
import { track } from '../../../services/marketingAnalytics';

// Auth always lives on the apex — cloudflared only routes /api/* on
// agentprovision.com, so subdomain visitors register/sign-in there.
// Mirrors the APEX_REGISTER pattern from AlphaHero (PR #450 B1).
const APEX_REGISTER = 'https://agentprovision.com/register';

// The "feeds" that converge into one record. Static labels — vet
// landing is English-only at launch, same call AlphaCommands made.
const FEEDS = [
  'PIMS',
  'Scribe',
  'Imaging',
  'Labs',
  'Scheduling',
  'Billing',
];

export default function VetHero() {
  const prefersReducedMotion = useReducedMotion();

  return (
    <section className="vet-hero" id="top">
      <div className="vet-hero__bg" />

      <div className="vet-hero__content">
        <motion.div
          className="vet-hero__text"
          initial={prefersReducedMotion ? false : { opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: 'easeOut' }}
        >
          <span className="vet-hero__badge">The Veterinary Practice OS</span>
          <h1 className="vet-hero__title">
            The operating system<br />for veterinary practices.
          </h1>
          <p className="vet-hero__subtitle">
            Connect every system you already run — PIMS, scribe, imaging,
            labs, scheduling, and billing — into one source-traceable record.
            Then let a fleet of agents coordinate the day-to-day work, with
            your team approving every clinical and financial decision.
          </p>

          <ul className="vet-hero__assurances" aria-label="What stays true">
            <li className="vet-hero__assurance">A licensed human approves every decision</li>
            <li className="vet-hero__assurance">Provenance on every fact</li>
            <li className="vet-hero__assurance">No rip-and-replace</li>
          </ul>

          <div className="vet-hero__ctas">
            {/* Anchors styled as buttons — a real <button> nested inside
                <a> is invalid DOM (React nesting warning). The button
                classes carry the identical visual style. Absolute apex
                href so the auth flow always resolves — cloudflared only
                routes /api/* on the apex hostname. */}
            <a
              className="vet-hero__cta-primary"
              href={APEX_REGISTER}
              onClick={() => track('vet_get_started_click', { location: 'hero' })}
            >
              Request access
            </a>
            <a
              className="vet-hero__cta-ghost"
              href="#fleet"
              onClick={() => track('vet_see_fleet_click', { location: 'hero' })}
            >
              See the agent fleet →
            </a>
          </div>
        </motion.div>

        <motion.div
          className="vet-hero__panel"
          initial={prefersReducedMotion ? false : { opacity: 0, x: 24 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.6, ease: 'easeOut', delay: 0.2 }}
          aria-hidden="true"
        >
          <div className="vet-hero__panel-feeds">
            {FEEDS.map((feed, i) => (
              <motion.span
                key={feed}
                className="vet-hero__feed"
                initial={prefersReducedMotion ? false : { opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4, delay: 0.35 + i * 0.08 }}
              >
                {feed}
              </motion.span>
            ))}
          </div>

          <div className="vet-hero__panel-arrow">↓</div>

          <div className="vet-hero__panel-record">
            <span className="vet-hero__panel-record-label">One unified record</span>
            <span className="vet-hero__panel-record-sub">
              source · timestamp · confidence on every fact
            </span>
          </div>

          <div className="vet-hero__panel-arrow">↓</div>

          <div className="vet-hero__panel-gate">
            <span className="vet-hero__panel-gate-icon" aria-hidden="true">✓</span>
            <span>Licensed human approves</span>
          </div>
        </motion.div>
      </div>
    </section>
  );
}
