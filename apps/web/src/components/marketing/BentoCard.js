import { motion, useReducedMotion } from 'framer-motion';

export default function BentoCard({ title, description, icon: Icon, large, className = '', children }) {
  const prefersReducedMotion = useReducedMotion();

  return (
    <motion.div
      className={`bento-card ${large ? 'bento-card--large' : 'bento-card--small'} ${className}`}
      whileHover={prefersReducedMotion ? {} : { y: -4 }}
      transition={{ type: 'spring', stiffness: 400, damping: 17 }}
    >
      {large && <div className="bento-card__accent" />}
      {!large && Icon && (
        <div className="bento-card__icon-wrap">
          <Icon size={32} className="bento-card__icon" />
        </div>
      )}
      <h3 className="bento-card__title">{title}</h3>
      <p className="bento-card__desc">{description}</p>
      {children && <div className="bento-card__content">{children}</div>}
    </motion.div>
  );
}
