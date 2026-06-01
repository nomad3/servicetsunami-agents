import { render, screen } from '@testing-library/react';
import AlphaEngines from '../AlphaEngines';

beforeAll(() => {
  // framer-motion uses IntersectionObserver for whileInView.
  global.IntersectionObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

test('renders all four engines', () => {
  render(<AlphaEngines />);
  expect(screen.getByText('Memory engine')).toBeInTheDocument();
  expect(screen.getByText('Emotions engine')).toBeInTheDocument();
  expect(screen.getByText('Teamwork engine')).toBeInTheDocument();
  expect(screen.getByText('Orchestration engine')).toBeInTheDocument();
});

test('has the #engines anchor used by the reused LandingNav', () => {
  render(<AlphaEngines />);
  const section = document.getElementById('engines');
  expect(section).not.toBeNull();
});

test('keeps the emotions rollout honest — Now / Next / Later, Later flagged roadmap', () => {
  // Honesty guardrail: we must NOT present the "Later" stage as shipped.
  render(<AlphaEngines />);
  expect(screen.getByText('Now')).toBeInTheDocument();
  expect(screen.getByText('Next')).toBeInTheDocument();
  expect(screen.getByText('Later')).toBeInTheDocument();
  // The Later line is explicitly tagged as roadmap.
  expect(screen.getByText(/\(roadmap\)/i)).toBeInTheDocument();
});

test('leads with the "merge is the moat" thesis', () => {
  render(<AlphaEngines />);
  expect(screen.getByText(/merge is the moat/i)).toBeInTheDocument();
});
