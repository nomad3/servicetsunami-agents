/*
 * Settings — tenant-scoped admin page.
 *
 * Replaces the prior scaffolded form (which had `placeholder` inputs and
 * no save handlers) with a tab layout where every visible control is
 * either wired or explicitly tagged "coming soon" so customers don't
 * type into dead UI.
 *
 * Tabs (only what's actually wired):
 *   - Profile        : GET/PUT /users/me — full_name editable, email
 *                      read-only, password change via existing
 *                      /password-recovery/{email}.
 *   - Workspace      : tenant identity + link to /settings/branding
 *                      (whitelabel theme editor).
 *   - Plan & Limits  : read-only display of GET /features — max_agents,
 *                      monthly_token_limit, default_cli_platform, etc.
 *                      "Change default CLI" links to the Integrations
 *                      page where the inline selector lives.
 *   - Database       : Postgres admin block (kept — already wired).
 */
import { useEffect, useState } from 'react';
import { Alert, Badge, Form, Spinner } from 'react-bootstrap';
import {
  FaBuilding,
  FaDatabase,
  FaEnvelope,
  FaKey,
  FaPalette,
  FaUserCircle,
  FaTachometerAlt,
  FaArrowRight,
} from 'react-icons/fa';
import { Link } from 'react-router-dom';

import { useAuth } from '../App';
import Layout from '../components/Layout';
import api from '../services/api';
import { brandingService } from '../services/branding';
import './SettingsPage.css';


const TABS = [
  { key: 'profile',   icon: FaUserCircle,    label: 'Profile' },
  { key: 'workspace', icon: FaBuilding,      label: 'Workspace' },
  { key: 'plan',      icon: FaTachometerAlt, label: 'Plan & Limits' },
  { key: 'database',  icon: FaDatabase,      label: 'Database' },
  { key: 'gestures',  icon: FaPalette,       label: 'Gestures' },
];

const CLI_LABELS = {
  claude_code: 'Claude Code',
  copilot_cli: 'GitHub Copilot CLI',
  codex: 'Codex CLI',
  gemini_cli: 'Gemini CLI',
  opencode: 'OpenCode (local)',
};


const SettingsPage = () => {
  const { user, refreshUser } = useAuth();

  const [activeTab, setActiveTab] = useState('profile');

  // Profile state
  const [fullName, setFullName] = useState('');
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileSavedAt, setProfileSavedAt] = useState(null);
  const [profileError, setProfileError] = useState(null);
  const [resetSent, setResetSent] = useState(false);

  // Tenant features (Plan & Limits)
  const [features, setFeatures] = useState(null);
  const [featuresLoading, setFeaturesLoading] = useState(true);

  // Postgres status (Database tab)
  const [postgresStatus, setPostgresStatus] = useState(null);
  const [postgresLoading, setPostgresLoading] = useState(true);
  const [postgresInitializing, setPostgresInitializing] = useState(false);
  const [postgresMessage, setPostgresMessage] = useState(null);

  // ── Load on mount ──────────────────────────────────────────────────
  useEffect(() => {
    if (user) setFullName(user.full_name || '');
  }, [user]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await brandingService.getFeatures();
        if (!cancelled) setFeatures(data);
      } catch (err) {
        // non-fatal — Plan tab will show a friendly empty state
      } finally {
        if (!cancelled) setFeaturesLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (activeTab !== 'database' || postgresStatus) return;
    fetchPostgresStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  const fetchPostgresStatus = async () => {
    try {
      setPostgresLoading(true);
      const r = await api.get('/postgres/status');
      setPostgresStatus(r.data);
    } catch (err) {
      // Surface in the tab itself, not a global toast
    } finally {
      setPostgresLoading(false);
    }
  };

  // ── Profile actions ────────────────────────────────────────────────
  const handleProfileSave = async (e) => {
    e?.preventDefault?.();
    setSavingProfile(true);
    setProfileError(null);
    try {
      await api.put('/users/me', { full_name: fullName });
      setProfileSavedAt(Date.now());
      // Refresh the auth context so the avatar / sidebar shows the new name.
      if (typeof refreshUser === 'function') {
        try { await refreshUser(); } catch { /* non-fatal */ }
      }
    } catch (err) {
      setProfileError(err.response?.data?.detail || 'Could not update profile');
    } finally {
      setSavingProfile(false);
    }
  };

  const handlePasswordReset = async () => {
    if (!user?.email) return;
    try {
      await api.post(`/auth/password-recovery/${encodeURIComponent(user.email)}`);
      setResetSent(true);
    } catch {
      // Endpoint deliberately returns 200 even when email isn't found
      // (no enumeration). We treat anything non-network as success.
      setResetSent(true);
    }
  };

  // ── Postgres actions ───────────────────────────────────────────────
  const handlePostgresInitialize = async () => {
    try {
      setPostgresInitializing(true);
      setPostgresMessage(null);
      await api.post('/postgres/initialize');
      setPostgresMessage({ type: 'success', text: 'PostgreSQL initialized successfully.' });
      fetchPostgresStatus();
    } catch (err) {
      setPostgresMessage({
        type: 'danger',
        text: err.response?.data?.detail || 'Initialization failed.',
      });
    } finally {
      setPostgresInitializing(false);
    }
  };

  // ── Render ─────────────────────────────────────────────────────────
  return (
    <Layout>
      <div className="settings-page">
        <header className="ap-page-header">
          <div>
            <h1 className="ap-page-title">Settings</h1>
            <p className="ap-page-subtitle">
              Personal profile, workspace branding, plan limits, and infrastructure.
            </p>
          </div>
        </header>

        <div className="ap-chip-row" role="tablist">
          {TABS.map(({ key, icon: Icon, label }) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={activeTab === key}
              className={`ap-chip-filter ${activeTab === key ? 'active' : ''}`}
              onClick={() => setActiveTab(key)}
            >
              <Icon size={12} /> {label}
            </button>
          ))}
        </div>

        <div className="tab-content-inner">
          {activeTab === 'profile' && (
            <article className="ap-card">
              <div className="ap-card-body">
                <h3 className="ap-card-title settings-section-title">
                  <FaUserCircle className="me-2" /> Profile
                </h3>
                <Form onSubmit={handleProfileSave}>
                  <Form.Group className="mb-3">
                    <Form.Label>Full name</Form.Label>
                    <Form.Control
                      type="text"
                      value={fullName}
                      onChange={(e) => setFullName(e.target.value)}
                      placeholder="Your display name"
                      disabled={savingProfile}
                    />
                  </Form.Group>
                  <Form.Group className="mb-3">
                    <Form.Label>
                      <FaEnvelope className="me-1" size={11} /> Email
                    </Form.Label>
                    <Form.Control
                      type="email"
                      value={user?.email || ''}
                      disabled
                      readOnly
                    />
                    <Form.Text className="text-muted">
                      Email is the login identity and cannot be changed here. Contact support to migrate to a new address.
                    </Form.Text>
                  </Form.Group>

                  {profileError && (
                    <Alert variant="danger" className="py-2" style={{ fontSize: '0.85rem' }}>
                      {profileError}
                    </Alert>
                  )}
                  {profileSavedAt && !profileError && (
                    <small className="text-success d-block mb-2">Saved.</small>
                  )}

                  <div className="settings-actions">
                    <button
                      type="submit"
                      className="ap-btn-primary"
                      disabled={savingProfile || (fullName === (user?.full_name || ''))}
                    >
                      {savingProfile ? <Spinner animation="border" size="sm" /> : 'Save profile'}
                    </button>
                  </div>
                </Form>

                <hr className="my-4" />

                <h4 className="ap-card-subtitle"><FaKey className="me-2" />Password</h4>
                <p className="text-muted" style={{ fontSize: '0.85rem' }}>
                  Send a password-reset email to your address. The link expires in 1 hour.
                </p>
                {resetSent ? (
                  <Alert variant="success" className="py-2" style={{ fontSize: '0.85rem' }}>
                    Check your inbox at <strong>{user?.email}</strong> for the reset link.
                  </Alert>
                ) : (
                  <button
                    type="button"
                    className="ap-btn-secondary ap-btn-sm"
                    onClick={handlePasswordReset}
                  >
                    Send reset email
                  </button>
                )}
              </div>
            </article>
          )}

          {activeTab === 'workspace' && (
            <article className="ap-card">
              <div className="ap-card-body">
                <h3 className="ap-card-title settings-section-title">
                  <FaPalette className="me-2" /> Workspace branding
                </h3>
                <p className="text-muted" style={{ fontSize: '0.9rem' }}>
                  Customize your tenant&apos;s display name, logo, AI assistant name, and theme colors.
                  Changes apply across the chat UI, marketing landing page, and emails.
                </p>
                <Link to="/settings/branding" className="ap-btn-primary">
                  Open branding editor <FaArrowRight className="ms-2" size={11} />
                </Link>
              </div>
            </article>
          )}

          {activeTab === 'plan' && (
            <article className="ap-card">
              <div className="ap-card-body">
                <h3 className="ap-card-title settings-section-title">
                  <FaTachometerAlt className="me-2" /> Plan &amp; limits
                </h3>
                {featuresLoading ? (
                  <div className="text-center py-4">
                    <Spinner animation="border" variant="primary" size="sm" />
                  </div>
                ) : !features ? (
                  <Alert variant="warning">
                    Could not load tenant features. Try refreshing.
                  </Alert>
                ) : (
                  <div className="settings-limits">
                    <LimitRow
                      label="Max agents"
                      value={features.max_agents ?? '—'}
                    />
                    <LimitRow
                      label="Max agent groups"
                      value={features.max_agent_groups ?? '—'}
                    />
                    <LimitRow
                      label="Monthly tokens"
                      value={features.monthly_token_limit?.toLocaleString() ?? '—'}
                    />
                    <LimitRow
                      label="Storage limit (GB)"
                      value={features.storage_limit_gb ?? '—'}
                    />
                    <LimitRow
                      label="Default CLI"
                      value={
                        <>
                          <Badge bg="info">
                            {features.default_cli_platform
                              ? (CLI_LABELS[features.default_cli_platform] || features.default_cli_platform)
                              : 'Auto (autodetect)'}
                          </Badge>
                          <Link
                            to="/integrations"
                            className="ms-3"
                            style={{ fontSize: '0.85rem' }}
                          >
                            Change on Integrations <FaArrowRight size={10} className="ms-1" />
                          </Link>
                        </>
                      }
                    />
                    <LimitRow
                      label="Memory v2"
                      value={features.use_memory_v2
                        ? <Badge bg="success">Enabled</Badge>
                        : <Badge bg="secondary">Disabled</Badge>}
                    />
                  </div>
                )}
                <Form.Text className="text-muted d-block mt-3">
                  Limits are read-only here. Plan changes go through your account manager.
                </Form.Text>
              </div>
            </article>
          )}

          {activeTab === 'database' && (
            <article className="ap-card">
              <div className="ap-card-body">
                <h3 className="ap-card-title settings-section-title">
                  <FaDatabase className="me-2" /> PostgreSQL
                </h3>
                {postgresLoading ? (
                  <div className="text-center py-4">
                    <Spinner animation="border" variant="primary" size="sm" />
                  </div>
                ) : (
                  <>
                    <div className="settings-limits mb-3">
                      <LimitRow
                        label="Connection"
                        value={
                          postgresStatus?.connected
                            ? <Badge bg="success">Connected</Badge>
                            : <Badge bg="danger">Disconnected</Badge>
                        }
                      />
                      {postgresStatus?.tables !== undefined && (
                        <LimitRow label="Tables" value={postgresStatus.tables} />
                      )}
                    </div>
                    {postgresMessage && (
                      <Alert variant={postgresMessage.type} className="py-2" style={{ fontSize: '0.85rem' }}>
                        {postgresMessage.text}
                      </Alert>
                    )}
                    <button
                      type="button"
                      className="ap-btn-secondary"
                      disabled={postgresInitializing}
                      onClick={handlePostgresInitialize}
                    >
                      {postgresInitializing
                        ? <Spinner animation="border" size="sm" />
                        : 'Initialize / migrate'}
                    </button>
                  </>
                )}
              </div>
            </article>
          )}

          {activeTab === 'gestures' && <GesturesSection />}
        </div>
      </div>
    </Layout>
  );
};


const LimitRow = ({ label, value }) => (
  <div className="settings-limit-row">
    <span className="settings-limit-label">{label}</span>
    <span className="settings-limit-value">{value}</span>
  </div>
);

// Read-only stub — full bindings editor lives in the Luna desktop client.
const GesturesSection = () => {
  const [bindings, setBindings] = useState(null);
  const [updatedAt, setUpdatedAt] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.get('/users/me/gesture-bindings');
        if (cancelled) return;
        setBindings(Array.isArray(res.data?.bindings) ? res.data.bindings : []);
        setUpdatedAt(res.data?.updated_at || null);
      } catch (e) {
        if (!cancelled) setError(e?.response?.data?.detail || e?.message || 'failed to load');
      }
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <article className="ap-card">
      <header className="ap-card-header">
        <h2 className="ap-card-title">Gestures</h2>
      </header>
      <div className="ap-card-body">
        <p style={{ marginBottom: 8 }}>
          Hand-gesture bindings for Luna. Configure recording and binding edits in the Luna desktop client.
        </p>
        {error && <div style={{ color: '#c33', fontSize: 13 }}>Error: {String(error)}</div>}
        {bindings === null && !error && <div>Loading…</div>}
        {bindings !== null && (
          <div style={{ fontSize: 13, color: '#456' }}>
            <div>{bindings.length} binding{bindings.length === 1 ? '' : 's'} synced.</div>
            {updatedAt && <div>Last updated: {new Date(updatedAt).toLocaleString()}</div>}
            {bindings.length === 0 && (
              <div style={{ marginTop: 6, opacity: 0.75 }}>
                No bindings yet. Open the Luna desktop client to record gestures.
              </div>
            )}
          </div>
        )}
      </div>
    </article>
  );
};

export default SettingsPage;
