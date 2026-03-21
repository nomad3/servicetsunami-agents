import { useCallback, useEffect, useState } from 'react';
import { Badge, Container } from 'react-bootstrap';
import {
  FaChartBar,
  FaComments,
  FaDatabase,
  FaRobot,
  FaSitemap,
} from 'react-icons/fa';
import PremiumCard from '../common/PremiumCard';

const screenshots = [
  {
    key: 'dashboard',
    label: 'Analytics Overview',
    icon: FaChartBar,
    image: `${process.env.PUBLIC_URL}/images/product/dashboard.png`,
  },
  {
    key: 'memory',
    label: 'Agent Memory',
    icon: FaDatabase,
    image: `${process.env.PUBLIC_URL}/images/product/memory.png`,
  },
  {
    key: 'chat',
    label: 'AI Command',
    icon: FaComments,
    image: `${process.env.PUBLIC_URL}/images/product/chat.png`,
  },
  {
    key: 'agents/fleet',
    label: 'Agent Fleet',
    icon: FaRobot,
    image: `${process.env.PUBLIC_URL}/images/product/agents.png`,
  },
  {
    key: 'workflows',
    label: 'Workflows',
    icon: FaSitemap,
    image: `${process.env.PUBLIC_URL}/images/product/workflows.png`,
  },
];

const InteractivePreview = () => {
  const [activeIndex, setActiveIndex] = useState(0);
  const [isHovered, setIsHovered] = useState(false);

  const next = useCallback(() => {
    setActiveIndex((prev) => (prev + 1) % screenshots.length);
  }, []);

  useEffect(() => {
    if (isHovered) return;
    const timer = setInterval(next, 4000);
    return () => clearInterval(timer);
  }, [isHovered, next]);

  const active = screenshots[activeIndex];

  return (
    <section
      className="py-5 position-relative overflow-hidden"
      style={{
        background:
          'radial-gradient(circle at 50% 50%, #f5f8fc 0%, #e8eff6 100%)',
      }}
    >
      <div
        className="position-absolute top-0 start-0 w-100 h-100"
        style={{
          opacity: 0.1,
          backgroundImage: 'radial-gradient(#2b7de9 1px, transparent 1px)',
          backgroundSize: '30px 30px',
        }}
      />

      <Container className="position-relative z-2">
        <div className="text-center mb-5">
          <Badge
            bg="primary"
            className="mb-3 px-3 py-2 rounded-pill bg-opacity-25 text-primary border border-primary border-opacity-25"
          >
            PRODUCT TOUR
          </Badge>
          <h2 className="display-5 fw-bold mb-3" style={{ color: 'var(--color-foreground)' }}>
            See <span className="gradient-text">wolfpoint.ai</span> in Action
          </h2>
          <p className="text-soft lead mx-auto" style={{ maxWidth: '600px' }}>
            Explore the unified command center for data, AI agents, and
            enterprise operations.
          </p>
        </div>

        {/* Screenshot Navigation Pills */}
        <div className="d-flex flex-wrap justify-content-center gap-2 mb-4">
          {screenshots.map((s, idx) => {
            const Icon = s.icon;
            return (
              <button
                key={s.key}
                onClick={() => setActiveIndex(idx)}
                className="btn btn-sm d-flex align-items-center gap-2 rounded-pill px-3 py-2 border-0"
                style={{
                  background:
                    idx === activeIndex
                      ? 'rgba(43, 125, 233, 0.12)'
                      : 'rgba(43,125,233,0.04)',
                  color: idx === activeIndex ? '#2b7de9' : 'rgba(45,65,90,0.5)',
                  transition: 'all 0.3s ease',
                  fontSize: '0.8rem',
                  fontWeight: idx === activeIndex ? 600 : 400,
                }}
              >
                <Icon size={14} />
                <span className="d-none d-md-inline">{s.label}</span>
              </button>
            );
          })}
        </div>

        {/* Browser Frame with Screenshot */}
        <div
          className="perspective-container"
          style={{ perspective: '2000px' }}
          onMouseEnter={() => setIsHovered(true)}
          onMouseLeave={() => setIsHovered(false)}
        >
          <div
            className="dashboard-mockup mx-auto"
            style={{
              transform: 'rotateX(3deg)',
              transition: 'transform 0.5s ease',
              boxShadow: '0 50px 100px -20px rgba(100,130,170,0.2)',
              maxWidth: '1100px',
            }}
          >
            <PremiumCard
              className="p-0 overflow-hidden border-secondary border-opacity-25"
              style={{ background: 'rgba(255, 255, 255, 0.95)' }}
            >
              {/* Browser Chrome */}
              <div className="d-flex align-items-center justify-content-between px-4 py-3 border-bottom border-secondary border-opacity-25 bg-light bg-opacity-75">
                <div className="d-flex align-items-center gap-2">
                  <div
                    className="rounded-circle bg-danger"
                    style={{ width: '10px', height: '10px' }}
                  />
                  <div
                    className="rounded-circle bg-warning"
                    style={{ width: '10px', height: '10px' }}
                  />
                  <div
                    className="rounded-circle bg-success"
                    style={{ width: '10px', height: '10px' }}
                  />
                </div>
                <div className="text-soft small font-monospace">
                  wolfpoint.ai/{active.key}
                </div>
                <div className="d-flex align-items-center gap-2">
                  <active.icon className="text-primary" size={14} />
                  <span className="text-soft small fw-semibold">
                    {active.label}
                  </span>
                </div>
              </div>

              {/* Screenshot Display */}
              <div
                style={{
                  position: 'relative',
                  overflow: 'hidden',
                  background: '#f0f5fa',
                }}
              >
                <img
                  src={active.image}
                  alt={active.label}
                  style={{
                    width: '100%',
                    display: 'block',
                    transition: 'opacity 0.4s ease',
                  }}
                />
              </div>
            </PremiumCard>
          </div>
        </div>

        {/* Dot Indicators */}
        <div className="d-flex justify-content-center gap-2 mt-4">
          {screenshots.map((_, idx) => (
            <button
              key={idx}
              onClick={() => setActiveIndex(idx)}
              className="btn p-0 border-0"
              style={{
                width: idx === activeIndex ? '24px' : '8px',
                height: '8px',
                borderRadius: '4px',
                background:
                  idx === activeIndex
                    ? '#2b7de9'
                    : 'rgba(43,125,233,0.15)',
                transition: 'all 0.3s ease',
              }}
              aria-label={`View ${screenshots[idx].label}`}
            />
          ))}
        </div>
      </Container>
    </section>
  );
};

export default InteractivePreview;
