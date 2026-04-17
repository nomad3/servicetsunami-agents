import { render, screen } from '@testing-library/react';
import { mockUseTranslation } from '../../../test-utils/i18nMock';
import MetricsStrip from '../MetricsStrip';

beforeAll(() => {
  global.IntersectionObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

jest.mock('react-i18next', () => ({ useTranslation: mockUseTranslation }));

test('renders all 4 stat labels', () => {
  render(<MetricsStrip />);
  expect(screen.getByText(/MCP Tools/i)).toBeInTheDocument();
  expect(screen.getByText(/Native Workflows/i)).toBeInTheDocument();
  expect(screen.getByText(/Avg Response Time/i)).toBeInTheDocument();
  expect(screen.getByText(/Faster Than Baseline/i)).toBeInTheDocument();
});
