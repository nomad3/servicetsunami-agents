import { render, screen } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { mockUseTranslation } from '../../../test-utils/i18nMock';
import HeroSection from '../HeroSection';

beforeAll(() => {
  global.IntersectionObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

jest.mock('react-i18next', () => ({ useTranslation: mockUseTranslation }));

const Wrapper = ({ children }) => <BrowserRouter>{children}</BrowserRouter>;

test('renders hero title and CTAs', () => {
  render(<HeroSection />, { wrapper: Wrapper });
  expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument();
  expect(screen.getByText(/Get Started/i)).toBeInTheDocument();
  expect(screen.getByText(/Sign In/i)).toBeInTheDocument();
});

test('renders background video', () => {
  render(<HeroSection />, { wrapper: Wrapper });
  const video = document.querySelector('video');
  expect(video).toBeInTheDocument();
  expect(video).toHaveAttribute('autoplay');
  expect(video.muted).toBe(true);
  expect(video).toHaveAttribute('loop');
});
