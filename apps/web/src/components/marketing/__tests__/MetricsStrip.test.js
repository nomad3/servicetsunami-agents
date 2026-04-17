import { render, screen } from '@testing-library/react';
import MetricsStrip from '../MetricsStrip';

// jsdom does not implement IntersectionObserver (used by framer-motion useInView)
beforeAll(() => {
  global.IntersectionObserver = class IntersectionObserver {
    constructor() {}
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

test('renders all 4 stat labels', () => {
  render(<MetricsStrip />);
  expect(screen.getByText(/MCP Tools/i)).toBeInTheDocument();
  expect(screen.getByText(/Native Workflows/i)).toBeInTheDocument();
  expect(screen.getByText(/Avg Response Time/i)).toBeInTheDocument();
  expect(screen.getByText(/Faster Than Baseline/i)).toBeInTheDocument();
});
