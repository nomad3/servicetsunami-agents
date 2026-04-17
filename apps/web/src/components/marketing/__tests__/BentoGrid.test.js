import { render, screen } from '@testing-library/react';
import { mockUseTranslation } from '../../../test-utils/i18nMock';
import BentoGrid from '../BentoGrid';

beforeAll(() => {
  global.IntersectionObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

jest.mock('react-i18next', () => ({ useTranslation: mockUseTranslation }));

test('renders all 7 feature card titles', () => {
  render(<BentoGrid />);
  expect(screen.getByText(/AI Command/i)).toBeInTheDocument();
  expect(screen.getByText(/Agent Memory/i)).toBeInTheDocument();
  expect(screen.getByText(/Multi-Agent Teams/i)).toBeInTheDocument();
  expect(screen.getByText(/Workflows/i)).toBeInTheDocument();
  expect(screen.getByText(/Enterprise Security/i)).toBeInTheDocument();
  expect(screen.getByText(/Inbox Monitor/i)).toBeInTheDocument();
  expect(screen.getByText(/Code Agent/i)).toBeInTheDocument();
});
