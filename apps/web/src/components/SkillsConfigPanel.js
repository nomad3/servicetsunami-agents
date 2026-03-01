import { useEffect, useState, useCallback } from 'react';
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
  FaSlack,
  FaEnvelope,
  FaGithub,
  FaWhatsapp,
  FaBook,
  FaTasks,
  FaCalendar,
  FaProjectDiagram,
  FaCog,
  FaCheckCircle,
  FaKey,
  FaSave,
  FaTimesCircle,
  FaToggleOn,
  FaToggleOff,
  FaPlug,
  FaPlay,
  FaLinkedin,
  FaGoogle,
  FaSignOutAlt,
  FaLink,
} from 'react-icons/fa';
import skillConfigService from '../services/skillConfigService';
import skillService from '../services/skillService';
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
  linear: '#5E6AD2',
  linkedin: '#0A66C2',
};

// Provider brand colors and icons for OAuth buttons
const OAUTH_BRAND = {
  google: { label: 'Google', icon: FaGoogle, color: '#4285F4', bg: '#fff', textColor: '#333' },
  github: { label: 'GitHub', icon: FaGithub, color: '#24292e', bg: '#24292e', textColor: '#fff' },
  linkedin: { label: 'LinkedIn', icon: FaLinkedin, color: '#0A66C2', bg: '#0A66C2', textColor: '#fff' },
};

const SkillsConfigPanel = () => {
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
  const [connectingProvider, setConnectingProvider] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const [registryRes, configsRes] = await Promise.all([
        skillConfigService.getRegistry(),
        skillConfigService.getAll(),
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
            const res = await skillConfigService.oauthStatus(provider);
            statuses[provider] = res.data?.connected ?? false;
          } catch {
            statuses[provider] = false;
          }
        })
      );
      setOauthStatuses(statuses);
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

  // Listen for OAuth popup messages
  useEffect(() => {
    const handleMessage = (event) => {
      if (event.data?.type === 'oauth-success') {
        setSuccess(`Connected to ${event.data.provider || 'provider'}`);
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
    configs.find((c) => c.skill_name === skillName);

  const getIcon = (iconName) => {
    const IconComponent = ICON_MAP[iconName];
    return IconComponent ? <IconComponent size={24} /> : <FaPlug size={24} />;
  };

  const handleOAuthConnect = async (provider) => {
    try {
      setConnectingProvider(provider);
      const res = await skillConfigService.oauthAuthorize(provider);
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

  const handleOAuthDisconnect = async (provider) => {
    try {
      setSaving(provider);
      await skillConfigService.oauthDisconnect(provider);
      setSuccess(`Disconnected from ${provider}`);
      setTimeout(() => setSuccess(null), 3000);
      await fetchData();
    } catch (err) {
      setError(`Failed to disconnect ${provider}`);
      setTimeout(() => setError(null), 5000);
    } finally {
      setSaving(null);
    }
  };

  const handleToggleSkill = async (skill) => {
    const existing = getConfigForSkill(skill.skill_name);
    try {
      setSaving(skill.skill_name);
      if (existing) {
        await skillConfigService.update(existing.id, {
          enabled: !existing.enabled,
        });
      } else {
        await skillConfigService.create({
          skill_name: skill.skill_name,
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
    const existing = getConfigForSkill(skill.skill_name);
    if (!existing) return;
    try {
      setSaving(skill.skill_name);
      await skillConfigService.update(existing.id, {
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
    const existing = getConfigForSkill(skill.skill_name);
    if (!existing) return;

    const formValues = credentialForms[skill.skill_name] || {};
    const credentialsToSave = skill.credentials.filter(
      (cred) => formValues[cred.key]?.trim()
    );

    if (credentialsToSave.length === 0) {
      setError('Please fill in at least one credential field');
      setTimeout(() => setError(null), 5000);
      return;
    }

    try {
      setSaving(skill.skill_name);
      for (const cred of credentialsToSave) {
        await skillConfigService.addCredential(existing.id, {
          credential_key: cred.key,
          value: formValues[cred.key],
          credential_type: cred.type === 'password' ? 'api_key' : 'text',
        });
      }
      setCredentialForms((prev) => ({
        ...prev,
        [skill.skill_name]: {},
      }));
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
      setTestingSkill(skill.skill_name);
      setError(null);
      const res = await skillService.execute({
        skill_name: skill.skill_name,
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

  // ---------------------------------------------------------------------------
  // OAuth skill card (expanded section)
  // ---------------------------------------------------------------------------
  const renderOAuthExpanded = (skill) => {
    const provider = skill.oauth_provider;
    const brand = OAUTH_BRAND[provider] || { label: provider, icon: FaLink, bg: '#555', textColor: '#fff' };
    const BrandIcon = brand.icon;
    const isConnected = oauthStatuses[provider] ?? false;
    const isConnecting = connectingProvider === provider;
    const isSaving = saving === provider;

    if (isConnected) {
      return (
        <div className="text-center py-2">
          <div className="d-flex align-items-center justify-content-center gap-2 mb-3">
            <FaCheckCircle style={{ color: '#2d9d78' }} size={18} />
            <span style={{ color: '#2d9d78', fontWeight: 600, fontSize: '0.9rem' }}>
              Connected
            </span>
          </div>
          <Button
            variant="outline-danger"
            size="sm"
            onClick={() => handleOAuthDisconnect(provider)}
            disabled={isSaving}
          >
            {isSaving ? (
              <Spinner animation="border" size="sm" style={{ width: 14, height: 14, borderWidth: 1.5 }} className="me-2" />
            ) : (
              <FaSignOutAlt className="me-2" size={12} />
            )}
            Disconnect
          </Button>
        </div>
      );
    }

    return (
      <div className="text-center py-2">
        <Button
          size="sm"
          onClick={() => handleOAuthConnect(provider)}
          disabled={isConnecting}
          style={{
            background: brand.bg,
            color: brand.textColor,
            border: provider === 'google' ? '1px solid #dadce0' : 'none',
            fontWeight: 500,
            fontSize: '0.88rem',
            padding: '8px 20px',
            borderRadius: 6,
          }}
        >
          {isConnecting ? (
            <Spinner animation="border" size="sm" style={{ width: 14, height: 14, borderWidth: 1.5 }} className="me-2" />
          ) : (
            <BrandIcon className="me-2" size={16} />
          )}
          Connect with {brand.label}
        </Button>
      </div>
    );
  };

  // ---------------------------------------------------------------------------
  // Skill card renderer
  // ---------------------------------------------------------------------------
  const renderSkillCard = (skill) => {
    const config = getConfigForSkill(skill.skill_name);
    const isExpanded = expandedSkill === skill.skill_name;
    const isOAuth = skill.auth_type === 'oauth';
    const isConfigured = isOAuth
      ? (oauthStatuses[skill.oauth_provider] ?? false)
      : !!config;
    const isEnabled = isOAuth
      ? (oauthStatuses[skill.oauth_provider] ?? false)
      : (config?.enabled ?? false);
    const accentColor = SKILL_COLORS[skill.skill_name] || '#6C757D';
    const formValues = credentialForms[skill.skill_name] || {};

    return (
      <Col md={6} lg={4} key={skill.skill_name} className="mb-3">
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
            onClick={() => handleCardClick(skill.skill_name)}
            style={{ padding: '1rem 1.25rem' }}
          >
            <div className="d-flex align-items-center justify-content-between">
              <div className="d-flex align-items-center gap-3">
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
                  }}
                >
                  {getIcon(skill.icon)}
                </div>
                <div>
                  <div
                    className="fw-semibold"
                    style={{ color: 'var(--color-foreground)', fontSize: '0.95rem' }}
                  >
                    {skill.display_name}
                  </div>
                  <div
                    className="text-muted"
                    style={{ fontSize: '0.78rem', lineHeight: 1.3 }}
                  >
                    {skill.description}
                  </div>
                </div>
              </div>
              <div className="d-flex align-items-center gap-2">
                {isConfigured && (
                  <Badge
                    bg={isEnabled ? 'success' : 'secondary'}
                    style={{ fontSize: '0.68rem' }}
                  >
                    {isEnabled ? (
                      <>
                        <FaCheckCircle size={8} className="me-1" />
                        Connected
                      </>
                    ) : (
                      'Disabled'
                    )}
                  </Badge>
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

              {/* Non-OAuth skills: manual credential flow */}
              {!isOAuth && (
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
                      {saving === skill.skill_name && (
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
                          id={`approval-${skill.skill_name}`}
                          label={
                            <span style={{ fontSize: '0.8rem', color: 'var(--color-muted)' }}>
                              Requires approval
                            </span>
                          }
                          checked={config?.requires_approval ?? false}
                          onChange={() => handleToggleApproval(skill)}
                          disabled={saving === skill.skill_name}
                        />
                      </div>
                    )}
                  </div>

                  {/* Channel Management (WhatsApp etc.) */}
                  {!!config && isEnabled && skill.channel_type && (
                    <WhatsAppChannelCard />
                  )}

                  {/* Credential Form (non-channel skills) */}
                  {!!config && isEnabled && !skill.channel_type && (
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

                      {skill.credentials.map((cred) => (
                        <Form.Group key={cred.key} className="mb-2">
                          <Form.Label
                            style={{
                              fontSize: '0.78rem',
                              color: 'var(--color-muted)',
                              marginBottom: '0.25rem',
                            }}
                          >
                            {cred.label}
                            {cred.required && (
                              <span className="text-danger ms-1">*</span>
                            )}
                          </Form.Label>
                          <Form.Control
                            type={cred.type === 'password' ? 'password' : 'text'}
                            size="sm"
                            placeholder={`Enter ${cred.label.toLowerCase()}`}
                            value={formValues[cred.key] || ''}
                            onChange={(e) =>
                              handleCredentialChange(
                                skill.skill_name,
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
                      ))}

                      <div className="d-flex gap-2">
                        <Button
                          variant="primary"
                          size="sm"
                          className="flex-grow-1"
                          onClick={() => handleSaveCredentials(skill)}
                          disabled={saving === skill.skill_name}
                        >
                          {saving === skill.skill_name ? (
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
                          disabled={testingSkill === skill.skill_name || saving === skill.skill_name}
                          title="Test connection"
                        >
                          {testingSkill === skill.skill_name ? (
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
                        disabled={saving === skill.skill_name}
                      >
                        {saving === skill.skill_name ? (
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
          Connected Apps
          <Badge
            bg="secondary"
            className="ms-2"
            style={{ fontSize: '0.68rem', fontWeight: 500 }}
          >
            {Object.values(oauthStatuses).filter(Boolean).length + configs.filter((c) => c.enabled).length} active
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
          <Row>{registry.map(renderSkillCard)}</Row>
        )}
      </Card.Body>
    </Card>
  );
};

export default SkillsConfigPanel;
