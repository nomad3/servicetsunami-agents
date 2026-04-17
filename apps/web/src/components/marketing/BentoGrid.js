import { useRef } from 'react';
import { motion, useInView, useReducedMotion } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import { FiDatabase, FiUsers, FiGitPullRequest, FiShield, FiMail } from 'react-icons/fi';
import BentoCard from './BentoCard';

const CARDS = [
  { id: 'ai-command', key: 'aiCommand', large: true },
  { id: 'memory', key: 'memory', icon: FiDatabase },
  { id: 'multi-agent', key: 'multiAgent', icon: FiUsers },
  { id: 'workflows', key: 'workflows', icon: FiGitPullRequest },
  { id: 'security', key: 'security', icon: FiShield },
  { id: 'inbox', key: 'inbox', icon: FiMail },
  { id: 'code-agent', key: 'codeAgent', large: true },
];

const stagger = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.08 } },
};
const item = {
  hidden: { opacity: 0, y: 20 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.5 } },
};

export default function BentoGrid() {
  const { t } = useTranslation('landing');
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: '-80px 0px' });
  const prefersReducedMotion = useReducedMotion();

  return (
    <section className="bento-section" id="platform">
      <div className="bento-section__inner">
        <h2 className="bento-section__heading">{t('bento.heading')}</h2>

        <motion.div
          ref={ref}
          className="bento-grid"
          variants={prefersReducedMotion ? {} : stagger}
          initial="hidden"
          animate={isInView ? 'visible' : 'hidden'}
        >
          {CARDS.map(card => (
            <motion.div
              key={card.id}
              className={`bento-${card.id}`}
              variants={prefersReducedMotion ? {} : item}
            >
              <BentoCard
                title={t(`bento.${card.key}.title`)}
                description={t(`bento.${card.key}.desc`)}
                icon={card.icon}
                large={card.large}
              />
            </motion.div>
          ))}
        </motion.div>
      </div>
    </section>
  );
}
