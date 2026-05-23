import { render, screen } from '@testing-library/react';
import AlphaCommands from '../AlphaCommands';

beforeAll(() => {
  global.IntersectionObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

test('renders 7 command cards covering each shipped surface', () => {
  render(<AlphaCommands />);
  // Locked by the roadmap: 7 differentiator cards (policy removed in
  // P0b — 2026-05-23 — see docs/plans/2026-05-23-p0b-agent-policy-decision.md).
  expect(screen.getByText('alpha run')).toBeInTheDocument();
  expect(screen.getByText('alpha run --fanout')).toBeInTheDocument();
  expect(screen.getByText('alpha recall')).toBeInTheDocument();
  expect(screen.getByText('alpha remember')).toBeInTheDocument();
  expect(screen.getByText('alpha coalition')).toBeInTheDocument();
  expect(screen.getByText('alpha recipes')).toBeInTheDocument();
  expect(screen.getByText('alpha usage / costs')).toBeInTheDocument();
});

test('each card has a title and an example line', () => {
  render(<AlphaCommands />);
  // Spot-check one card end-to-end.
  expect(screen.getByText('Durable tasks')).toBeInTheDocument();
  expect(
    screen.getByText(/alpha run "refactor auth" --background/)
  ).toBeInTheDocument();
});
