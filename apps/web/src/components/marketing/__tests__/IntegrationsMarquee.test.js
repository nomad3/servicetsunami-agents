import { render, screen } from '@testing-library/react';
import { mockUseTranslation } from '../../../test-utils/i18nMock';
import IntegrationsMarquee from '../IntegrationsMarquee';

jest.mock('react-i18next', () => ({ useTranslation: mockUseTranslation }));

test('renders section heading', () => {
  render(<IntegrationsMarquee />);
  expect(screen.getByText(/Connects to everything/i)).toBeInTheDocument();
});
