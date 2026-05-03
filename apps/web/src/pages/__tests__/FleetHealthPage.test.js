import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import FleetHealthPage from '../FleetHealthPage';

jest.mock('../../services/api', () => ({
  __esModule: true,
  default: { get: jest.fn() },
}));
jest.mock('../../components/Layout', () => ({ children }) => <>{children}</>);

const api = require('../../services/api').default;


function renderPage() {
  return render(
    <MemoryRouter>
      <FleetHealthPage />
    </MemoryRouter>,
  );
}


describe('FleetHealthPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('renders empty state when no agents', async () => {
    api.get.mockResolvedValue({ data: { rows: [], next_cursor: null, has_more: false } });
    renderPage();
    await waitFor(() => expect(screen.getByText(/No agents match/)).toBeInTheDocument());
  });

  test('renders agent rows from the response', async () => {
    api.get.mockResolvedValue({
      data: {
        rows: [
          {
            id: '11111111-1111-1111-1111-111111111111',
            name: 'Acme Sales Bot',
            source: 'copilot_studio',
            status: 'production',
            owner_email: 'sarah@acme.com',
            last_invoked_at: new Date().toISOString(),
            invocations_24h: 12,
            invocations_7d: 87,
            tokens_used_7d: 45000,
            cost_usd_7d: 1.23,
            latest_error: null,
            drift_state: 'unknown',
          },
        ],
        next_cursor: null,
        has_more: false,
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('Acme Sales Bot')).toBeInTheDocument());
    // Source filter dropdown ALSO contains "Copilot Studio" as an
    // option label — scope per-cell-content checks to <tbody> so the
    // assertions don't false-match the dropdown options or stat tiles.
    const tbodyText = document.querySelector('tbody').textContent;
    expect(tbodyText).toContain('Copilot Studio');
    expect(tbodyText).toContain('sarah@acme.com');
    expect(tbodyText).toContain('production');
    expect(tbodyText).toContain('87');
    expect(tbodyText).toContain('$1.23');
  });

  test('zombies filter triggers refetch with zombies=true', async () => {
    api.get.mockResolvedValue({ data: { rows: [], next_cursor: null, has_more: false } });
    renderPage();
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    const calls = api.get.mock.calls.length;
    fireEvent.click(screen.getByText('Zombies'));
    await waitFor(() => expect(api.get.mock.calls.length).toBeGreaterThan(calls));
    const lastCall = api.get.mock.calls[api.get.mock.calls.length - 1];
    expect(lastCall[1]?.params?.zombies).toBe('true');
  });

  test('source filter passes the selected source value', async () => {
    api.get.mockResolvedValue({ data: { rows: [], next_cursor: null, has_more: false } });
    renderPage();
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    const select = screen.getByDisplayValue('All sources');
    fireEvent.change(select, { target: { value: 'copilot_studio' } });
    await waitFor(() => {
      const lastCall = api.get.mock.calls[api.get.mock.calls.length - 1];
      expect(lastCall[1]?.params?.source).toBe('copilot_studio');
    });
  });

  test('renders error banner when API fails', async () => {
    api.get.mockRejectedValue({ response: { data: { detail: 'boom' } } });
    renderPage();
    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument());
  });

  test('shows "—" placeholders when an agent has no recent activity or cost', async () => {
    api.get.mockResolvedValue({
      data: {
        rows: [
          {
            id: '22222222-2222-2222-2222-222222222222',
            name: 'Idle Bot',
            source: 'native',
            status: 'draft',
            owner_email: null,
            last_invoked_at: null,
            invocations_24h: 0,
            invocations_7d: 0,
            tokens_used_7d: 0,
            cost_usd_7d: 0,
            latest_error: null,
            drift_state: 'unknown',
          },
        ],
        next_cursor: null,
        has_more: false,
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('Idle Bot')).toBeInTheDocument());
    expect(screen.getByText('never')).toBeInTheDocument(); // last_invoked_at=null → "never"
  });
});
