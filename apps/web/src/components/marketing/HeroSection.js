import { useRef } from 'react';
import { motion, useScroll, useTransform, useReducedMotion } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';

const VIDEO_SRC = '/images/hero-transition.mp4';
const VIDEO_POSTER = '/images/Gemini_Generated_Image_fovh8nfovh8nfovh.png';

export default function HeroSection() {
  const { t } = useTranslation('landing');
  const heroRef = useRef(null);
  const prefersReducedMotion = useReducedMotion();

  const { scrollYProgress } = useScroll({
    target: heroRef,
    offset: ['start start', 'end start'],
  });

  const textOpacity = useTransform(scrollYProgress, [0, 0.45], [1, 0]);
  const textY = useTransform(scrollYProgress, [0, 0.45], ['0px', '-36px']);

  return (
    <section ref={heroRef} className="hero-scroll">
      <video
        className="hero-scroll__video"
        autoPlay
        muted
        loop
        playsInline
        poster={`${process.env.PUBLIC_URL}${VIDEO_POSTER}`}
      >
        <source src={`${process.env.PUBLIC_URL}${VIDEO_SRC}`} type="video/mp4" />
      </video>

      <div className="hero-scroll__overlay" />

      <div className="hero-scroll__content">
        <motion.div
          className="hero-scroll__text-block"
          style={prefersReducedMotion ? {} : { opacity: textOpacity, y: textY }}
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
      </div>
    </section>
  );
}
