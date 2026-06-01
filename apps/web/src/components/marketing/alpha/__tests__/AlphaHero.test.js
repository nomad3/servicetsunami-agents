import { render, screen } from '@testing-library/react';
import AlphaHero from '../AlphaHero';

// Mock the analytics module so its track() calls during render don't
// try to load Plausible in jsdom.
jest.mock('../../../../services/marketingAnalytics', () => ({
  track: jest.fn(),
}));

test('renders the hero headline', () => {
  render(<AlphaHero />);
  expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument();
});

test('renders the copyable install command', () => {
  render(<AlphaHero />);
  expect(
    screen.getByText(/curl -fsSL https:\/\/agentprovision\.com\/install\.sh \| sh/)
  ).toBeInTheDocument();
  // Copy button is accessible by label.
  expect(
    screen.getByRole('button', { name: /copy install command/i })
  ).toBeInTheDocument();
});

test('primary CTA links to the apex (not subdomain-relative)', () => {
  // PR #450 BLOCKER B1: alpha CTAs must point at the apex so auth
  // flows resolve. Locks the contract — the wrapping <a> href must
  // be the absolute agentprovision.com URL, not a relative /register
  // that would 404 on the alpha subdomain.
  render(<AlphaHero />);
  const cta = screen.getByText(/start free/i).closest('a');
  expect(cta).not.toBeNull();
  expect(cta.getAttribute('href')).toBe('https://agentprovision.com/register');
});

test('secondary CTA is an on-page "How it works" anchor', () => {
  // 2026-05-31 redesign (Codex review): one primary CTA before trust is
  // earned; the second is an in-page jump to the engines section, not a
  // competing GitHub/source link.
  render(<AlphaHero />);
  const how = screen.getByText(/how it works/i).closest('a');
  expect(how).not.toBeNull();
  expect(how.getAttribute('href')).toBe('#engines');
});

test('hero headline carries the empathic-teammate spine', () => {
  render(<AlphaHero />);
  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/trusted teammates/i);
});
