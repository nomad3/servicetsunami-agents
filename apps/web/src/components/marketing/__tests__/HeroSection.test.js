import { render, screen } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { mockUseTranslation } from '../../../test-utils/i18nMock';
import HeroSection from '../HeroSection';

jest.mock('react-i18next', () => ({ useTranslation: mockUseTranslation }));

const Wrapper = ({ children }) => <BrowserRouter>{children}</BrowserRouter>;

test('renders headline and CTAs', () => {
  render(<HeroSection />, { wrapper: Wrapper });
  expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument();
  expect(screen.getByText(/Get Started/i)).toBeInTheDocument();
});
