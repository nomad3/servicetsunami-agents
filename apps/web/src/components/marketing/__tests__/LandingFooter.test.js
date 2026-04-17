import { render, screen } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { mockUseTranslation } from '../../../test-utils/i18nMock';
import LandingFooter from '../LandingFooter';

jest.mock('react-i18next', () => ({ useTranslation: mockUseTranslation }));

test('renders footer with nav links', () => {
  render(<LandingFooter />, { wrapper: ({ children }) => <BrowserRouter>{children}</BrowserRouter> });
  expect(screen.getAllByText(/AgentProvision/i).length).toBeGreaterThan(0);
  expect(screen.getByText(/Platform/i)).toBeInTheDocument();
});
