import { useRef } from 'react';
import { motion, useInView, useReducedMotion } from 'framer-motion';
import { FiDatabase, FiUsers, FiGitPullRequest, FiShield, FiMail, FiTerminal } from 'react-icons/fi';
import BentoCard from './BentoCard';

const cards = [
  { id: 'ai-command', title: 'AI Command', desc: 'Chat-driven agent orchestration. Dispatch multi-step tasks in plain language and watch your agent network execute.', large: true },
  { id: 'memory', title: 'Agent Memory', desc: 'Persistent knowledge graph. Every interaction builds context.', icon: FiDatabase },
  { id: 'multi-agent', title: 'Multi-Agent Teams', desc: '5 specialized teams, zero coordination overhead.', icon: FiUsers },
  { id: 'workflows', title: 'Workflows', desc: 'Visual no-code workflow builder with 25 native templates.', icon: FiGitPullRequest },
  { id: 'security', title: 'Enterprise Security', desc: 'Multi-tenant isolation, encrypted credential vault, JWT auth.', icon: FiShield },
  { id: 'inbox', title: 'Inbox Monitor', desc: 'Proactive email and calendar monitoring, 24/7.', icon: FiMail },
  { id: 'code-agent', title: 'Code Agent', desc: 'Autonomous coding powered by Claude Code CLI. Creates PRs with full audit trails.', large: true },
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
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: '-80px 0px' });
  const prefersReducedMotion = useReducedMotion();

  return (
    <section className="bento-section" id="platform">
      <div className="bento-section__inner">
        <h2 className="bento-section__heading">Everything your team needs</h2>

        <motion.div
          ref={ref}
          className="bento-grid"
          variants={prefersReducedMotion ? {} : stagger}
          initial="hidden"
          animate={isInView ? 'visible' : 'hidden'}
        >
          {cards.map(card => (
            <motion.div
              key={card.id}
              className={`bento-${card.id}`}
              variants={prefersReducedMotion ? {} : item}
            >
              <BentoCard
                title={card.title}
                description={card.desc}
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
