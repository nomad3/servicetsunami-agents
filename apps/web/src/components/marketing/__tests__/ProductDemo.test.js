import { render, screen, fireEvent } from '@testing-library/react';
import { mockUseTranslation } from '../../../test-utils/i18nMock';
import ProductDemo from '../ProductDemo';

beforeAll(() => {
  global.IntersectionObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

jest.mock('react-i18next', () => ({ useTranslation: mockUseTranslation }));

test('renders all 5 tab labels', () => {
  render(<ProductDemo />);
  expect(screen.getByText(/Dashboard/i)).toBeInTheDocument();
  expect(screen.getByText(/Agent Memory/i)).toBeInTheDocument();
  expect(screen.getByText(/AI Command/i)).toBeInTheDocument();
  expect(screen.getByText(/Agent Fleet/i)).toBeInTheDocument();
  expect(screen.getByText(/Workflows/i)).toBeInTheDocument();
});

test('clicking a tab updates active state', () => {
  render(<ProductDemo />);
  const memoryTab = screen.getByText(/Agent Memory/i);
  fireEvent.click(memoryTab);
  expect(memoryTab.closest('button')).toHaveClass('product-demo__tab--active');
});
