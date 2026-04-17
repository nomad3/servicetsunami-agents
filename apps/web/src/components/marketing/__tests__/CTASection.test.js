import { render, screen } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { mockUseTranslation } from '../../../test-utils/i18nMock';
import CTASection from '../CTASection';

beforeAll(() => {
  global.IntersectionObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

jest.mock('react-i18next', () => ({ useTranslation: mockUseTranslation }));

test('renders CTA button', () => {
  render(<CTASection />, { wrapper: ({ children }) => <BrowserRouter>{children}</BrowserRouter> });
  expect(screen.getByText(/Get Started Free/i)).toBeInTheDocument();
});
