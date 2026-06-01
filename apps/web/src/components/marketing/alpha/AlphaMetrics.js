/**
 * Real, grounded metrics strip for the alpha landing — reinforces the
 * "network running on a real substrate" story with numbers pulled
 * straight from the platform (CLAUDE.md), NOT invented marketing stats.
 *
 *   4   engines (memory · emotions · teamwork · orchestration)
 *   4   CLI runtimes orchestrated (Claude Code · Codex · Gemini · Copilot)
 *   90+ MCP tools served over SSE
 *   5.5s chat p50 latency (88% faster than the pre-memory baseline)
 *
 * Reuses useCountUp (same hook as the main landing's MetricsStrip) and
 * the dark `--land-bg-dark` band so it visually matches the platform.
 */
import { useRef } from 'react';
import { motion, useInView, useReducedMotion } from 'framer-motion';
import { useCountUp } from '../hooks/useCountUp';

const STATS = [
  { target: 4, suffix: '', label: 'Engines in the substrate' },
  { target: 4, suffix: '', label: 'CLI runtimes orchestrated' },
  { target: 90, suffix: '+', label: 'MCP tools over SSE' },
  { target: 5.5, suffix: 's', label: 'Chat p50 latency', decimals: 1 },
];

function StatBlock({ target, suffix, label, decimals }) {
  const [ref, display] = useCountUp(target, 1500, { decimals });
  return (
    <div ref={ref} className="alpha-metrics__stat">
      <span className="alpha-metrics__value">{display}{suffix}</span>
      <span className="alpha-metrics__label">{label}</span>
    </div>
  );
}

export default function AlphaMetrics() {
  const sectionRef = useRef(null);
  const isInView = useInView(sectionRef, { once: true, margin: '-80px 0px' });
  const prefersReducedMotion = useReducedMotion();

  return (
    <motion.section
      ref={sectionRef}
      className="alpha-metrics"
      initial={prefersReducedMotion ? {} : { opacity: 0, y: 32 }}
      animate={isInView ? { opacity: 1, y: 0 } : {}}
      transition={{ duration: 0.5 }}
      aria-label="Platform metrics"
    >
      <div className="alpha-metrics__inner">
        {STATS.map((s) => (
          <StatBlock key={s.label} {...s} />
        ))}
      </div>
    </motion.section>
  );
}
