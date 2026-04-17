import { useState, useRef } from 'react';
import { motion, AnimatePresence, useInView, useReducedMotion } from 'framer-motion';
import { useTranslation } from 'react-i18next';

const TAB_IDS = ['dashboard', 'memory', 'chat', 'agents', 'workflows'];
const TAB_IMGS = {
  dashboard: '/images/product/dashboard.png',
  memory: '/images/product/memory.png',
  chat: '/images/product/chat.png',
  agents: '/images/product/agents.png',
  workflows: '/images/product/workflows.png',
};

export default function ProductDemo() {
  const { t } = useTranslation('landing');
  const [active, setActive] = useState('dashboard');
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: '-100px 0px' });
  const prefersReducedMotion = useReducedMotion();

  const tabs = TAB_IDS.map(id => ({
    id,
    label: t(`productDemo.tabs.${id}`),
    img: TAB_IMGS[id],
  }));
  const current = tabs.find(tab => tab.id === active);

  return (
    <section className="product-demo" id="features">
      <div className="product-demo__inner">
        <h2 className="product-demo__heading">{t('productDemo.heading')}</h2>

        <motion.div
          ref={ref}
          className="product-demo__mockup"
          initial={prefersReducedMotion ? {} : { scale: 0.95, opacity: 0 }}
          animate={isInView ? { scale: 1, opacity: 1 } : {}}
          transition={{ duration: 0.5, ease: 'easeOut' }}
        >
          <div className="product-demo__chrome">
            <span className="chrome-dot chrome-dot--red" />
            <span className="chrome-dot chrome-dot--yellow" />
            <span className="chrome-dot chrome-dot--green" />
            <span className="chrome-address">agentprovision.com</span>
          </div>
          <div className="product-demo__screen">
            <AnimatePresence mode="wait">
              <motion.img
                key={active}
                src={`${process.env.PUBLIC_URL}${current.img}`}
                alt={current.label}
                className="product-demo__screenshot"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.3 }}
              />
            </AnimatePresence>
          </div>
        </motion.div>

        {/* Tab pills */}
        <div className="product-demo__tabs" role="tablist">
          {tabs.map(tab => (
            <button
              key={tab.id}
              role="tab"
              aria-selected={active === tab.id}
              className={`product-demo__tab ${active === tab.id ? 'product-demo__tab--active' : ''}`}
              onClick={() => setActive(tab.id)}
            >
              {tab.label}
              {active === tab.id && (
                <motion.div
                  layoutId="tab-indicator"
                  className="product-demo__tab-indicator"
                  transition={{ type: 'spring', stiffness: 400, damping: 30 }}
                />
              )}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
