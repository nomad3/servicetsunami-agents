import { useRef } from 'react';
import { motion, useInView, useReducedMotion } from 'framer-motion';
import { useCountUp } from './hooks/useCountUp';

const stats = [
  { key: 'tools', target: 81, suffix: '', label: 'MCP Tools' },
  { key: 'workflows', target: 25, suffix: '+', label: 'Native Workflows' },
  { key: 'responseTime', target: 5.5, suffix: 's', label: 'Avg Response Time', decimal: true },
  { key: 'improvement', target: 88, suffix: '%', label: 'Faster Than Baseline' },
];

function StatBlock({ target, suffix, label, decimal }) {
  const [ref, display] = useCountUp(target, 1500);
  const val = decimal ? parseFloat(display).toFixed(1) : display;
  return (
    <div ref={ref} className="metrics-stat">
      <span className="metrics-stat__value">{val}{suffix}</span>
      <span className="metrics-stat__label">{label}</span>
    </div>
  );
}

export default function MetricsStrip() {
  const sectionRef = useRef(null);
  const isInView = useInView(sectionRef, { once: true, margin: '-80px 0px' });
  const prefersReducedMotion = useReducedMotion();

  return (
    <motion.section
      ref={sectionRef}
      className="metrics-strip"
      initial={prefersReducedMotion ? {} : { opacity: 0, y: 40 }}
      animate={isInView ? { opacity: 1, y: 0 } : {}}
      transition={{ duration: 0.5 }}
    >
      <div className="metrics-strip__inner">
        {stats.map(s => <StatBlock key={s.key} {...s} />)}
      </div>
    </motion.section>
  );
}
