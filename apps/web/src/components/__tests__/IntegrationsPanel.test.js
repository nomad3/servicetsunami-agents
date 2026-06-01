import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import IntegrationsPanel from '../IntegrationsPanel';

// ── Boundary mocks ────────────────────────────────────────────────────
jest.mock('../../services/integrationConfigService', () => ({
  __esModule: true,
  default: {
    getRegistry: jest.fn(),
    getAll: jest.fn(),
    create: jest.fn(),
    update: jest.fn(),
    remove: jest.fn(),
    addCredential: jest.fn(),
    revokeCredential: jest.fn(),
    getCredentialStatus: jest.fn(),
    oauthAuthorize: jest.fn(),
    oauthDisconnect: jest.fn(),
    oauthStatus: jest.fn(),
    codexAuthStart: jest.fn(),
    codexAuthStatus: jest.fn(),
    codexAuthCancel: jest.fn(),
    claudeAuthStart: jest.fn(),
    claudeAuthStatus: jest.fn(),
    claudeAuthCancel: jest.fn(),
    geminiCliAuthStart: jest.fn(),
    geminiCliAuthStatus: jest.fn(),
    geminiCliAuthSubmitCode: jest.fn(),
    geminiCliAuthCancel: jest.fn(),
    geminiCliAuthDisconnect: jest.fn(),
  },
}));

jest.mock('../../services/skillService', () => ({
  __esModule: true,
  default: {
    execute: jest.fn(),
  },
}));

jest.mock('../../services/notifications', () => ({
  __esModule: true,
  notificationService: {
    getInboxMonitorStatus: jest.fn(),
    startInboxMonitor: jest.fn(),
    stopInboxMonitor: jest.fn(),
  },
}));

// Heavy children — render stubs so we test the panel-level behaviour, not
// these helper components (each owns its own coverage path).
jest.mock('../../components/DefaultCliSelector', () => () => (
  <div data-testid="default-cli-selector" />
));
jest.mock('../../components/GithubPrimaryAccountSelector', () => () => (
  <div data-testid="github-primary-selector" />
));
jest.mock('../../components/GithubSshKeyCard', () => () => (
  <div data-testid="github-ssh-key-card" />
));
jest.mock('../../components/CpaExportFormatSelector', () => () => (
  <div data-testid="cpa-export-selector" />
));
jest.mock('../../components/WhatsAppChannelCard', () => () => (
  <div data-testid="whatsapp-channel-card" />
));

const integrationConfigService = require('../../services/integrationConfigService').default;
const skillService = require('../../services/skillService').default;
const { notificationService } = require('../../services/notifications');

const sampleRegistry = [
  {
    integration_name: 'gmail',
    display_name: 'Gmail',
    description: 'Gmail send + read',
    icon: 'FaEnvelope',
    auth_type: 'oauth',
    oauth_provider: 'google',
    credentials: [],
  },
  {
    integration_name: 'jira',
    display_name: 'Jira',
    description: 'Jira issues',
    icon: 'FaTasks',
    auth_type: 'manual',
    credentials: [
      { key: 'api_token', label: 'API Token', type: 'password', required: true },
      { key: 'email', label: 'Email', type: 'text', required: true },
    ],
  },
  {
    integration_name: 'codex',
    display_name: 'Codex CLI',
    description: 'Codex device auth',
    icon: 'FaTerminal',
    auth_type: 'device_auth',
    credentials: [],
  },
  {
    integration_name: 'whatsapp',
    display_name: 'WhatsApp',
    description: 'WhatsApp business channel',
    icon: 'FaWhatsapp',
    auth_type: 'manual',
    credentials: [{ key: 'phone_number', label: 'Phone Number', type: 'text' }],
  },
];

const sampleConfigs = [
  {
    id: 'cfg-jira',
    integration_name: 'jira',
    enabled: true,
    requires_approval: false,
  },
];

beforeEach(() => {
  jest.clearAllMocks();
  integrationConfigService.getRegistry.mockResolvedValue({ data: sampleRegistry });
  integrationConfigService.getAll.mockResolvedValue({ data: sampleConfigs });
  integrationConfigService.oauthStatus.mockResolvedValue({
    data: { connected: false, accounts: [] },
  });
  integrationConfigService.getCredentialStatus.mockResolvedValue({
    data: { stored_keys: [] },
  });
  integrationConfigService.codexAuthStatus.mockResolvedValue({
    data: { status: 'idle', connected: false },
  });
  integrationConfigService.geminiCliAuthStatus.mockResolvedValue({
    data: { status: 'idle', connected: false },
  });
  integrationConfigService.create.mockResolvedValue({
    data: { id: 'new-cfg', enabled: true },
  });
  integrationConfigService.update.mockResolvedValue({ data: {} });
  integrationConfigService.addCredential.mockResolvedValue({ data: {} });
  integrationConfigService.oauthAuthorize.mockResolvedValue({
    data: { auth_url: 'https://oauth.example.com/auth' },
  });
  integrationConfigService.oauthDisconnect.mockResolvedValue({});
  skillService.execute.mockResolvedValue({ data: { duration_ms: 42 } });
  notificationService.getInboxMonitorStatus.mockResolvedValue({ running: false });
  notificationService.startInboxMonitor.mockResolvedValue({});
  notificationService.stopInboxMonitor.mockResolvedValue({});

  // window.open is used for OAuth popups + device auth — return a fake
  // popup object so success-path tests don't hit "popup blocked".
  window.open = jest.fn(() => ({ closed: false, focus: jest.fn() }));
});

describe('IntegrationsPanel', () => {
  test('loads registry + configs on mount and renders integration cards', async () => {
    render(<IntegrationsPanel />);
    await waitFor(() =>
      expect(integrationConfigService.getRegistry).toHaveBeenCalled(),
    );
    expect(integrationConfigService.getAll).toHaveBeenCalled();
    // Each card shows its display_name.
    expect(await screen.findByText('Gmail')).toBeInTheDocument();
    expect(screen.getByText('Jira')).toBeInTheDocument();
    expect(screen.getByText('Codex CLI')).toBeInTheDocument();
    expect(screen.getByText('WhatsApp')).toBeInTheDocument();
  });

  test('fetches OAuth status for each oauth provider in the registry', async () => {
    render(<IntegrationsPanel />);
    await waitFor(() =>
      expect(integrationConfigService.oauthStatus).toHaveBeenCalledWith('google'),
    );
  });

  test('fetches credential status for enabled manual integrations', async () => {
    render(<IntegrationsPanel />);
    await waitFor(() =>
      expect(integrationConfigService.getCredentialStatus).toHaveBeenCalledWith(
        'cfg-jira',
      ),
    );
  });

  test('shows the empty state when registry is empty', async () => {
    integrationConfigService.getRegistry.mockResolvedValue({ data: [] });
    render(<IntegrationsPanel />);
    expect(
      await screen.findByText('No integrations available'),
    ).toBeInTheDocument();
  });

  test('shows error alert when initial load fails', async () => {
    integrationConfigService.getRegistry.mockRejectedValue(new Error('boom'));
    integrationConfigService.getAll.mockRejectedValue(new Error('boom'));
    const errSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
    render(<IntegrationsPanel />);
    expect(
      await screen.findByText('Failed to load integrations'),
    ).toBeInTheDocument();
    errSpy.mockRestore();
  });

  test('clicking a card expands it to reveal the action area', async () => {
    render(<IntegrationsPanel />);
    const jiraCard = await screen.findByText('Jira');
    // Click the card body (the Card.Body that wraps the title).
    fireEvent.click(jiraCard.closest('.card-body'));
    // Once expanded, the Save Credentials button is in the DOM (Jira is
    // already enabled via sampleConfigs).
    expect(await screen.findByText('Save Credentials')).toBeInTheDocument();
  });

  test('saving credentials calls addCredential for each filled field', async () => {
    render(<IntegrationsPanel />);
    const jiraCard = await screen.findByText('Jira');
    fireEvent.click(jiraCard.closest('.card-body'));
    // Type the API token. The input is identified by its placeholder.
    const tokenInput = await screen.findByPlaceholderText(/Enter api token/i);
    fireEvent.change(tokenInput, { target: { value: 'shhh' } });
    fireEvent.click(screen.getByRole('button', { name: /Save Credentials/i }));
    await waitFor(() => {
      expect(integrationConfigService.addCredential).toHaveBeenCalledWith(
        'cfg-jira',
        expect.objectContaining({
          credential_key: 'api_token',
          value: 'shhh',
        }),
      );
    });
  });

  test('save credentials shows error when no fields are filled', async () => {
    render(<IntegrationsPanel />);
    const jiraCard = await screen.findByText('Jira');
    fireEvent.click(jiraCard.closest('.card-body'));
    fireEvent.click(await screen.findByRole('button', { name: /Save Credentials/i }));
    expect(
      await screen.findByText(/Please fill in at least one credential field/),
    ).toBeInTheDocument();
  });

  test('test connection button runs skillService.execute', async () => {
    render(<IntegrationsPanel />);
    const jiraCard = await screen.findByText('Jira');
    fireEvent.click(jiraCard.closest('.card-body'));
    // The test button is the green outlined one with a play icon — it has
    // title="Test connection".
    const testBtn = await screen.findByTitle('Test connection');
    fireEvent.click(testBtn);
    await waitFor(() => {
      expect(skillService.execute).toHaveBeenCalledWith({
        integration_name: 'jira',
        payload: { test: true, message: 'ping' },
      });
    });
    expect(await screen.findByText(/Jira: connected/)).toBeInTheDocument();
  });

  test('test connection surfaces error from skillService.execute', async () => {
    skillService.execute.mockRejectedValue({
      response: { data: { detail: 'jira down' } },
    });
    render(<IntegrationsPanel />);
    const jiraCard = await screen.findByText('Jira');
    fireEvent.click(jiraCard.closest('.card-body'));
    fireEvent.click(await screen.findByTitle('Test connection'));
    expect(await screen.findByText(/Jira: jira down/)).toBeInTheDocument();
  });

  test('OAuth Connect button calls oauthAuthorize and opens popup', async () => {
    integrationConfigService.oauthStatus.mockResolvedValue({
      data: { connected: false, accounts: [] },
    });
    render(<IntegrationsPanel />);
    const gmailCard = await screen.findByText('Gmail');
    fireEvent.click(gmailCard.closest('.card-body'));
    // OAuth panels render a "Connect with Google" button.
    const connectBtn = await screen.findByRole('button', {
      name: /Connect with Google/i,
    });
    fireEvent.click(connectBtn);
    await waitFor(() => {
      expect(integrationConfigService.oauthAuthorize).toHaveBeenCalledWith('google');
    });
    await waitFor(() => {
      expect(window.open).toHaveBeenCalledWith(
        'https://oauth.example.com/auth',
        'oauth-google',
        expect.any(String),
      );
    });
  });

  test('OAuth disconnect calls oauthDisconnect with the email', async () => {
    integrationConfigService.oauthStatus.mockResolvedValue({
      data: {
        connected: true,
        accounts: [{ email: 'user@example.com' }],
      },
    });
    render(<IntegrationsPanel />);
    const gmailCard = await screen.findByText('Gmail');
    fireEvent.click(gmailCard.closest('.card-body'));
    // Connected account row exposes a "Disconnect" button.
    const disconnectBtn = await screen.findByRole('button', {
      name: /Disconnect/i,
    });
    fireEvent.click(disconnectBtn);
    await waitFor(() => {
      expect(integrationConfigService.oauthDisconnect).toHaveBeenCalledWith(
        'google',
        'user@example.com',
      );
    });
  });

  test('Codex Connect button starts device auth and opens verification URL', async () => {
    integrationConfigService.codexAuthStart.mockResolvedValue({
      data: {
        status: 'pending',
        connected: false,
        verification_url: 'https://chatgpt.com/auth/login',
        user_code: 'ABCD-1234',
      },
    });
    render(<IntegrationsPanel />);
    const codexCard = await screen.findByText('Codex CLI');
    fireEvent.click(codexCard.closest('.card-body'));
    // Codex device-auth panel renders a Connect button.
    const connectBtn = await screen.findByRole('button', {
      name: /Connect with ChatGPT|Connect/i,
    });
    fireEvent.click(connectBtn);
    await waitFor(() => {
      expect(integrationConfigService.codexAuthStart).toHaveBeenCalled();
    });
  });

  test('renders the active-integrations counter in the header', async () => {
    render(<IntegrationsPanel />);
    // jira is enabled (1) + 0 oauth accounts + 0 device-auth → 1 active.
    expect(await screen.findByText(/1 active/)).toBeInTheDocument();
  });

  test('renders helper child components', async () => {
    render(<IntegrationsPanel />);
    await screen.findByText('Gmail');
    expect(screen.getByTestId('default-cli-selector')).toBeInTheDocument();
    expect(screen.getByTestId('github-primary-selector')).toBeInTheDocument();
    expect(screen.getByTestId('cpa-export-selector')).toBeInTheDocument();
  });
});
