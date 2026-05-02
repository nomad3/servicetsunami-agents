/*
 * Organization page — replaces the prior `/tenants` dashboard.
 *
 * Tabs:
 *   - Overview : tenant identity card + usage stats (refined version of
 *                the prior page).
 *   - Members  : list of users in the tenant via GET /users (new
 *                endpoint added with this PR). Displays role, email,
 *                active state.
 *   - Audit    : cross-agent audit log via GET /audit/agents. Renders
 *                only when the current user is a superuser.
 *
 * Replaces the prior page which mixed tenant identity with stats and
 * had no way to see members or audit. The "Organizations" name is also
 * misleading — a user belongs to ONE tenant — so the title is now
 * "Organization" (singular) and the sidebar entry follows suit.
 */
import { useEffect, useMemo, useState } from 'react';
import { Alert, Badge, Card, Spinner, Table } from 'react-bootstrap';
import {
  FaBuilding,
  FaCalendarCheck,
  FaCloudUploadAlt,
  FaCommentDots,
  FaComments,
  FaDatabase,
  FaHistory,
  FaLayerGroup,
  FaNetworkWired,
  FaProjectDiagram,
  FaRobot,
  FaUserCircle,
  FaUserShield,
  FaUsers,
} from 'react-icons/fa';

import { useAuth } from '../App';
import Layout from '../components/Layout';
import api from '../services/api';
import './TenantsPage.css';


const TABS = [
  { key: 'overview', icon: FaBuilding, label: 'Overview' },
  { key: 'members',  icon: FaUsers,    label: 'Members' },
  { key: 'audit',    icon: FaHistory,  label: 'Audit',   superuserOnly: true },
];


const TenantsPage = () => {
  const { user } = useAuth();

  const [activeTab, setActiveTab] = useState('overview');

  // Overview state
  const [tenant, setTenant] = useState(null);
  const [stats, setStats] = useState(null);
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [overviewError, setOverviewError] = useState(null);

  // Members
  const [members, setMembers] = useState(null);
  const [membersLoading, setMembersLoading] = useState(false);
  const [membersError, setMembersError] = useState(null);

  // Audit
  const [auditEntries, setAuditEntries] = useState(null);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditError, setAuditError] = useState(null);

  const isSuperuser = !!user?.is_superuser;

  // Filter the tab list to what this user can actually see — saves
  // the "you are not authorized" surprise on the audit tab.
  const visibleTabs = useMemo(
    () => TABS.filter((t) => !t.superuserOnly || isSuperuser),
    [isSuperuser],
  );

  // ── Overview load ──────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setOverviewLoading(true);
        const me = await api.get('/users/me');
        if (cancelled) return;
        setTenant(me.data.tenant);
        try {
          const s = await api.get('/analytics/dashboard');
          if (!cancelled) setStats(s.data);
        } catch {
          // analytics is non-fatal — overview tab still shows the
          // tenant card.
        }
      } catch (err) {
        if (!cancelled) setOverviewError('Failed to load tenant data.');
      } finally {
        if (!cancelled) setOverviewLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // ── Members load (lazy, on tab switch) ─────────────────────────────
  useEffect(() => {
    if (activeTab !== 'members' || members !== null) return;
    let cancelled = false;
    (async () => {
      try {
        setMembersLoading(true);
        const r = await api.get('/users');
        if (!cancelled) setMembers(r.data || []);
      } catch (err) {
        if (!cancelled) setMembersError('Could not load members.');
      } finally {
        if (!cancelled) setMembersLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [activeTab, members]);

  // ── Audit load (lazy, on tab switch, superuser only) ───────────────
  useEffect(() => {
    if (activeTab !== 'audit' || !isSuperuser || auditEntries !== null) return;
    let cancelled = false;
    (async () => {
      try {
        setAuditLoading(true);
        const r = await api.get('/audit/agents', { params: { limit: 100 } });
        if (!cancelled) setAuditEntries(r.data || []);
      } catch (err) {
        if (!cancelled) setAuditError('Could not load audit log.');
      } finally {
        if (!cancelled) setAuditLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [activeTab, isSuperuser, auditEntries]);

  // ── Render helpers ─────────────────────────────────────────────────
  const StatItem = ({ icon: Icon, label, value, color = 'primary' }) => (
    <div className="stat-item-grid">
      <div className={`stat-icon-wrapper bg-${color}-subtle`}>
        <Icon className={`text-${color}`} size={20} />
      </div>
      <div className="stat-content">
        <div className="stat-value">{value ?? '—'}</div>
        <div className="stat-label">{label}</div>
      </div>
    </div>
  );

  return (
    <Layout>
      <div className="tenants-page">
        <header className="ap-page-header">
          <div>
            <h1 className="ap-page-title">
              <FaBuilding className="text-primary me-2" />
              Organization
            </h1>
            <p className="ap-page-subtitle">
              Tenant identity, members, and audit log.
            </p>
          </div>
        </header>

        <div className="ap-chip-row" role="tablist">
          {visibleTabs.map(({ key, icon: Icon, label }) => (
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
          {/* ── OVERVIEW ───────────────────────────────────────────── */}
          {activeTab === 'overview' && (
            <>
              {overviewLoading ? (
                <div className="text-center py-5">
                  <Spinner animation="border" variant="primary" />
                </div>
              ) : overviewError ? (
                <Alert variant="danger">{overviewError}</Alert>
              ) : (
                <>
                  <div className="org-identity-grid mb-4">
                    <Card className="tenant-card">
                      <Card.Body>
                        <div className="d-flex align-items-center justify-content-between mb-3">
                          <div className="icon-pill-sm"><FaBuilding size={20} /></div>
                          <Badge bg="primary">Tenant</Badge>
                        </div>
                        <h6 className="text-muted mb-1">Organization Name</h6>
                        <div className="h4 fw-bold mb-2">{tenant?.name || '—'}</div>
                        <div className="small text-muted text-truncate">ID: {tenant?.id}</div>
                      </Card.Body>
                    </Card>

                    <Card className="tenant-card">
                      <Card.Body>
                        <div className="d-flex align-items-center justify-content-between mb-3">
                          <div className="icon-pill-sm"><FaUserCircle size={20} /></div>
                          <Badge bg={isSuperuser ? 'warning' : 'success'}>
                            {isSuperuser ? 'Admin' : 'Member'}
                          </Badge>
                        </div>
                        <h6 className="text-muted mb-1">Logged in as</h6>
                        <div className="h4 fw-bold mb-2">{user?.full_name || user?.email}</div>
                        <div className="small text-muted">{user?.email}</div>
                      </Card.Body>
                    </Card>

                    <Card className="tenant-card">
                      <Card.Body>
                        <div className="d-flex align-items-center justify-content-between mb-3">
                          <div className="icon-pill-sm"><FaCalendarCheck size={20} /></div>
                          <Badge bg="success">Active</Badge>
                        </div>
                        <h6 className="text-muted mb-1">Account Status</h6>
                        <div className="h4 fw-bold text-success mb-2">Operational</div>
                        <div className="small text-success">All systems nominal</div>
                      </Card.Body>
                    </Card>
                  </div>

                  {stats && (
                    <Card className="tenant-card mb-4">
                      <div className="card-header-transparent">
                        <h5 className="mb-0">Platform usage</h5>
                      </div>
                      <Card.Body>
                        <div className="stat-grid">
                          <StatItem icon={FaRobot}            label="Agents"       value={stats.overview?.total_agents}        color="primary" />
                          <StatItem icon={FaCloudUploadAlt}   label="Deployments"  value={stats.overview?.total_deployments}   color="success" />
                          <StatItem icon={FaDatabase}         label="Datasets"     value={stats.overview?.total_datasets}      color="info" />
                          <StatItem icon={FaLayerGroup}       label="Vector stores" value={stats.overview?.total_vector_stores} color="danger" />
                          <StatItem icon={FaCommentDots}      label="Chat sessions" value={stats.overview?.total_chat_sessions} color="primary" />
                          <StatItem icon={FaComments}         label="Messages"     value={stats.activity?.total_messages}      color="info" />
                          <StatItem icon={FaNetworkWired}     label="Data sources" value={stats.overview?.total_data_sources}  color="success" />
                          <StatItem icon={FaProjectDiagram}   label="Pipelines"    value={stats.overview?.total_pipelines}     color="warning" />
                        </div>
                      </Card.Body>
                    </Card>
                  )}
                </>
              )}
            </>
          )}

          {/* ── MEMBERS ────────────────────────────────────────────── */}
          {activeTab === 'members' && (
            <Card className="tenant-card">
              <Card.Body>
                <h5 className="mb-3">Members</h5>
                {membersLoading ? (
                  <div className="text-center py-4">
                    <Spinner animation="border" variant="primary" size="sm" />
                  </div>
                ) : membersError ? (
                  <Alert variant="warning">{membersError}</Alert>
                ) : !members?.length ? (
                  <Alert variant="info">No members yet.</Alert>
                ) : (
                  <Table hover responsive className="mb-0">
                    <thead>
                      <tr>
                        <th>Name</th>
                        <th>Email</th>
                        <th>Role</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {members.map((m) => (
                        <tr key={m.id}>
                          <td>{m.full_name || <span className="text-muted">—</span>}</td>
                          <td>{m.email}</td>
                          <td>
                            {m.role === 'admin'
                              ? <Badge bg="warning"><FaUserShield className="me-1" size={10} />Admin</Badge>
                              : <Badge bg="secondary">Member</Badge>}
                          </td>
                          <td>
                            {m.is_active
                              ? <Badge bg="success">Active</Badge>
                              : <Badge bg="secondary">Inactive</Badge>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                )}
                <p className="text-muted mt-3 mb-0" style={{ fontSize: '0.85rem' }}>
                  Inviting and removing members will land in a follow-up — today the API exposes read access only.
                </p>
              </Card.Body>
            </Card>
          )}

          {/* ── AUDIT (superuser-only tab) ─────────────────────────── */}
          {activeTab === 'audit' && isSuperuser && (
            <Card className="tenant-card">
              <Card.Body>
                <h5 className="mb-3">Cross-agent audit log</h5>
                {auditLoading ? (
                  <div className="text-center py-4">
                    <Spinner animation="border" variant="primary" size="sm" />
                  </div>
                ) : auditError ? (
                  <Alert variant="warning">{auditError}</Alert>
                ) : !auditEntries?.length ? (
                  <Alert variant="info">No audit entries yet.</Alert>
                ) : (
                  <Table hover responsive size="sm" className="mb-0 audit-table">
                    <thead>
                      <tr>
                        <th>When</th>
                        <th>Type</th>
                        <th>Status</th>
                        <th>Latency</th>
                        <th>Tokens</th>
                        <th>Cost</th>
                        <th>Input summary</th>
                      </tr>
                    </thead>
                    <tbody>
                      {auditEntries.map((row) => (
                        <tr key={row.id}>
                          <td className="text-nowrap">{new Date(row.created_at).toLocaleString()}</td>
                          <td><code>{row.invocation_type}</code></td>
                          <td>
                            {row.status === 'success'
                              ? <Badge bg="success">{row.status}</Badge>
                              : <Badge bg="danger">{row.status}</Badge>}
                          </td>
                          <td className="text-nowrap">{row.latency_ms ? `${row.latency_ms}ms` : '—'}</td>
                          <td className="text-nowrap">
                            {(row.input_tokens || 0) + (row.output_tokens || 0) || '—'}
                          </td>
                          <td className="text-nowrap">{row.cost_usd ? `$${Number(row.cost_usd).toFixed(4)}` : '—'}</td>
                          <td className="text-truncate" style={{ maxWidth: 320 }} title={row.input_summary}>
                            {row.input_summary || <span className="text-muted">—</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                )}
                <p className="text-muted mt-3 mb-0" style={{ fontSize: '0.85rem' }}>
                  Showing the latest 100 entries across all agents in this tenant. Admin-only.
                </p>
              </Card.Body>
            </Card>
          )}
        </div>
      </div>
    </Layout>
  );
};

export default TenantsPage;
