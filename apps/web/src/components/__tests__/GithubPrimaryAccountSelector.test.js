import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import GithubPrimaryAccountSelector from '../GithubPrimaryAccountSelector';

jest.mock('../../services/branding', () => ({
  brandingService: {
    getFeatures: jest.fn(),
    updateFeatures: jest.fn(),
  },
}));

const { brandingService } = require('../../services/branding');


describe('GithubPrimaryAccountSelector', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    brandingService.getFeatures.mockResolvedValue({ github_primary_account: null });
    brandingService.updateFeatures.mockResolvedValue({});
  });

  test('hidden when zero github accounts connected', async () => {
    const { container } = render(
      <GithubPrimaryAccountSelector configs={[]} credentialStatuses={{}} />,
    );
    await waitFor(() => expect(brandingService.getFeatures).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  test('hidden when only one github account connected', async () => {
    const configs = [
      { integration_name: 'github', enabled: true, account_email: 'a@b.com' },
    ];
    const { container } = render(
      <GithubPrimaryAccountSelector configs={configs} credentialStatuses={{}} />,
    );
    await waitFor(() => expect(brandingService.getFeatures).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  test('renders when ≥2 github accounts connected', async () => {
    const configs = [
      { integration_name: 'github', enabled: true, account_email: 'personal@me.com' },
      { integration_name: 'github', enabled: true, account_email: 'work@employer.com' },
    ];
    render(<GithubPrimaryAccountSelector configs={configs} credentialStatuses={{}} />);
    await waitFor(() => expect(screen.getByText(/GitHub primary account/)).toBeInTheDocument());
    const select = screen.getByLabelText(/GitHub primary account/i);
    const options = Array.from(select.querySelectorAll('option')).map((o) => o.value);
    expect(options).toContain('__auto__');
    expect(options).toContain('personal@me.com');
    expect(options).toContain('work@employer.com');
  });

  test('shows current pin loaded from features', async () => {
    brandingService.getFeatures.mockResolvedValue({ github_primary_account: 'work@employer.com' });
    const configs = [
      { integration_name: 'github', enabled: true, account_email: 'personal@me.com' },
      { integration_name: 'github', enabled: true, account_email: 'work@employer.com' },
    ];
    render(<GithubPrimaryAccountSelector configs={configs} credentialStatuses={{}} />);
    const select = await screen.findByLabelText(/GitHub primary account/i);
    expect(select.value).toBe('work@employer.com');
  });

  test('"Auto" sends null to clear the pin', async () => {
    brandingService.getFeatures.mockResolvedValue({ github_primary_account: 'work@employer.com' });
    const configs = [
      { integration_name: 'github', enabled: true, account_email: 'personal@me.com' },
      { integration_name: 'github', enabled: true, account_email: 'work@employer.com' },
    ];
    render(<GithubPrimaryAccountSelector configs={configs} credentialStatuses={{}} />);
    const select = await screen.findByLabelText(/GitHub primary account/i);
    fireEvent.change(select, { target: { value: '__auto__' } });
    await waitFor(() =>
      expect(brandingService.updateFeatures).toHaveBeenCalledWith({
        github_primary_account: null,
      }),
    );
  });

  test('save sends specific account_email to features endpoint', async () => {
    const configs = [
      { integration_name: 'github', enabled: true, account_email: 'personal@me.com' },
      { integration_name: 'github', enabled: true, account_email: 'work@employer.com' },
    ];
    render(<GithubPrimaryAccountSelector configs={configs} credentialStatuses={{}} />);
    const select = await screen.findByLabelText(/GitHub primary account/i);
    fireEvent.change(select, { target: { value: 'work@employer.com' } });
    await waitFor(() =>
      expect(brandingService.updateFeatures).toHaveBeenCalledWith({
        github_primary_account: 'work@employer.com',
      }),
    );
  });

  test('falls back to Auto when stored pin no longer connected', async () => {
    brandingService.getFeatures.mockResolvedValue({ github_primary_account: 'removed@me.com' });
    const configs = [
      { integration_name: 'github', enabled: true, account_email: 'personal@me.com' },
      { integration_name: 'github', enabled: true, account_email: 'work@employer.com' },
    ];
    render(<GithubPrimaryAccountSelector configs={configs} credentialStatuses={{}} />);
    const select = await screen.findByLabelText(/GitHub primary account/i);
    expect(select.value).toBe('__auto__');
  });

  test('shows admin-only error on 403 from PUT /features', async () => {
    brandingService.updateFeatures.mockRejectedValue({ response: { status: 403 } });
    const configs = [
      { integration_name: 'github', enabled: true, account_email: 'personal@me.com' },
      { integration_name: 'github', enabled: true, account_email: 'work@employer.com' },
    ];
    render(<GithubPrimaryAccountSelector configs={configs} credentialStatuses={{}} />);
    const select = await screen.findByLabelText(/GitHub primary account/i);
    fireEvent.change(select, { target: { value: 'work@employer.com' } });
    await waitFor(() => expect(screen.getByText(/Only admins/)).toBeInTheDocument());
  });

  test('disabled github configs do not count', async () => {
    const configs = [
      { integration_name: 'github', enabled: false, account_email: 'a@b.com' },
      { integration_name: 'github', enabled: true, account_email: 'b@b.com' },
    ];
    const { container } = render(
      <GithubPrimaryAccountSelector configs={configs} credentialStatuses={{}} />,
    );
    await waitFor(() => expect(brandingService.getFeatures).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });
});
