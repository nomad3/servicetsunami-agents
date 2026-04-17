import { render, screen } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { mockUseTranslation } from '../../../test-utils/i18nMock';
import LandingNav from '../LandingNav';

jest.mock('react-i18next', () => ({ useTranslation: mockUseTranslation }));

const Wrapper = ({ children }) => <BrowserRouter>{children}</BrowserRouter>;

test('renders nav links', () => {
  render(<LandingNav />, { wrapper: Wrapper });
  expect(screen.getByText(/Get Started/i)).toBeInTheDocument();
  expect(screen.getByText(/Sign In/i)).toBeInTheDocument();
});
