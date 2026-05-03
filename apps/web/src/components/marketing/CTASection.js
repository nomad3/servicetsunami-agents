import { useRef } from 'react';
import { motion, useInView, useReducedMotion } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { track } from '../../services/marketingAnalytics';

export default function CTASection() {
  const { t } = useTranslation('landing');
  const navigate = useNavigate();
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: '-80px 0px' });
  const prefersReducedMotion = useReducedMotion();

  return (
    <section className="cta-v2">
      <motion.div
        ref={ref}
        className="cta-v2__inner"
        initial={prefersReducedMotion ? {} : { opacity: 0, scale: 0.98 }}
        animate={isInView ? { opacity: 1, scale: 1 } : {}}
        transition={{ duration: 0.5 }}
      >
        <h2 className="cta-v2__heading">{t('cta.heading')}</h2>
        <p className="cta-v2__sub">{t('cta.subtext')}</p>
        <motion.button
          className="cta-v2__btn"
          onClick={() => { track('cta_get_started_click', { location: 'footer_cta' }); navigate('/register'); }}
          whileHover={prefersReducedMotion ? {} : { scale: 1.02 }}
          whileTap={prefersReducedMotion ? {} : { scale: 0.98 }}
          transition={{ type: 'spring', stiffness: 400, damping: 17 }}
        >
          {t('cta.button')}
        </motion.button>
      </motion.div>
    </section>
  );
}
