import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import GithubSshKeyCard from '../GithubSshKeyCard';

jest.mock('../../services/integrationConfigService', () => ({
  __esModule: true,
  default: {
    githubSshKeyStatus: jest.fn(),
    githubSshKeySave: jest.fn(),
    githubSshKeyDelete: jest.fn(),
  },
}));

const svc = require('../../services/integrationConfigService').default;

beforeEach(() => {
  jest.clearAllMocks();
});

describe('GithubSshKeyCard', () => {
  test('no key: shows Add button, opens the paste form, saves and refreshes', async () => {
    svc.githubSshKeyStatus
      .mockResolvedValueOnce({ data: { present: false, fingerprint: null } })
      .mockResolvedValueOnce({ data: { present: true, fingerprint: 'SHA256:abc' } });
    svc.githubSshKeySave.mockResolvedValue({ data: { status: 'saved', fingerprint: 'SHA256:abc' } });

    render(<GithubSshKeyCard />);
    const addBtn = await screen.findByRole('button', { name: /Add SSH key/i });
    fireEvent.click(addBtn);

    const textarea = await screen.findByPlaceholderText(/OPENSSH PRIVATE KEY/i);
    fireEvent.change(textarea, { target: { value: 'fake-key' } });
    fireEvent.click(screen.getByRole('button', { name: /Save key/i }));

    await waitFor(() => expect(svc.githubSshKeySave).toHaveBeenCalledWith('fake-key'));
    // after save it re-fetches status → now present → shows the fingerprint
    expect(await screen.findByText('SHA256:abc')).toBeInTheDocument();
  });

  test('present key: shows fingerprint + Remove', async () => {
    svc.githubSshKeyStatus.mockResolvedValue({ data: { present: true, fingerprint: 'SHA256:xyz' } });
    render(<GithubSshKeyCard />);
    expect(await screen.findByText('SHA256:xyz')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Remove/i })).toBeInTheDocument();
  });

  test('surfaces the API rejection detail (e.g. passphrase key)', async () => {
    svc.githubSshKeyStatus.mockResolvedValue({ data: { present: false, fingerprint: null } });
    svc.githubSshKeySave.mockRejectedValue({
      response: { data: { detail: 'passphrase-protected SSH keys are not supported' } },
    });
    render(<GithubSshKeyCard />);
    fireEvent.click(await screen.findByRole('button', { name: /Add SSH key/i }));
    fireEvent.change(await screen.findByPlaceholderText(/OPENSSH PRIVATE KEY/i), { target: { value: 'enc' } });
    fireEvent.click(screen.getByRole('button', { name: /Save key/i }));
    expect(await screen.findByText(/passphrase-protected/i)).toBeInTheDocument();
  });
});
