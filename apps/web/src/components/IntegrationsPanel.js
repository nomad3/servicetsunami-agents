import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Form,
  Row,
  Spinner,
} from 'react-bootstrap';
import {
  FaBook,
  FaCalendar,
  FaCheckCircle,
  FaCog,
  FaEnvelope,
  FaGithub,
  FaGoogle,
  FaKey,
  FaLink,
  FaLinkedin,
  FaMicrosoft,
  FaPlay,
  FaPlug,
  FaPlus,
  FaProjectDiagram,
  FaSave,
  FaSignOutAlt,
  FaSlack,
  FaTasks,
  FaTerminal,
  FaTimesCircle,
  FaToggleOff,
  FaToggleOn,
  FaUserCircle,
  FaWhatsapp
} from 'react-icons/fa';
import integrationConfigService from '../services/integrationConfigService';
import skillService from '../services/skillService';
import { notificationService } from '../services/notifications';

import DefaultCliSelector from './DefaultCliSelector';
import WhatsAppChannelCard from './WhatsAppChannelCard';

// Map icon name strings from the registry to actual React icon components
const ICON_MAP = {
  FaSlack: FaSlack,
  FaEnvelope: FaEnvelope,
  FaGithub: FaGithub,
  FaWhatsapp: FaWhatsapp,
  FaBook: FaBook,
  FaTasks: FaTasks,
  FaCalendar: FaCalendar,
  FaProjectDiagram: FaProjectDiagram,
  FaLinkedin: FaLinkedin,
  FaMicrosoft: FaMicrosoft,
  FaTerminal: FaTerminal,
};

// Color accents per skill for visual distinction
const SKILL_COLORS = {
  slack: '#4A154B',
  gmail: '#EA4335',
  github: '#333333',
  whatsapp: '#25D366',
  notion: '#000000',
  jira: '#0052CC',
  google_calendar: '#4285F4',
  outlook: '#0078D4',
  linkedin: '#0A66C2',
  claude_code: '#D97706',
  codex: '#111827',
  gemini_cli: '#1A73E8',
};

// Pinned order for the integration card grid. Anything not listed gets pushed
// to the end and sorted alphabetically.
//
// Layout intent: WhatsApp first (primary tenant channel for Luna), then the
// CLI cluster grouped together (Gemini, Claude Code, GitHub Copilot CLI,
// Codex CLI), then productivity / OAuth-suite integrations.
const INTEGRATION_ORDER = [
  'whatsapp',
  'gemini_cli',
  'claude_code',
  'github',
  'codex',
  'gmail',
  'google_calendar',
  'google_drive',
  'outlook',
  'linkedin',
  'slack',
  'notion',
  'jira',
];

const sortIntegrations = (a, b) => {
  const ai = INTEGRATION_ORDER.indexOf(a.integration_name);
  const bi = INTEGRATION_ORDER.indexOf(b.integration_name);
  if (ai === -1 && bi === -1) return a.display_name.localeCompare(b.display_name);
  if (ai === -1) return 1;
  if (bi === -1) return -1;
  return ai - bi;
};

// Provider brand colors and icons for OAuth buttons
const OAUTH_BRAND = {
  google: { label: 'Google', icon: FaGoogle, color: '#4285F4', bg: '#fff', textColor: '#333' },
  microsoft: { label: 'Microsoft', icon: FaMicrosoft, color: '#0078D4', bg: '#0078D4', textColor: '#fff' },
  github: { label: 'GitHub', icon: FaGithub, color: '#24292e', bg: '#24292e', textColor: '#fff' },
  linkedin: { label: 'LinkedIn', icon: FaLinkedin, color: '#0A66C2', bg: '#0A66C2', textColor: '#fff' },
};

const IntegrationsPanel = () => {
  const [registry, setRegistry] = useState([]);
  const [configs, setConfigs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedSkill, setExpandedSkill] = useState(null);
  const [credentialForms, setCredentialForms] = useState({});
  const [saving, setSaving] = useState(null);
  const [testingSkill, setTestingSkill] = useState(null);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);
  const [oauthStatuses, setOauthStatuses] = useState({});
  const [credentialStatuses, setCredentialStatuses] = useState({});
  const [connectingProvider, setConnectingProvider] = useState(null);
  const [codexAuthState, setCodexAuthState] = useState({ status: 'idle', connected: false });
  const [claudeAuthState, setClaudeAuthState] = useState({ status: 'idle', connected: false });
  const [geminiCliAuthState, setGeminiCliAuthState] = useState({ status: 'idle', connected: false });
  const [geminiCliCode, setGeminiCliCode] = useState('');
  const [monitorRunning, setMonitorRunning] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const [registryRes, configsRes] = await Promise.all([
        integrationConfigService.getRegistry(),
        integrationConfigService.getAll(),
      ]);
      setRegistry(registryRes.data || []);
      setConfigs(configsRes.data || []);

      // Fetch OAuth statuses for all OAuth providers
      const oauthProviders = [...new Set(
        (registryRes.data || [])
          .filter(s => s.auth_type === 'oauth' && s.oauth_provider)
          .map(s => s.oauth_provider)
      )];

      const statuses = {};
      await Promise.all(
        oauthProviders.map(async (provider) => {
          try {
            const res = await integrationConfigService.oauthStatus(provider);
            statuses[provider] = {
              connected: res.data?.connected ?? false,
              accounts: res.data?.accounts ?? [],
            };
          } catch {
            statuses[provider] = { connected: false, accounts: [] };
          }
        })
      );
      setOauthStatuses(statuses);

      // Fetch credential status for manual (non-OAuth) integrations
      const manualConfigs = (configsRes.data || []).filter(cfg => {
        const reg = (registryRes.data || []).find(r => r.integration_name === cfg.integration_name);
        return reg && reg.auth_type !== 'oauth' && cfg.enabled;
      });
      const credStatuses = {};
      await Promise.all(
        manualConfigs.map(async (cfg) => {
          try {
            const res = await integrationConfigService.getCredentialStatus(cfg.id);
            credStatuses[cfg.integration_name] = res.data?.stored_keys || [];
          } catch {
            credStatuses[cfg.integration_name] = [];
          }
        })
      );
      setCredentialStatuses(credStatuses);

      try {
        const codexRes = await integrationConfigService.codexAuthStatus();
        setCodexAuthState(codexRes.data || { status: 'idle', connected: false });
      } catch {
        setCodexAuthState({ status: 'idle', connected: false });
      }

      try {
        const geminiRes = await integrationConfigService.geminiCliAuthStatus();
        setGeminiCliAuthState(geminiRes.data || { status: 'idle', connected: false });
      } catch {
        setGeminiCliAuthState({ status: 'idle', connected: false });
      }

      // Check inbox monitor status if Google is connected
      if (statuses.google?.connected) {
        try {
          const monitorStatus = await notificationService.getInboxMonitorStatus();
          setMonitorRunning(monitorStatus.running || false);
        } catch {
          // Ignore
        }
      }
    } catch (err) {
      console.error('Failed to load skill data:', err);
      setError('Failed to load integrations');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useEffect(() => {
    if (!['starting', 'pending'].includes(codexAuthState?.status)) return undefined;

    const interval = setInterval(async () => {
      try {
        const res = await integrationConfigService.codexAuthStatus();
        const nextState = res.data || { status: 'idle', connected: false };
        setCodexAuthState(nextState);

        if (nextState.status === 'connected') {
          setSuccess('Connected Codex via ChatGPT');
          setTimeout(() => setSuccess(null), 4000);
          fetchData();
        } else if (nextState.status === 'failed') {
          setError(nextState.error || 'Codex login failed');
          setTimeout(() => setError(null), 6000);
        }
      } catch {
        setError('Failed to check Codex login status');
        setTimeout(() => setError(null), 6000);
      }
    }, 2500);

    return () => clearInterval(interval);
  }, [codexAuthState?.status, fetchData]);

  // Poll Claude auth status while login is pending
  useEffect(() => {
    if (!['starting', 'pending'].includes(claudeAuthState?.status)) return undefined;

    const interval = setInterval(async () => {
      try {
        const res = await integrationConfigService.claudeAuthStatus();
        const nextState = res.data || { status: 'idle', connected: false };
        setClaudeAuthState(nextState);

        if (nextState.status === 'connected') {
          setSuccess('Connected Claude Code via Anthropic');
          setTimeout(() => setSuccess(null), 4000);
          fetchData();
        } else if (nextState.status === 'failed') {
          setError(nextState.error || 'Claude login failed');
          setTimeout(() => setError(null), 6000);
        }
      } catch {
        setError('Failed to check Claude login status');
        setTimeout(() => setError(null), 6000);
      }
    }, 2500);

    return () => clearInterval(interval);
  }, [claudeAuthState?.status, fetchData]);

  // Poll Gemini CLI auth status while login is pending
  useEffect(() => {
    if (!['starting', 'pending'].includes(geminiCliAuthState?.status)) return undefined;

    const interval = setInterval(async () => {
      try {
        const res = await integrationConfigService.geminiCliAuthStatus();
        const nextState = res.data || { status: 'idle', connected: false };
        setGeminiCliAuthState(nextState);

        if (nextState.status === 'connected') {
          setSuccess('Connected Gemini CLI');
          setTimeout(() => setSuccess(null), 4000);
          setGeminiCliCode('');
          fetchData();
        } else if (nextState.status === 'failed') {
          setError(nextState.error || 'Gemini login failed');
          setTimeout(() => setError(null), 6000);
        }
      } catch {
        setError('Failed to check Gemini login status');
        setTimeout(() => setError(null), 6000);
      }
    }, 2500);

    return () => clearInterval(interval);
  }, [geminiCliAuthState?.status, fetchData]);

  // Listen for OAuth popup messages
  useEffect(() => {
    const handleMessage = (event) => {
      if (event.data?.type === 'oauth-success') {
        const email = event.data.email || '';
        setSuccess(`Connected ${email || event.data.provider || 'account'}`);
        setTimeout(() => setSuccess(null), 4000);
        fetchData();
      } else if (event.data?.type === 'oauth-error') {
        setError(`Failed to connect ${event.data.provider || 'provider'}`);
        setTimeout(() => setError(null), 6000);
      }
      setConnectingProvider(null);
    };
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [fetchData]);

  const getConfigForSkill = (skillName) =>
    configs.find((c) => c.integration_name === skillName);

  const getIcon = (iconName) => {
    const IconComponent = ICON_MAP[iconName];
    return IconComponent ? <IconComponent size={24} /> : <FaPlug size={24} />;
  };

  const handleOAuthConnect = async (provider) => {
    try {
      setConnectingProvider(provider);
      const res = await integrationConfigService.oauthAuthorize(provider);
      const authUrl = res.data?.auth_url;
      if (!authUrl) {
        setError('Could not get authorization URL');
        setConnectingProvider(null);
        return;
      }
      const popup = window.open(authUrl, `oauth-${provider}`, 'width=600,height=700,scrollbars=yes');
      if (!popup) {
        setError('Please allow popups for this site to connect your account');
        setConnectingProvider(null);
      }
    } catch (err) {
      const detail = err.response?.data?.detail || 'OAuth not available';
      setError(detail);
      setTimeout(() => setError(null), 6000);
      setConnectingProvider(null);
    }
  };

  const handleOAuthDisconnect = async (provider, accountEmail) => {
    try {
      setSaving(`${provider}-${accountEmail || 'all'}`);
      await integrationConfigService.oauthDisconnect(provider, accountEmail);
      setSuccess(`Disconnected ${accountEmail || provider}`);
      setTimeout(() => setSuccess(null), 3000);
      await fetchData();
    } catch (err) {
      setError(`Failed to disconnect ${accountEmail || provider}`);
      setTimeout(() => setError(null), 5000);
    } finally {
      setSaving(null);
    }
  };

  const handleCodexConnect = async () => {
    try {
      setConnectingProvider('codex');
      const res = await integrationConfigService.codexAuthStart();
      const nextState = res.data || { status: 'idle', connected: false };
      setCodexAuthState(nextState);

      if (nextState.verification_url) {
        window.open(nextState.verification_url, 'codex-device-auth', 'width=700,height=820,scrollbars=yes');
      }
    } catch (err) {
      const detail = err.response?.data?.detail || 'Codex login not available';
      setError(detail);
      setTimeout(() => setError(null), 6000);
      setConnectingProvider(null);
    } finally {
      setConnectingProvider(null);
    }
  };

  const handleCodexCancel = async () => {
    try {
      setSaving('codex-cancel');
      const res = await integrationConfigService.codexAuthCancel();
      setCodexAuthState(res.data || { status: 'cancelled', connected: false });
    } catch (err) {
      const detail = err.response?.data?.detail || 'Failed to cancel Codex login';
      setError(detail);
      setTimeout(() => setError(null), 5000);
    } finally {
      setSaving(null);
    }
  };

  // ── Claude Code OAuth login (same pattern as Codex device auth) ──
  const handleClaudeConnect = async () => {
    try {
      setConnectingProvider('claude_code');
      const res = await integrationConfigService.claudeAuthStart();
      const nextState = res.data || { status: 'idle', connected: false };
      setClaudeAuthState(nextState);

      if (nextState.verification_url) {
        window.open(nextState.verification_url, 'claude-oauth', 'width=700,height=820,scrollbars=yes');
      }
    } catch (err) {
      const detail = err.response?.data?.detail || 'Claude login not available';
      setError(detail);
      setTimeout(() => setError(null), 6000);
    } finally {
      setConnectingProvider(null);
    }
  };

  const handleClaudeCancel = async () => {
    try {
      setSaving('claude-cancel');
      const res = await integrationConfigService.claudeAuthCancel();
      setClaudeAuthState(res.data || { status: 'cancelled', connected: false });
    } catch (err) {
      const detail = err.response?.data?.detail || 'Failed to cancel Claude login';
      setError(detail);
      setTimeout(() => setError(null), 5000);
    } finally {
      setSaving(null);
    }
  };

  // ── Gemini CLI OAuth login (paste-back code flow) ──
  const handleGeminiCliConnect = async () => {
    try {
      setConnectingProvider('gemini_cli');
      const res = await integrationConfigService.geminiCliAuthStart();
      const nextState = res.data || { status: 'idle', connected: false };
      setGeminiCliAuthState(nextState);

      if (nextState.verification_url) {
        window.open(nextState.verification_url, 'gemini-cli-auth', 'width=700,height=820,scrollbars=yes');
      }
    } catch (err) {
      const detail = err.response?.data?.detail || 'Gemini CLI login not available';
      setError(detail);
      setTimeout(() => setError(null), 6000);
    } finally {
      setConnectingProvider(null);
    }
  };

  const handleGeminiCliCancel = async () => {
    try {
      setSaving('gemini-cancel');
      const res = await integrationConfigService.geminiCliAuthCancel();
      setGeminiCliAuthState(res.data || { status: 'cancelled', connected: false });
      setGeminiCliCode('');
    } catch (err) {
      const detail = err.response?.data?.detail || 'Failed to cancel Gemini login';
      setError(detail);
      setTimeout(() => setError(null), 5000);
    } finally {
      setSaving(null);
    }
  };

  const handleGeminiCliSubmitCode = async () => {
    if (!geminiCliCode.trim()) return;
    try {
      setSaving('gemini-submit');
      const res = await integrationConfigService.geminiCliAuthSubmitCode(geminiCliCode.trim());
      setGeminiCliAuthState(res.data || { status: 'pending', connected: false });
    } catch (err) {
      const detail = err.response?.data?.detail || 'Failed to submit Gemini code';
      setError(detail);
      setTimeout(() => setError(null), 5000);
    } finally {
      setSaving(null);
    }
  };

  const handleGeminiCliDisconnect = async () => {
    try {
      setSaving('gemini-disconnect');
      await integrationConfigService.geminiCliAuthDisconnect();
      setGeminiCliAuthState({ status: 'idle', connected: false });
      setCredentialStatuses((prev) => ({ ...prev, gemini_cli: [] }));
      setSuccess('Disconnected Gemini CLI');
      setTimeout(() => setSuccess(null), 3000);
      await fetchData();
    } catch (err) {
      setError('Failed to disconnect Gemini CLI');
      setTimeout(() => setError(null), 5000);
    } finally {
      setSaving(null);
    }
  };

  const handleCodexDisconnect = async () => {
    const config = getConfigForSkill('codex');
    if (!config) return;

    try {
      setSaving('codex-disconnect');
      const storedKeys = credentialStatuses.codex || [];
      const keysToRevoke = storedKeys.filter((key) => ['auth_json', 'session_token'].includes(key));
      await Promise.all(keysToRevoke.map((key) => integrationConfigService.revokeCredential(config.id, key)));
      setCodexAuthState({ status: 'idle', connected: false });
      setSuccess('Disconnected Codex');
      setTimeout(() => setSuccess(null), 3000);
      await fetchData();
    } catch (err) {
      setError('Failed to disconnect Codex');
      setTimeout(() => setError(null), 5000);
    } finally {
      setSaving(null);
    }
  };

  const handleCopyCodexCode = async () => {
    if (!codexAuthState?.user_code) return;
    try {
      await navigator.clipboard.writeText(codexAuthState.user_code);
      setSuccess('Copied Codex one-time code');
      setTimeout(() => setSuccess(null), 2500);
    } catch {
      setError('Failed to copy Codex code');
      setTimeout(() => setError(null), 4000);
    }
  };

  const handleToggleSkill = async (skill) => {
    const existing = getConfigForSkill(skill.integration_name);
    try {
      setSaving(skill.integration_name);
      if (existing) {
        await integrationConfigService.update(existing.id, {
          enabled: !existing.enabled,
        });
      } else {
        await integrationConfigService.create({
          integration_name: skill.integration_name,
          enabled: true,
        });
      }
      await fetchData();
      setSuccess(`${skill.display_name} ${existing?.enabled ? 'disabled' : 'enabled'}`);
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(`Failed to toggle ${skill.display_name}`);
      setTimeout(() => setError(null), 5000);
    } finally {
      setSaving(null);
    }
  };

  const handleToggleApproval = async (skill) => {
    const existing = getConfigForSkill(skill.integration_name);
    if (!existing) return;
    try {
      setSaving(skill.integration_name);
      await integrationConfigService.update(existing.id, {
        requires_approval: !existing.requires_approval,
      });
      await fetchData();
    } catch (err) {
      setError(`Failed to update approval setting for ${skill.display_name}`);
      setTimeout(() => setError(null), 5000);
    } finally {
      setSaving(null);
    }
  };

  const handleCredentialChange = (skillName, key, value) => {
    setCredentialForms((prev) => ({
      ...prev,
      [skillName]: {
        ...(prev[skillName] || {}),
        [key]: value,
      },
    }));
  };

  const handleSaveCredentials = async (skill) => {
    const existing = getConfigForSkill(skill.integration_name);
    if (!existing) return;

    const formValues = credentialForms[skill.integration_name] || {};
    const credentialsToSave = skill.credentials.filter(
      (cred) => formValues[cred.key]?.trim()
    );

    if (credentialsToSave.length === 0) {
      setError('Please fill in at least one credential field');
      setTimeout(() => setError(null), 5000);
      return;
    }

    try {
      setSaving(skill.integration_name);
      for (const cred of credentialsToSave) {
        await integrationConfigService.addCredential(existing.id, {
          credential_key: cred.key,
          value: formValues[cred.key],
          credential_type: cred.type === 'password' ? 'api_key' : 'text',
        });
      }
      setCredentialForms((prev) => ({
        ...prev,
        [skill.integration_name]: {},
      }));
      // Refresh credential status to show "Saved" indicators
      try {
        const statusRes = await integrationConfigService.getCredentialStatus(existing.id);
        setCredentialStatuses((prev) => ({
          ...prev,
          [skill.integration_name]: statusRes.data?.stored_keys || [],
        }));
      } catch { /* ignore */ }
      setSuccess(`Credentials saved for ${skill.display_name}`);
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(`Failed to save credentials for ${skill.display_name}`);
      setTimeout(() => setError(null), 5000);
    } finally {
      setSaving(null);
    }
  };

  const handleTestSkill = async (skill) => {
    try {
      setTestingSkill(skill.integration_name);
      setError(null);
      const res = await skillService.execute({
        integration_name: skill.integration_name,
        payload: { test: true, message: 'ping' },
      });
      setSuccess(`${skill.display_name}: connected (${res.data?.duration_ms || 0}ms)`);
      setTimeout(() => setSuccess(null), 5000);
    } catch (err) {
      const detail = err.response?.data?.detail || err.message || 'Test failed';
      setError(`${skill.display_name}: ${detail}`);
      setTimeout(() => setError(null), 8000);
    } finally {
      setTestingSkill(null);
    }
  };

  const handleCardClick = (skillName) => {
    setExpandedSkill(expandedSkill === skillName ? null : skillName);
  };

  const handleToggleMonitor = async () => {
    try {
      if (monitorRunning) {
        await notificationService.stopInboxMonitor();
        setMonitorRunning(false);
      } else {
        await notificationService.startInboxMonitor(15);
        setMonitorRunning(true);
      }
    } catch (err) {
      console.error('Failed to toggle monitor:', err);
    }
  };

  // ---------------------------------------------------------------------------
  // OAuth skill card (expanded section) — multi-account
  // ---------------------------------------------------------------------------
  const renderOAuthExpanded = (skill) => {
    const provider = skill.oauth_provider;
    const brand = OAUTH_BRAND[provider] || { label: provider, icon: FaLink, bg: '#555', textColor: '#fff' };
    const BrandIcon = brand.icon;
    const providerStatus = oauthStatuses[provider] || { connected: false, accounts: [] };
    const connectedAccounts = providerStatus.accounts || [];
    const isConnecting = connectingProvider === provider;

    return (
      <div className="py-2">
        {/* Connected accounts list */}
        {connectedAccounts.length > 0 && (
          <div className="mb-3">
            {connectedAccounts.map((account, idx) => {
              const savingKey = `${provider}-${account.email || 'all'}`;
              const isSaving = saving === savingKey;

              return (
                <div
                  key={account.email || idx}
                  className="d-flex align-items-center justify-content-between py-2"
                  style={{
                    borderBottom: idx < connectedAccounts.length - 1
                      ? '1px solid var(--color-border)'
                      : 'none',
                  }}
                >
                  <div className="d-flex align-items-center gap-2">
                    <FaUserCircle
                      size={20}
                      style={{ color: brand.color, opacity: 0.8, flexShrink: 0 }}
                    />
                    <div>
                      <div
                        style={{
                          fontSize: '0.88rem',
                          fontWeight: 500,
                          color: 'var(--color-foreground)',
                        }}
                      >
                        {account.email || 'Connected account'}
                      </div>
                      <div
                        className="d-flex align-items-center gap-1"
                        style={{ fontSize: '0.72rem', color: '#2d9d78' }}
                      >
                        <FaCheckCircle size={8} />
                        Connected
                      </div>
                    </div>
                  </div>
                  <Button
                    variant="outline-danger"
                    size="sm"
                    onClick={() => handleOAuthDisconnect(provider, account.email)}
                    disabled={isSaving}
                    style={{ fontSize: '0.78rem', padding: '4px 12px' }}
                  >
                    {isSaving ? (
                      <Spinner
                        animation="border"
                        size="sm"
                        style={{ width: 12, height: 12, borderWidth: 1.5 }}
                        className="me-1"
                      />
                    ) : (
                      <FaSignOutAlt className="me-1" size={10} />
                    )}
                    Disconnect
                  </Button>
                </div>
              );
            })}
          </div>
        )}

        {/* Add / Connect button */}
        <div className="text-center">
          <Button
            size="sm"
            onClick={() => handleOAuthConnect(provider)}
            disabled={isConnecting}
            style={{
              background: brand.bg,
              color: brand.textColor,
              border: provider === 'google' ? '1px solid #dadce0' : 'none',
              fontWeight: 500,
              fontSize: '0.85rem',
              padding: '8px 20px',
              borderRadius: 6,
            }}
          >
            {isConnecting ? (
              <Spinner
                animation="border"
                size="sm"
                style={{ width: 14, height: 14, borderWidth: 1.5 }}
                className="me-2"
              />
            ) : connectedAccounts.length > 0 ? (
              <FaPlus className="me-2" size={12} />
            ) : (
              <BrandIcon className="me-2" size={16} />
            )}
            {connectedAccounts.length > 0
              ? `Add another ${brand.label} account`
              : `Connect with ${brand.label}`
            }
          </Button>
        </div>

        {/* Show monitor toggle when Google is connected */}
        {skill.oauth_provider === 'google' && oauthStatuses.google?.connected && (
          <div className="d-flex align-items-center justify-content-between mt-3 pt-3"
            style={{ borderTop: '1px solid var(--border-color)' }}>
            <div>
              <strong style={{ fontSize: '0.85rem' }}>Proactive Monitoring</strong>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                Luna monitors your inbox & calendar every 15 min
              </div>
            </div>
            <Form.Check
              type="switch"
              checked={monitorRunning}
              onChange={handleToggleMonitor}
            />
          </div>
        )}
      </div>
    );
  };

  const renderCodexExpanded = (skill) => {
    const config = getConfigForSkill(skill.integration_name);
    const storedKeys = credentialStatuses[skill.integration_name] || [];
    const hasStoredAuth = storedKeys.includes('auth_json') || storedKeys.includes('session_token');
    const isConnected = codexAuthState.connected || hasStoredAuth;
    const isPending = ['starting', 'pending'].includes(codexAuthState.status);
    const isConnecting = connectingProvider === 'codex';
    const verificationUrl = codexAuthState.verification_url || 'https://auth.openai.com/codex/device';

    return (
      <div className="py-2">
        {isConnected && (
          <div
            className="d-flex align-items-center justify-content-between mb-3 p-3"
            style={{
              border: '1px solid rgba(45,157,120,0.25)',
              borderRadius: 10,
              background: 'rgba(45,157,120,0.08)',
            }}
          >
            <div>
              <div style={{ fontSize: '0.88rem', fontWeight: 600, color: 'var(--color-foreground)' }}>
                Connected with ChatGPT
              </div>
              <div style={{ fontSize: '0.74rem', color: 'var(--color-muted)' }}>
                Codex auth.json is stored in the tenant vault
              </div>
            </div>
            {!!config && (
              <Button
                variant="outline-danger"
                size="sm"
                onClick={handleCodexDisconnect}
                disabled={saving === 'codex-disconnect'}
              >
                Disconnect
              </Button>
            )}
          </div>
        )}

        {isPending && (
          <Alert variant="info" className="mb-3" style={{ fontSize: '0.82rem' }}>
            <div className="fw-semibold mb-2">Finish Codex sign-in</div>
            <div className="mb-2">
              Open{' '}
              <a href={verificationUrl} target="_blank" rel="noreferrer">
                {verificationUrl}
              </a>
              {' '}and approve access with your ChatGPT account.
            </div>
            {codexAuthState.user_code && (
              <div
                className="d-flex align-items-center justify-content-between px-3 py-2"
                style={{
                  borderRadius: 8,
                  background: 'rgba(17,24,39,0.08)',
                  border: '1px dashed rgba(17,24,39,0.2)',
                }}
              >
                <div>
                  <div style={{ fontSize: '0.68rem', color: 'var(--color-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    One-time code
                  </div>
                  <div style={{ fontSize: '1rem', fontWeight: 700, color: 'var(--color-foreground)', letterSpacing: '0.08em' }}>
                    {codexAuthState.user_code}
                  </div>
                </div>
                <Button variant="outline-secondary" size="sm" onClick={handleCopyCodexCode}>
                  Copy code
                </Button>
              </div>
            )}
          </Alert>
        )}

        {codexAuthState.status === 'failed' && codexAuthState.error && (
          <Alert variant="danger" className="mb-3" style={{ fontSize: '0.8rem' }}>
            {codexAuthState.error}
          </Alert>
        )}

        <div className="d-flex gap-2">
          <Button
            size="sm"
            onClick={handleCodexConnect}
            disabled={isConnecting}
            style={{
              background: '#111827',
              color: '#fff',
              border: 'none',
              fontWeight: 500,
              fontSize: '0.85rem',
              padding: '8px 16px',
              borderRadius: 6,
            }}
          >
            {isConnecting ? (
              <Spinner
                animation="border"
                size="sm"
                style={{ width: 14, height: 14, borderWidth: 1.5 }}
                className="me-2"
              />
            ) : (
              <FaTerminal className="me-2" size={14} />
            )}
            {isConnected ? 'Reconnect Codex' : 'Connect with ChatGPT'}
          </Button>

          {isPending && (
            <>
              <Button
                variant="outline-secondary"
                size="sm"
                onClick={() => window.open(verificationUrl, 'codex-device-auth', 'width=700,height=820,scrollbars=yes')}
              >
                Open sign-in page
              </Button>
              <Button
                variant="outline-danger"
                size="sm"
                onClick={handleCodexCancel}
                disabled={saving === 'codex-cancel'}
              >
                Cancel
              </Button>
            </>
          )}
        </div>
      </div>
    );
  };

  const renderGeminiCliExpanded = (skill) => {
    const config = getConfigForSkill(skill.integration_name);
    const storedKeys = credentialStatuses[skill.integration_name] || [];
    const hasStoredAuth = storedKeys.includes('oauth_creds') || storedKeys.includes('oauth_token');
    const isConnected = geminiCliAuthState.connected || hasStoredAuth;
    const isPending = ['starting', 'pending'].includes(geminiCliAuthState.status);
    const isConnecting = connectingProvider === 'gemini_cli';
    const verificationUrl = geminiCliAuthState.verification_url;

    return (
      <div className="py-2">
        {isConnected && !isPending && (
          <div
            className="d-flex align-items-center justify-content-between mb-3 p-3"
            style={{
              border: '1px solid rgba(45,157,120,0.25)',
              borderRadius: 10,
              background: 'rgba(45,157,120,0.08)',
            }}
          >
            <div>
              <div style={{ fontSize: '0.88rem', fontWeight: 600, color: 'var(--color-foreground)' }}>
                Connected with Google
              </div>
              <div style={{ fontSize: '0.74rem', color: 'var(--color-muted)' }}>
                Gemini CLI oauth_creds.json is stored in the tenant vault
              </div>
            </div>
            {!!config && (
              <Button
                variant="outline-danger"
                size="sm"
                onClick={handleGeminiCliDisconnect}
                disabled={saving === 'gemini-disconnect'}
              >
                Disconnect
              </Button>
            )}
          </div>
        )}

        {isPending && (
          <Alert variant="info" className="mb-3" style={{ fontSize: '0.82rem' }}>
            <div className="fw-semibold mb-2">Finish Gemini CLI sign-in</div>
            <div className="mb-2">
              {verificationUrl ? (
                <>
                  Open{' '}
                  <a href={verificationUrl} target="_blank" rel="noreferrer">
                    this Google authorization URL
                  </a>
                  , approve access with your Gemini account, then paste the code below.
                </>
              ) : (
                'Waiting for the Gemini CLI to print a verification URL...'
              )}
            </div>
            {verificationUrl && (
              <Form.Group className="mb-2">
                <Form.Label style={{ fontSize: '0.74rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  Authorization code
                </Form.Label>
                <div className="d-flex gap-2">
                  <Form.Control
                    size="sm"
                    type="text"
                    placeholder="Paste the code from Google"
                    value={geminiCliCode}
                    onChange={(e) => setGeminiCliCode(e.target.value)}
                  />
                  <Button
                    size="sm"
                    onClick={handleGeminiCliSubmitCode}
                    disabled={saving === 'gemini-submit' || !geminiCliCode.trim()}
                  >
                    Submit
                  </Button>
                </div>
              </Form.Group>
            )}
          </Alert>
        )}

        {geminiCliAuthState.status === 'failed' && geminiCliAuthState.error && (
          <Alert variant="danger" className="mb-3" style={{ fontSize: '0.8rem' }}>
            {geminiCliAuthState.error}
          </Alert>
        )}

        <div className="d-flex gap-2">
          <Button
            size="sm"
            onClick={handleGeminiCliConnect}
            disabled={isConnecting || isPending}
            style={{
              background: '#1A73E8',
              color: '#fff',
              border: 'none',
              fontWeight: 500,
              fontSize: '0.85rem',
              padding: '8px 16px',
              borderRadius: 6,
            }}
          >
            {isConnecting ? (
              <Spinner
                animation="border"
                size="sm"
                style={{ width: 14, height: 14, borderWidth: 1.5 }}
                className="me-2"
              />
            ) : (
              <FaGoogle className="me-2" size={14} />
            )}
            {isConnected ? 'Reconnect Gemini' : 'Connect with Google'}
          </Button>

          {isPending && (
            <>
              {verificationUrl && (
                <Button
                  variant="outline-secondary"
                  size="sm"
                  onClick={() => window.open(verificationUrl, 'gemini-cli-auth', 'width=700,height=820,scrollbars=yes')}
                >
                  Open sign-in page
                </Button>
              )}
              <Button
                variant="outline-danger"
                size="sm"
                onClick={handleGeminiCliCancel}
                disabled={saving === 'gemini-cancel'}
              >
                Cancel
              </Button>
            </>
          )}
        </div>
      </div>
    );
  };

  // ---------------------------------------------------------------------------
  // Skill card renderer
  // ---------------------------------------------------------------------------
  const renderSkillCard = (skill) => {
    const config = getConfigForSkill(skill.integration_name);
    const isExpanded = expandedSkill === skill.integration_name;
    const isOAuth = skill.auth_type === 'oauth';
    const isDeviceAuth = skill.auth_type === 'device_auth';
    const isBrowserAuth = skill.auth_type === 'browser_auth';
    const providerStatus = isOAuth
      ? (oauthStatuses[skill.oauth_provider] || { connected: false, accounts: [] })
      : { connected: false, accounts: [] };
    const storedKeys = credentialStatuses[skill.integration_name] || [];
    const hasStoredCredentials = storedKeys.includes('auth_json') || storedKeys.includes('session_token') || storedKeys.includes('oauth_creds') || storedKeys.length > 0;
    const isConfigured = isOAuth
      ? providerStatus.connected
      : isDeviceAuth
        ? (skill.integration_name === 'codex'
            ? (codexAuthState.connected || hasStoredCredentials)
            : skill.integration_name === 'gemini_cli'
              ? (geminiCliAuthState.connected || hasStoredCredentials)
              : hasStoredCredentials)
        : isBrowserAuth
        ? (skill.integration_name === 'claude_code' ? (claudeAuthState.connected || hasStoredCredentials) : hasStoredCredentials)
        : !!config;
    const isEnabled = isOAuth
      ? providerStatus.connected
      : (isDeviceAuth || isBrowserAuth)
        ? (config?.enabled ?? isConfigured)
        : (config?.enabled ?? false);
    const accountCount = isOAuth ? providerStatus.accounts.length : 0;
    const accentColor = SKILL_COLORS[skill.integration_name] || '#6C757D';
    const formValues = credentialForms[skill.integration_name] || {};

    return (
      <Col md={6} lg={4} key={skill.integration_name} className="mb-3">
        <Card
          style={{
            border: `1px solid ${isExpanded ? accentColor : 'var(--color-border)'}`,
            borderRadius: 12,
            background: 'var(--surface-elevated)',
            cursor: 'pointer',
            transition: 'all 0.2s ease',
            boxShadow: isExpanded
              ? `0 4px 20px rgba(100, 130, 170, 0.15)`
              : '0 2px 10px rgba(100, 130, 170, 0.08)',
          }}
        >
          {/* Card header */}
          <Card.Body
            onClick={() => handleCardClick(skill.integration_name)}
            style={{ padding: '1rem 1.25rem' }}
          >
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '44px minmax(0, 1fr)',
                gap: '0.75rem 1rem',
                alignItems: 'start',
              }}
            >
              <div
                style={{
                  width: 44,
                  height: 44,
                  borderRadius: 10,
                  background: `${accentColor}22`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: accentColor,
                  flexShrink: 0,
                  gridRow: '1 / span 2',
                }}
              >
                {getIcon(skill.icon)}
              </div>
              <div style={{ minWidth: 0 }}>
                <div
                  className="fw-semibold"
                  style={{
                    color: 'var(--color-foreground)',
                    fontSize: '0.95rem',
                    lineHeight: 1.2,
                    minWidth: 0,
                    marginBottom: '0.25rem',
                  }}
                >
                  {skill.display_name}
                </div>
                <div
                  className="text-muted"
                  style={{
                    fontSize: '0.78rem',
                    lineHeight: 1.35,
                    minWidth: 0,
                    minHeight: '3.15rem',
                    display: '-webkit-box',
                    WebkitLineClamp: 3,
                    WebkitBoxOrient: 'vertical',
                    overflow: 'hidden',
                    overflowWrap: 'anywhere',
                  }}
                >
                  {skill.description}
                </div>
                {isConfigured && (
                  <div style={{ marginTop: '0.6rem' }}>
                    <Badge
                      bg={isEnabled ? 'success' : 'secondary'}
                      style={{
                        fontSize: '0.68rem',
                        display: 'inline-flex',
                        alignItems: 'center',
                      }}
                    >
                      {isEnabled ? (
                        <>
                          <FaCheckCircle size={8} className="me-1" />
                          {isOAuth && accountCount > 1
                            ? `${accountCount} accounts`
                            : 'Connected'
                          }
                        </>
                      ) : (
                        'Disabled'
                      )}
                    </Badge>
                  </div>
                )}
              </div>
            </div>
          </Card.Body>

          {/* Expanded section */}
          {isExpanded && (
            <div
              style={{
                borderTop: '1px solid var(--color-border)',
                padding: '1rem 1.25rem',
              }}
              onClick={(e) => e.stopPropagation()}
            >
              {/* OAuth skills */}
              {isOAuth && renderOAuthExpanded(skill)}

              {isDeviceAuth && skill.integration_name === 'codex' && renderCodexExpanded(skill)}

              {isDeviceAuth && skill.integration_name === 'gemini_cli' && renderGeminiCliExpanded(skill)}

              {/* Claude Code browser auth */}
              {isBrowserAuth && skill.integration_name === 'claude_code' && (
                <div className="p-3">
                  {claudeAuthState.status === 'pending' && (
                    <Alert variant="info" className="mb-2">
                      <small>Sign in with your Anthropic account in the browser window. Waiting for authentication...</small>
                    </Alert>
                  )}
                  {claudeAuthState.status === 'failed' && claudeAuthState.error && (
                    <Alert variant="danger" className="mb-2"><small>{claudeAuthState.error}</small></Alert>
                  )}
                  {claudeAuthState.connected && (
                    <Alert variant="success" className="mb-2"><small>Connected to Claude Code</small></Alert>
                  )}
                  <Button
                    size="sm"
                    variant={claudeAuthState.connected ? 'outline-success' : 'primary'}
                    onClick={['starting', 'pending'].includes(claudeAuthState.status) ? handleClaudeCancel : handleClaudeConnect}
                    disabled={connectingProvider === 'claude_code'}
                  >
                    {['starting', 'pending'].includes(claudeAuthState.status) ? 'Cancel' : claudeAuthState.connected ? 'Reconnect' : 'Connect with Anthropic'}
                  </Button>
                </div>
              )}

              {/* Non-OAuth skills: manual credential flow */}
              {!isOAuth && !isDeviceAuth && !isBrowserAuth && (
                <>
                  {/* Enable/Disable Toggle */}
                  <div className="d-flex align-items-center justify-content-between mb-3">
                    <div className="d-flex align-items-center gap-2">
                      {isEnabled ? (
                        <FaToggleOn
                          size={22}
                          style={{ color: '#2d9d78', cursor: 'pointer' }}
                          onClick={() => handleToggleSkill(skill)}
                        />
                      ) : (
                        <FaToggleOff
                          size={22}
                          style={{ color: 'var(--color-muted)', cursor: 'pointer' }}
                          onClick={() => handleToggleSkill(skill)}
                        />
                      )}
                      <span
                        style={{
                          color: 'var(--color-foreground)',
                          fontSize: '0.85rem',
                          fontWeight: 500,
                        }}
                      >
                        {isEnabled ? 'Enabled' : 'Disabled'}
                      </span>
                      {saving === skill.integration_name && (
                        <Spinner
                          animation="border"
                          size="sm"
                          style={{ width: 14, height: 14, borderWidth: 1.5 }}
                        />
                      )}
                    </div>

                    {/* Requires Approval Toggle */}
                    {!!config && (
                      <div className="d-flex align-items-center gap-2">
                        <Form.Check
                          type="switch"
                          id={`approval-${skill.integration_name}`}
                          label={
                            <span style={{ fontSize: '0.8rem', color: 'var(--color-muted)' }}>
                              Requires approval
                            </span>
                          }
                          checked={config?.requires_approval ?? false}
                          onChange={() => handleToggleApproval(skill)}
                          disabled={saving === skill.integration_name}
                        />
                      </div>
                    )}
                  </div>

                  {/* Credential Form (non-channel skills) */}
                  {!!config && isEnabled && (
                    <>
                      {skill.integration_name === 'whatsapp' ? (
                        <WhatsAppChannelCard />
                      ) : (
                        <>
                          <div className="mb-2" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <FaKey size={12} style={{ color: 'var(--color-muted)' }} />
                            <span
                              style={{
                                fontSize: '0.8rem',
                                fontWeight: 600,
                                color: 'var(--color-foreground)',
                                textTransform: 'uppercase',
                                letterSpacing: '0.5px',
                              }}
                            >
                              Credentials
                            </span>
                          </div>

                          {skill.credentials.map((cred) => {
                            const storedKeys = credentialStatuses[skill.integration_name] || [];
                            const isStored = storedKeys.includes(cred.key);
                            return (
                            <Form.Group key={cred.key} className="mb-2">
                              <Form.Label
                                style={{
                                  fontSize: '0.78rem',
                                  color: 'var(--color-muted)',
                                  marginBottom: '0.25rem',
                                  display: 'flex',
                                  alignItems: 'center',
                                  gap: 6,
                                }}
                              >
                                {cred.label}
                                {cred.required && (
                                  <span className="text-danger ms-1">*</span>
                                )}
                                {isStored && (
                                  <span style={{ color: '#28a745', fontSize: '0.72rem', display: 'flex', alignItems: 'center', gap: 3 }}>
                                    <FaCheckCircle size={10} /> Saved
                                  </span>
                                )}
                              </Form.Label>
                              <Form.Control
                                type={cred.type === 'password' ? 'password' : 'text'}
                                size="sm"
                                placeholder={isStored ? '••••••••  (saved — enter new value to update)' : `Enter ${cred.label.toLowerCase()}`}
                                value={formValues[cred.key] || ''}
                                onChange={(e) =>
                                  handleCredentialChange(
                                    skill.integration_name,
                                    cred.key,
                                    e.target.value
                                  )
                                }
                                style={{
                                  background: 'var(--surface-contrast, rgba(0,0,0,0.2))',
                                  border: '1px solid var(--color-border)',
                                  color: 'var(--color-foreground)',
                                  fontSize: '0.82rem',
                                }}
                              />
                            </Form.Group>
                            );
                          })}

                          <div className="d-flex gap-2">
                            <Button
                              variant="primary"
                              size="sm"
                              className="flex-grow-1"
                              onClick={() => handleSaveCredentials(skill)}
                              disabled={saving === skill.integration_name}
                            >
                              {saving === skill.integration_name ? (
                                <Spinner
                                  animation="border"
                                  size="sm"
                                  style={{ width: 14, height: 14, borderWidth: 1.5 }}
                                  className="me-2"
                                />
                              ) : (
                                <FaSave className="me-2" size={12} />
                              )}
                              Save Credentials
                            </Button>
                            <Button
                              variant="outline-success"
                              size="sm"
                              onClick={() => handleTestSkill(skill)}
                              disabled={testingSkill === skill.integration_name || saving === skill.integration_name}
                              title="Test connection"
                            >
                              {testingSkill === skill.integration_name ? (
                                <Spinner
                                  animation="border"
                                  size="sm"
                                  style={{ width: 14, height: 14, borderWidth: 1.5 }}
                                />
                              ) : (
                                <FaPlay size={12} />
                              )}
                            </Button>
                          </div>
                        </>
                      )}
                    </>
                  )}

                  {/* Prompt to enable first if not configured */}
                  {!config && (
                    <div className="text-center py-2">
                      <p
                        className="text-muted mb-2"
                        style={{ fontSize: '0.82rem' }}
                      >
                        Enable this integration to configure credentials
                      </p>
                      <Button
                        variant="outline-primary"
                        size="sm"
                        onClick={() => handleToggleSkill(skill)}
                        disabled={saving === skill.integration_name}
                      >
                        {saving === skill.integration_name ? (
                          <Spinner
                            animation="border"
                            size="sm"
                            style={{ width: 14, height: 14, borderWidth: 1.5 }}
                            className="me-2"
                          />
                        ) : (
                          <FaCog className="me-2" size={12} />
                        )}
                        Enable {skill.display_name}
                      </Button>
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </Card>
      </Col>
    );
  };

  // Count total active connections (OAuth accounts + manual configs)
  const totalActive =
    Object.values(oauthStatuses).reduce((sum, s) => sum + (s.accounts?.length || 0), 0) +
    configs.filter((c) => {
      const entry = registry.find((item) => item.integration_name === c.integration_name);
      return c.enabled && entry && entry.auth_type !== 'oauth' && entry.auth_type !== 'device_auth';
    }).length +
    registry.filter((item) => {
      if (item.auth_type !== 'device_auth') return false;
      const storedKeys = credentialStatuses[item.integration_name] || [];
      return storedKeys.includes('auth_json') || storedKeys.includes('session_token');
    }).length;

  return (
    <Card
      className="mb-4"
      style={{
        border: '1px solid var(--color-border)',
        borderRadius: 12,
        background: 'var(--surface-elevated)',
        boxShadow: '0 2px 15px rgba(100, 130, 170, 0.08)',
      }}
    >
      <Card.Header
        style={{
          background: 'transparent',
          borderBottom: '1px solid var(--color-border)',
          padding: '0.75rem 1.25rem',
        }}
      >
        <h6
          className="mb-0 d-flex align-items-center"
          style={{ color: 'var(--color-foreground)' }}
        >
          <FaPlug className="me-2" />
          Integrations
          <Badge
            bg="secondary"
            className="ms-2"
            style={{ fontSize: '0.68rem', fontWeight: 500 }}
          >
            {totalActive} active
          </Badge>
        </h6>
      </Card.Header>
      <Card.Body>
        {error && (
          <Alert
            variant="danger"
            onClose={() => setError(null)}
            dismissible
            className="mb-3"
            style={{ fontSize: '0.85rem' }}
          >
            <FaTimesCircle className="me-2" />
            {error}
          </Alert>
        )}
        {success && (
          <Alert
            variant="success"
            onClose={() => setSuccess(null)}
            dismissible
            className="mb-3"
            style={{ fontSize: '0.85rem' }}
          >
            <FaCheckCircle className="me-2" />
            {success}
          </Alert>
        )}

        {/*
          Default CLI selector — only renders when ≥2 CLIs are
          connected. Single-CLI tenants don't see it (the backend
          autodetect handles routing without a choice to make).
        */}
        <DefaultCliSelector
          configs={configs}
          credentialStatuses={credentialStatuses}
        />

        {loading ? (
          <div className="text-center py-4">
            <Spinner animation="border" size="sm" variant="primary" />
            <p className="text-muted mt-2 mb-0" style={{ fontSize: '0.85rem' }}>
              Loading integrations...
            </p>
          </div>
        ) : registry.length === 0 ? (
          <div className="text-center py-4">
            <FaPlug size={32} className="text-muted mb-2" />
            <p className="text-muted mb-0" style={{ fontSize: '0.85rem' }}>
              No integrations available
            </p>
          </div>
        ) : (
          <Row>{[...registry].sort(sortIntegrations).map(renderSkillCard)}</Row>
        )}
      </Card.Body>
    </Card>
  );
};

export default IntegrationsPanel;
