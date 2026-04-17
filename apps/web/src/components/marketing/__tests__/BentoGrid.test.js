import { render, screen } from '@testing-library/react';
import BentoGrid from '../BentoGrid';

// jsdom does not implement IntersectionObserver (used by framer-motion useInView)
beforeAll(() => {
  global.IntersectionObserver = class IntersectionObserver {
    constructor() {}
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

test('renders all 7 feature card titles', () => {
  render(<BentoGrid />);
  expect(screen.getByText(/AI Command/i)).toBeInTheDocument();
  expect(screen.getByText(/Agent Memory/i)).toBeInTheDocument();
  expect(screen.getByText(/Multi-Agent/i)).toBeInTheDocument();
  expect(screen.getByText(/Workflows/i)).toBeInTheDocument();
  expect(screen.getByText(/Security/i)).toBeInTheDocument();
  expect(screen.getByText(/Inbox Monitor/i)).toBeInTheDocument();
  expect(screen.getByText(/Code Agent/i)).toBeInTheDocument();
});
