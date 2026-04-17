import { useRef } from 'react';
import { motion, useScroll, useTransform, useReducedMotion } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';

const IMG_SOLO = '/images/Gemini_Generated_Image_lka21blka21blka2-2.png';
const IMG_PACK = '/images/Gemini_Generated_Image_fovh8nfovh8nfovh.png';

export default function HeroSection() {
  const { t } = useTranslation('landing');
  const heroRef = useRef(null);
  const prefersReducedMotion = useReducedMotion();

  const { scrollYProgress } = useScroll({
    target: heroRef,
    offset: ['start start', 'end start'],
  });

  const img1Opacity = useTransform(scrollYProgress, [0, 0.5], [1, 0]);
  const img2Opacity = useTransform(scrollYProgress, [0.15, 0.6], [0, 1]);
  const text1Opacity = useTransform(scrollYProgress, [0, 0.25], [1, 0]);
  const text1Y = useTransform(scrollYProgress, [0, 0.25], ['0px', '-40px']);
  const text2Opacity = useTransform(scrollYProgress, [0.25, 0.55], [0, 1]);
  const text2Y = useTransform(scrollYProgress, [0.25, 0.55], ['40px', '0px']);

  return (
    <section ref={heroRef} className="hero-scroll">
      <motion.img
        src={`${process.env.PUBLIC_URL}${IMG_SOLO}`}
        alt=""
        aria-hidden="true"
        className="hero-scroll__img"
        style={{ opacity: prefersReducedMotion ? 0 : img1Opacity }}
      />
      <motion.img
        src={`${process.env.PUBLIC_URL}${IMG_PACK}`}
        alt="A coordinated pack of AI agents"
        className="hero-scroll__img"
        style={{ opacity: prefersReducedMotion ? 1 : img2Opacity }}
      />

      <div className="hero-scroll__overlay" />

      <div className="hero-scroll__content">
        <motion.div
          className="hero-scroll__text-block"
          style={
            prefersReducedMotion
              ? { opacity: 0, pointerEvents: 'none' }
              : { opacity: text1Opacity, y: text1Y }
          }
        >
          <h1 className="hero-scroll__title">{t('hero.title')}</h1>
          <div className="hero-scroll__ctas">
            <Link to="/register" style={{ pointerEvents: 'auto' }}>
              <button className="hero-scroll__cta-primary">{t('nav.getStarted')}</button>
            </Link>
            <Link to="/login" style={{ pointerEvents: 'auto' }}>
              <button className="hero-scroll__cta-ghost">{t('nav.signIn')}</button>
            </Link>
          </div>
        </motion.div>

        <motion.div
          className="hero-scroll__text-block"
          style={
            prefersReducedMotion
              ? { opacity: 1 }
              : { opacity: text2Opacity, y: text2Y }
          }
        >
          <p className="hero-scroll__lead">{t('hero.lead')}</p>
        </motion.div>
      </div>
    </section>
  );
}
