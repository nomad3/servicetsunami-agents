import { motion, useReducedMotion } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

export default function HeroSection() {
  const { t } = useTranslation('landing');
  const navigate = useNavigate();
  const prefersReducedMotion = useReducedMotion();

  const fadeUp = prefersReducedMotion
    ? { hidden: { opacity: 1, y: 0 }, visible: { opacity: 1, y: 0 } }
    : { hidden: { opacity: 0, y: 24 }, visible: { opacity: 1, y: 0 } };

  const slideRight = prefersReducedMotion
    ? { hidden: { opacity: 1, x: 0 }, visible: { opacity: 1, x: 0 } }
    : { hidden: { opacity: 0, x: 40 }, visible: { opacity: 1, x: 0 } };

  return (
    <section className="hero-v2" id="hero">
      {/* Dot grid background */}
      <div className="hero-v2__dotgrid" aria-hidden="true" />

      <div className="hero-v2__inner">
        {/* Left column */}
        <motion.div
          className="hero-v2__left"
          variants={fadeUp}
          initial="hidden"
          animate="visible"
          transition={{ duration: 0.5, ease: 'easeOut' }}
        >
          <motion.span
            className="hero-v2__badge"
            variants={fadeUp}
            transition={{ delay: 0.1 }}
          >
            {t('hero.badge')}
          </motion.span>

          <motion.h1
            className="hero-v2__headline"
            variants={fadeUp}
            transition={{ delay: 0.2 }}
          >
            {t('hero.title')}
          </motion.h1>

          <motion.p
            className="hero-v2__sub"
            variants={fadeUp}
            transition={{ delay: 0.3 }}
          >
            {t('hero.lead')}
          </motion.p>

          <motion.div
            className="hero-v2__ctas"
            variants={fadeUp}
            transition={{ delay: 0.4 }}
          >
            <motion.button
              className="hero-v2__cta-primary"
              onClick={() => navigate('/register')}
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.97 }}
              transition={{ type: 'spring', stiffness: 400, damping: 17 }}
            >
              {t('nav.getStarted')}
            </motion.button>
            <motion.button
              className="hero-v2__cta-ghost"
              onClick={() => navigate('/login')}
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.97 }}
              transition={{ type: 'spring', stiffness: 400, damping: 17 }}
            >
              {t('nav.signIn')} →
            </motion.button>
          </motion.div>

          <motion.p
            className="hero-v2__social-proof"
            variants={fadeUp}
            transition={{ delay: 0.5 }}
          >
            {t('hero.socialProofFallback')}
          </motion.p>
        </motion.div>

        {/* Right column — browser chrome mockup */}
        <motion.div
          className="hero-v2__right"
          variants={slideRight}
          initial="hidden"
          animate="visible"
          transition={{ type: 'spring', stiffness: 100, damping: 20, delay: 0.15 }}
        >
          <motion.div
            className="hero-v2__browser"
            animate={prefersReducedMotion ? {} : { y: [0, -8, 0] }}
            transition={{ repeat: Infinity, repeatType: 'reverse', duration: 6, ease: 'easeInOut' }}
          >
            <div className="hero-v2__browser-chrome">
              <span className="chrome-dot chrome-dot--red" />
              <span className="chrome-dot chrome-dot--yellow" />
              <span className="chrome-dot chrome-dot--green" />
              <span className="chrome-address">agentprovision.com/chat</span>
            </div>
            <img
              src={`${process.env.PUBLIC_URL}/images/product/chat.png`}
              alt="AgentProvision AI chat"
              className="hero-v2__screenshot"
            />
          </motion.div>
        </motion.div>
      </div>
    </section>
  );
}
