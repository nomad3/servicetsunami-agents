import { useRef } from 'react';
import { motion, useInView, useReducedMotion } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import { useCountUp } from './hooks/useCountUp';

const STATS = [
  { key: 'tools', target: 81, suffix: '' },
  { key: 'workflows', target: 25, suffix: '+' },
  { key: 'responseTime', target: 5.5, suffix: 's', decimals: 1 },
  { key: 'improvement', target: 88, suffix: '%' },
];

function StatBlock({ statKey, target, suffix, decimals }) {
  const { t } = useTranslation('landing');
  const [ref, display] = useCountUp(target, 1500, { decimals });
  return (
    <div ref={ref} className="metrics-stat">
      <span className="metrics-stat__value">{display}{suffix}</span>
      <span className="metrics-stat__label">{t(`statsStrip.${statKey}.label`)}</span>
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
        {STATS.map(({ key, ...s }) => (
          <StatBlock key={key} statKey={key} {...s} />
        ))}
      </div>
    </motion.section>
  );
}
