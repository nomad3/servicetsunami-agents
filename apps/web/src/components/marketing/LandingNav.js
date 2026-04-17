import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

const navLinks = ['platform', 'features', 'integrations', 'pricing'];

export default function LandingNav() {
  const { t } = useTranslation('landing');
  const navigate = useNavigate();
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const handler = () => setScrolled(window.scrollY > 50);
    window.addEventListener('scroll', handler, { passive: true });
    return () => window.removeEventListener('scroll', handler);
  }, []);

  return (
    <motion.nav
      className={`landing-nav ${scrolled ? 'landing-nav--scrolled' : ''}`}
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
    >
      <div className="landing-nav__inner">
        <span className="landing-nav__logo">AgentProvision</span>

        <div className="landing-nav__links">
          {navLinks.map((key, i) => (
            <motion.a
              key={key}
              href={`#${key}`}
              className="landing-nav__link"
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.06 + 0.2 }}
            >
              {t(`nav.${key}`)}
            </motion.a>
          ))}
        </div>

        <div className="landing-nav__actions">
          <button className="landing-nav__signin" onClick={() => navigate('/login')}>
            {t('nav.signIn')}
          </button>
          <motion.button
            className="landing-nav__cta"
            onClick={() => navigate('/register')}
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.97 }}
            transition={{ type: 'spring', stiffness: 400, damping: 17 }}
          >
            {t('nav.getStarted')}
          </motion.button>
        </div>
      </div>
    </motion.nav>
  );
}
