import { render, screen } from '@testing-library/react';
import AlphaRealityLedger from '../AlphaRealityLedger';

beforeAll(() => {
  // framer-motion uses IntersectionObserver for whileInView.
  global.IntersectionObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

test('renders the three honesty columns', () => {
  render(<AlphaRealityLedger />);
  expect(screen.getByText(/Live now/i)).toBeInTheDocument();
  expect(screen.getByText(/In alpha/i)).toBeInTheDocument();
  expect(screen.getByText(/Research/i)).toBeInTheDocument();
});

test('exposes the #reality anchor for the nav', () => {
  const { container } = render(<AlphaRealityLedger />);
  expect(container.querySelector('#reality')).not.toBeNull();
});

test('keeps fleet-wide emotion honest (in Next, not Live)', () => {
  // The hero/Engines must NOT claim fleet-wide emotional coordination as
  // shipped; the ledger places it explicitly under Research/next.
  render(<AlphaRealityLedger />);
  expect(screen.getByText(/Fleet-wide emotional coordination/i)).toBeInTheDocument();
});
