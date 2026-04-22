import { useEffect, useState } from 'react';
import { Badge, Col, Modal, Nav, Row, Spinner } from 'react-bootstrap';
import { useNavigate, useParams } from 'react-router-dom';
import Layout from '../components/Layout';
import agentService from '../services/agent';
import TestsTabSection from '../components/agent/TestsTabSection';
import api from '../services/api';
import './AgentDetailPage.css';

// Semantic/categorical status colors — kept as meaningful data viz values
const STATUS_COLORS = { active: '#22c55e', error: '#ef4444', inactive: '#94a3b8' };
const ROLE_COLORS = { analyst: '#6f42c1', manager: '#0d6efd', specialist: '#fd7e14' };
const TASK_STATUS_COLORS = {
  completed: '#22c55e', failed: '#ef4444', executing: '#f59e0b',
  thinking: '#f59e0b', queued: '#94a3b8', delegated: '#6f42c1',
};
const PRIORITY_COLORS = { critical: '#ef4444', high: '#f59e0b', normal: '#4dabf7', low: '#94a3b8' };
const AUDIT_STATUS_COLORS = {
  success: '#22c55e', error: '#ef4444', timeout: '#f59e0b', blocked_by_policy: '#fd7e14',
};

const AgentDetailPage = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const [agent, setAgent] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('overview');
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const [publishForm, setPublishForm] = useState({
    protocol: 'openai_chat',
    endpoint_url: '',
    pricing_model: 'free',
    price_per_call_usd: '',
    public: true,
  });
  const [publishBusy, setPublishBusy] = useState(false);
  const [publishError, setPublishError] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const [performanceData, setPerformanceData] = useState(null);
  const [perfLoading, setPerfLoading] = useState(false);
  const [perfWindow, setPerfWindow] = useState('24h');

  const [auditLogs, setAuditLogs] = useState([]);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditFromDt, setAuditFromDt] = useState('');
  const [auditToDt, setAuditToDt] = useState('');

  const [versions, setVersions] = useState([]);
  const [versionsLoading, setVersionsLoading] = useState(false);

  const [allIntegrations, setAllIntegrations] = useState([]);
  const [assignedIntegrations, setAssignedIntegrations] = useState([]);
  const [integrationsLoading, setIntegrationsLoading] = useState(false);

  const apiBase = process.env.REACT_APP_API_BASE_URL || '';
  const token = localStorage.getItem('token');
  const authHeaders = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };

  useEffect(() => {
    setLoading(true);
    Promise.all([
      agentService.getById(id).then(r => setAgent(r.data)),
      agentService.getTasks().then(r => setTasks((r.data || []).filter(t => t.assigned_agent_id === id))).catch(() => {}),
      agentService.getAll().then(r => setAgents(r.data || [])).catch(() => {}),
    ])
      .catch(err => console.error('Failed to load agent:', err))
      .finally(() => setLoading(false));
  }, [id]);

  const fetchPerformance = async (win) => {
    setPerfLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/v1/agents/${id}/performance?window=${win}`, { headers: authHeaders });
      if (res.ok) setPerformanceData(await res.json());
    } catch (e) {
      console.error(e);
    } finally {
      setPerfLoading(false);
    }
  };

  const fetchAuditLogs = async (fromDt, toDt) => {
    setAuditLoading(true);
    try {
      let url = `${apiBase}/api/v1/agents/${id}/audit-log?limit=50`;
      if (fromDt) url += `&from_dt=${encodeURIComponent(fromDt)}`;
      if (toDt) url += `&to_dt=${encodeURIComponent(toDt)}`;
      const res = await fetch(url, { headers: authHeaders });
      if (res.ok) setAuditLogs(await res.json());
    } catch (e) {
      console.error(e);
    } finally {
      setAuditLoading(false);
    }
  };

  const fetchVersions = async () => {
    setVersionsLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/v1/agents/${id}/versions`, { headers: authHeaders });
      if (res.ok) setVersions(await res.json());
    } catch (e) {
      console.error(e);
    } finally {
      setVersionsLoading(false);
    }
  };

  const fetchIntegrations = async () => {
    setIntegrationsLoading(true);
    try {
      const [allRes, assignedRes] = await Promise.all([
        fetch(`${apiBase}/api/v1/integrations`, { headers: authHeaders }),
        fetch(`${apiBase}/api/v1/agents/${id}/integrations`, { headers: authHeaders }),
      ]);
      if (allRes.ok) setAllIntegrations(await allRes.json());
      if (assignedRes.ok) setAssignedIntegrations(await assignedRes.json());
    } catch (e) {
      console.error(e);
    } finally {
      setIntegrationsLoading(false);
    }
  };

  const handleTabSwitch = (tab) => {
    setActiveTab(tab);
    if (tab === 'performance' && !performanceData) fetchPerformance(perfWindow);
    if (tab === 'audit' && auditLogs.length === 0) fetchAuditLogs('', '');
    if (tab === 'versions' && versions.length === 0) fetchVersions();
    if (tab === 'integrations' && allIntegrations.length === 0) fetchIntegrations();
  };

  const handlePerfWindow = (win) => {
    setPerfWindow(win);
    fetchPerformance(win);
  };

  const handleAuditDateChange = (from, to) => {
    setAuditFromDt(from);
    setAuditToDt(to);
    fetchAuditLogs(from, to);
  };

  const handleRollback = async (version) => {
    try {
      await fetch(`${apiBase}/api/v1/agents/${id}/versions/${version}/rollback`, {
        method: 'POST',
        headers: authHeaders,
      });
      fetchVersions();
    } catch (e) {
      console.error(e);
    }
  };

  const handleIntegrationToggle = async (cfgId, isAssigned) => {
    try {
      if (isAssigned) {
        await fetch(`${apiBase}/api/v1/agents/${id}/integrations/${cfgId}`, {
          method: 'DELETE',
          headers: authHeaders,
        });
      } else {
        await fetch(`${apiBase}/api/v1/agents/${id}/integrations`, {
          method: 'POST',
          headers: authHeaders,
          body: JSON.stringify({ integration_config_id: cfgId }),
        });
      }
      fetchIntegrations();
    } catch (e) {
      console.error(e);
    }
  };

  const handleDelete = async () => {
    try {
      setDeleting(true);
      await agentService.delete(id);
      navigate('/agents');
    } catch (err) {
      console.error(err);
    } finally {
      setDeleting(false);
    }
  };

  if (loading) {
    return (
      <Layout>
        <div className="text-center py-5">
          <Spinner animation="border" size="sm" variant="primary" />
        </div>
      </Layout>
    );
  }

  if (!agent) {
    return (
      <Layout>
        <div className="text-center py-5">
          <p style={{ color: 'var(--ap-text-muted)' }}>Agent not found.</p>
          <button type="button" className="ap-btn-secondary ap-btn-sm" onClick={() => navigate('/agents')}>Back to Fleet</button>
        </div>
      </Layout>
    );
  }

  const configSkills = agent.config?.skills || agent.config?.tools || [];
  const agentSkillRecords = agent.skills || [];
  const skillMap = {};
  agentSkillRecords.forEach(s => { skillMap[s.skill_name] = s; });
  configSkills.forEach(s => { if (!skillMap[s]) skillMap[s] = { skill_name: s, proficiency: null, times_used: 0, success_rate: 0 }; });
  const allSkills = Object.values(skillMap);

  const completedTasks = tasks.filter(t => t.status === 'completed').length;
  const totalTasks = tasks.length;
  const successRate = totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0;
  const activeTasks = tasks.filter(t => ['queued', 'thinking', 'executing'].includes(t.status)).length;

  const status = agent.status || 'inactive';

  const assignedIds = new Set((assignedIntegrations || []).map(a =>
    typeof a === 'string' ? a : (a.integration_config_id || a.id)
  ));

  const auditExportUrl = () => {
    let url = `${apiBase}/api/v1/audit/agents/export?agent_id=${id}`;
    if (auditFromDt) url += `&from_dt=${encodeURIComponent(auditFromDt)}`;
    if (auditToDt) url += `&to_dt=${encodeURIComponent(auditToDt)}`;
    return url;
  };

  return (
    <Layout>
      <div className="agent-detail-page" style={{ maxWidth: 1100 }}>
        {/* Header */}
        <div className="detail-header">
          <button
            type="button"
            onClick={() => navigate('/agents')}
            className="ap-inline-link"
            style={{ fontSize: 'var(--ap-fs-sm)', textDecoration: 'none', marginBottom: 12, display: 'inline-block' }}
          >
            &larr; Back to Agent Fleet
          </button>

          <header className="ap-page-header">
            <div>
              <div className="d-flex align-items-center gap-2 mb-1">
                <span style={{ width: 10, height: 10, borderRadius: '50%', background: STATUS_COLORS[status] || STATUS_COLORS.inactive }} />
                <h1 className="ap-page-title">{agent.name}</h1>
              </div>
              <p className="ap-page-subtitle">{agent.description || 'No description'}</p>
              <div className="d-flex gap-2 flex-wrap mt-2">
                <span className="ap-badge-outline" title="Model tier — actual model is selected by the tenant's routed CLI platform">
                  {agent.default_model_tier || 'full'} tier
                </span>
                {agent.role && (
                  <Badge bg="none" style={{ fontSize: 'var(--ap-fs-xs)', backgroundColor: ROLE_COLORS[agent.role] || 'var(--ap-text-subtle)' }}>
                    {agent.role}
                  </Badge>
                )}
                <span className="ap-badge-outline">{agent.autonomy_level || 'supervised'}</span>
              </div>
            </div>
            <div className="ap-page-actions">
              {agent.status === 'production' && (
                <button type="button" className="ap-btn-secondary ap-btn-sm" onClick={() => setPublishOpen(true)}>
                  Publish
                </button>
              )}
              <button type="button" className="ap-btn-danger ap-btn-sm" onClick={() => setDeleteConfirm(true)}>
                Delete
              </button>
            </div>
          </header>
        </div>

        {/* Tabs */}
        <Nav className="tab-nav" as="ul">
          {['overview', 'relations', 'tasks', 'config', 'performance', 'audit', 'versions', 'integrations', 'tests'].map(tab => (
            <Nav.Item as="li" key={tab}>
              <Nav.Link
                className={activeTab === tab ? 'active' : ''}
                onClick={() => handleTabSwitch(tab)}
                style={{ textTransform: 'capitalize' }}
              >
                {tab}
              </Nav.Link>
            </Nav.Item>
          ))}
        </Nav>

        {/* Overview Tab */}
        {activeTab === 'overview' && (
          <div>
            <Row className="g-3 mb-4">
              {[
                { label: 'Total Tasks', value: totalTasks },
                { label: 'Completed', value: completedTasks },
                { label: 'Active', value: activeTasks },
                { label: 'Success Rate', value: `${successRate}%` },
              ].map(s => (
                <Col md={3} sm={6} key={s.label}>
                  <article className="ap-card h-100">
                    <div className="ap-card-body">
                      <div className="stat-value">{s.value}</div>
                      <div className="stat-label">{s.label}</div>
                    </div>
                  </article>
                </Col>
              ))}
            </Row>

            <div className="ap-section-label">
              Skills ({allSkills.length})
            </div>
            {allSkills.length === 0 ? (
              <p style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)' }}>No skills configured.</p>
            ) : (
              <Row className="g-2 mb-4">
                {allSkills.map(skill => (
                  <Col md={6} lg={4} key={skill.skill_name}>
                    <article className="ap-card h-100">
                      <div className="ap-card-body skill-card-body">
                        <div className="d-flex justify-content-between align-items-center mb-1">
                          <span style={{ fontSize: 'var(--ap-fs-sm)', fontWeight: 500, color: 'var(--ap-text)' }}>
                            {skill.skill_name.replace(/_/g, ' ')}
                          </span>
                          {skill.learned_from && (
                            <span className="ap-badge-outline">
                              {skill.learned_from}
                            </span>
                          )}
                        </div>
                        {skill.proficiency !== null && skill.proficiency !== undefined && (
                          <div className="d-flex align-items-center gap-2 mb-1">
                            <div className="proficiency-bar" style={{ flex: 1 }}>
                              <div className="fill" style={{ width: `${Math.round(skill.proficiency * 100)}%` }} />
                            </div>
                            <span style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)', minWidth: 28 }}>
                              {Math.round(skill.proficiency * 100)}%
                            </span>
                          </div>
                        )}
                        <div className="d-flex gap-3" style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>
                          <span>Used {skill.times_used || 0}x</span>
                          {skill.success_rate > 0 && <span>Success {Math.round(skill.success_rate * 100)}%</span>}
                        </div>
                      </div>
                    </article>
                  </Col>
                ))}
              </Row>
            )}
          </div>
        )}

        {/* Relations Tab */}
        {activeTab === 'relations' && (
          <div>
            <article className="ap-card">
              <div className="ap-card-body">
                {agents.filter(a => a.id !== agent.id).length === 0 ? (
                  <p style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)', margin: 0 }}>No other agents in the fleet.</p>
                ) : (
                  agents.filter(a => a.id !== agent.id).map(other => (
                    <div key={other.id} className="relation-row">
                      <span style={{ color: 'var(--ap-text-muted)' }}>&harr;</span>
                      <button
                        type="button"
                        className="ap-inline-link"
                        style={{ textDecoration: 'none', fontWeight: 500 }}
                        onClick={() => navigate(`/agents/${other.id}`)}
                      >
                        {other.name}
                      </button>
                      <span className="ap-badge-outline">{other.role || 'agent'}</span>
                      <span style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)', marginLeft: 'auto' }}>
                        {other.status || 'inactive'}
                      </span>
                    </div>
                  ))
                )}
              </div>
            </article>
          </div>
        )}

        {/* Tasks Tab */}
        {activeTab === 'tasks' && (
          <article className="ap-card">
            <div className="ap-card-body">
              {tasks.length === 0 ? (
                <p style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)', margin: 0 }}>No tasks assigned to this agent.</p>
              ) : (
                <table className="task-table">
                  <thead>
                    <tr>
                      <th>Objective</th>
                      <th>Status</th>
                      <th>Priority</th>
                      <th>Created</th>
                      <th>Confidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tasks.map(task => (
                      <tr key={task.id}>
                        <td style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {task.objective}
                        </td>
                        <td>
                          <Badge bg="none" style={{ fontSize: 'var(--ap-fs-xs)', backgroundColor: TASK_STATUS_COLORS[task.status] || TASK_STATUS_COLORS.queued }}>
                            {task.status}
                          </Badge>
                        </td>
                        <td>
                          <Badge bg="none" style={{ fontSize: 'var(--ap-fs-xs)', backgroundColor: PRIORITY_COLORS[task.priority] || PRIORITY_COLORS.low }}>
                            {task.priority || 'normal'}
                          </Badge>
                        </td>
                        <td style={{ color: 'var(--ap-text-muted)' }}>
                          {task.created_at ? new Date(task.created_at).toLocaleDateString() : '\u2014'}
                        </td>
                        <td style={{ color: 'var(--ap-text-muted)' }}>
                          {task.confidence != null ? `${Math.round(task.confidence * 100)}%` : '\u2014'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </article>
        )}

        {/* Config Tab */}
        {activeTab === 'config' && (
          <div>
            {(agent.config?.system_prompt || agent.system_prompt) && (
              <div className="mb-4">
                <div className="ap-section-label">System Prompt</div>
                <div className="config-block">
                  {agent.config?.system_prompt || agent.system_prompt}
                </div>
              </div>
            )}

            <div className="ap-section-label">Routing</div>
            <p style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)', marginTop: 0, marginBottom: 12 }}>
              The actual LLM and model are chosen by the tenant's routed CLI platform (Gemini CLI, Claude Code, or Codex). Agents don't bind to a specific model.
            </p>
            <Row className="g-3 mb-4">
              {[
                { label: 'Model Tier', value: agent.default_model_tier || 'full' },
                { label: 'Temperature', value: agent.config?.temperature ?? '—' },
                { label: 'Max Tokens', value: agent.config?.max_tokens ?? '—' },
                { label: 'Autonomy', value: agent.autonomy_level || 'supervised' },
              ].map(p => (
                <Col md={3} sm={6} key={p.label}>
                  <article className="ap-card h-100">
                    <div className="ap-card-body">
                      <div className="stat-label">{p.label}</div>
                      <div style={{ fontSize: 'var(--ap-fs-md)', fontWeight: 600, color: 'var(--ap-text)', marginTop: 4 }}>
                        {p.value}
                      </div>
                    </div>
                  </article>
                </Col>
              ))}
            </Row>

            <div className="ap-section-label">Raw Configuration</div>
            <div className="config-block">
              {JSON.stringify(
                Object.fromEntries(Object.entries(agent.config || {}).filter(([k]) => k !== 'model')),
                null,
                2
              )}
            </div>
          </div>
        )}

        {/* Performance Tab */}
        {activeTab === 'performance' && (
          <div>
            <div className="ap-chip-row mb-4">
              {['24h', '7d', '30d'].map(w => (
                <button
                  key={w}
                  type="button"
                  className={`ap-chip-filter ${perfWindow === w ? 'active' : ''}`}
                  onClick={() => handlePerfWindow(w)}
                >
                  {w}
                </button>
              ))}
            </div>
            {perfLoading ? (
              <div className="text-center py-4">
                <Spinner animation="border" size="sm" variant="primary" />
              </div>
            ) : !performanceData || performanceData.snapshot_count === 0 ? (
              <p style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)' }}>
                No performance data yet. Agent needs to be invoked first.
              </p>
            ) : (
              <Row className="g-3">
                {[
                  { label: 'Invocations', value: performanceData.invocation_count ?? '\u2014' },
                  { label: 'Success Rate', value: performanceData.success_rate != null ? `${Math.round(performanceData.success_rate * 100)}%` : '\u2014' },
                  { label: 'p50 Latency (ms)', value: performanceData.latency_p50_ms ?? '\u2014' },
                  { label: 'p95 Latency (ms)', value: performanceData.latency_p95_ms ?? '\u2014' },
                  { label: 'Avg Quality Score', value: performanceData.avg_quality_score != null ? performanceData.avg_quality_score.toFixed(1) : '\u2014' },
                  { label: 'Total Cost (USD)', value: performanceData.total_cost_usd != null ? `$${performanceData.total_cost_usd.toFixed(4)}` : '\u2014' },
                ].map(s => (
                  <Col md={4} sm={6} key={s.label}>
                    <article className="ap-card h-100">
                      <div className="ap-card-body">
                        <div className="stat-value">{s.value}</div>
                        <div className="stat-label">{s.label}</div>
                      </div>
                    </article>
                  </Col>
                ))}
              </Row>
            )}
          </div>
        )}

        {/* Audit Tab */}
        {activeTab === 'audit' && (
          <div>
            <div className="d-flex align-items-center gap-3 mb-4 flex-wrap">
              <div className="d-flex align-items-center gap-2">
                <label style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)', marginBottom: 0 }}>From</label>
                <input
                  type="datetime-local"
                  value={auditFromDt}
                  onChange={e => handleAuditDateChange(e.target.value, auditToDt)}
                  style={{ fontSize: 'var(--ap-fs-sm)', background: 'var(--ap-card-bg)', border: '1px solid var(--ap-border)', borderRadius: 'var(--ap-radius-sm)', padding: '4px 8px', color: 'var(--ap-text)' }}
                />
              </div>
              <div className="d-flex align-items-center gap-2">
                <label style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)', marginBottom: 0 }}>To</label>
                <input
                  type="datetime-local"
                  value={auditToDt}
                  onChange={e => handleAuditDateChange(auditFromDt, e.target.value)}
                  style={{ fontSize: 'var(--ap-fs-sm)', background: 'var(--ap-card-bg)', border: '1px solid var(--ap-border)', borderRadius: 'var(--ap-radius-sm)', padding: '4px 8px', color: 'var(--ap-text)' }}
                />
              </div>
              <a
                href={auditExportUrl()}
                download
                className="ap-btn-secondary ap-btn-sm"
                style={{ textDecoration: 'none' }}
              >
                Export CSV
              </a>
            </div>
            {auditLoading ? (
              <div className="text-center py-4">
                <Spinner animation="border" size="sm" variant="primary" />
              </div>
            ) : auditLogs.length === 0 ? (
              <p style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)' }}>No audit log entries yet.</p>
            ) : (
              <article className="ap-card">
                <div className="ap-card-body" style={{ overflowX: 'auto' }}>
                  <table className="task-table">
                    <thead>
                      <tr>
                        <th>Time</th>
                        <th>Type</th>
                        <th>Status</th>
                        <th>Latency (ms)</th>
                        <th>Cost ($)</th>
                        <th>Quality Score</th>
                        <th>Input</th>
                      </tr>
                    </thead>
                    <tbody>
                      {auditLogs.map((log, i) => (
                        <tr key={log.id || i}>
                          <td style={{ color: 'var(--ap-text-muted)', whiteSpace: 'nowrap' }}>
                            {log.created_at ? new Date(log.created_at).toLocaleString() : '\u2014'}
                          </td>
                          <td style={{ color: 'var(--ap-text-muted)' }}>{log.event_type || log.type || '\u2014'}</td>
                          <td>
                            <Badge bg="none" style={{ fontSize: 'var(--ap-fs-xs)', backgroundColor: AUDIT_STATUS_COLORS[log.status] || '#94a3b8' }}>
                              {log.status || '\u2014'}
                            </Badge>
                          </td>
                          <td style={{ color: 'var(--ap-text-muted)' }}>{log.latency_ms ?? '\u2014'}</td>
                          <td style={{ color: 'var(--ap-text-muted)' }}>{log.cost_usd != null ? `$${log.cost_usd.toFixed(4)}` : '\u2014'}</td>
                          <td style={{ color: 'var(--ap-text-muted)' }}>{log.quality_score != null ? log.quality_score.toFixed(1) : '\u2014'}</td>
                          <td style={{ color: 'var(--ap-text-muted)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {log.input ? String(log.input).slice(0, 100) : '\u2014'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </article>
            )}
          </div>
        )}

        {/* Versions Tab */}
        {activeTab === 'versions' && (
          <div>
            {versionsLoading ? (
              <div className="text-center py-4">
                <Spinner animation="border" size="sm" variant="primary" />
              </div>
            ) : versions.length === 0 ? (
              <p style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)' }}>No version history available.</p>
            ) : (
              <article className="ap-card">
                {versions.map((v, i) => (
                  <div
                    key={v.id || i}
                    style={{
                      padding: '14px 20px',
                      borderBottom: i < versions.length - 1 ? '1px solid var(--ap-border)' : 'none',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 12,
                    }}
                  >
                    <span style={{ fontWeight: 600, fontSize: 'var(--ap-fs-base)', color: 'var(--ap-text)', minWidth: 32 }}>
                      v{v.version}
                    </span>
                    {v.is_current ? (
                      <span className="ap-status ap-status-production">Current</span>
                    ) : (
                      <span className="ap-badge-outline">{v.status || 'archived'}</span>
                    )}
                    <span style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)' }}>
                      {v.promoted_by ? `by ${v.promoted_by}` : ''}
                    </span>
                    <span style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)' }}>
                      {v.promoted_at ? new Date(v.promoted_at).toLocaleDateString() : ''}
                    </span>
                    {v.notes && (
                      <span style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)', flex: 1 }}>{v.notes}</span>
                    )}
                    {!v.is_current && (
                      <button
                        type="button"
                        className="ap-btn-secondary ap-btn-sm"
                        onClick={() => handleRollback(v.version)}
                        style={{ marginLeft: 'auto' }}
                      >
                        Rollback to this version
                      </button>
                    )}
                  </div>
                ))}
              </article>
            )}
          </div>
        )}

        {/* Integrations Tab */}
        {activeTab === 'integrations' && (
          <div>
            {integrationsLoading ? (
              <div className="text-center py-4">
                <Spinner animation="border" size="sm" variant="primary" />
              </div>
            ) : allIntegrations.length === 0 ? (
              <p style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)' }}>
                No integrations configured for this tenant. Add integrations in the Integrations page.
              </p>
            ) : (
              <>
                {assignedIds.size === 0 && (
                  <p style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)', marginBottom: 16 }}>
                    Inherits all tenant integrations. Assign specific integrations to restrict access.
                  </p>
                )}
                <article className="ap-card">
                  {allIntegrations.map((intg, i) => {
                    const cfgId = intg.integration_config_id || intg.id;
                    const isAssigned = assignedIds.has(cfgId);
                    return (
                      <div
                        key={cfgId || i}
                        style={{
                          padding: '14px 20px',
                          borderBottom: i < allIntegrations.length - 1 ? '1px solid var(--ap-border)' : 'none',
                          display: 'flex',
                          alignItems: 'center',
                          gap: 12,
                        }}
                      >
                        <div style={{ flex: 1 }}>
                          <div style={{ fontSize: 'var(--ap-fs-base)', fontWeight: 500, color: 'var(--ap-text)' }}>
                            {intg.name || intg.integration_name || cfgId}
                          </div>
                          {intg.description && (
                            <div style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)' }}>{intg.description}</div>
                          )}
                        </div>
                        <div
                          onClick={() => handleIntegrationToggle(cfgId, isAssigned)}
                          role="switch"
                          aria-checked={isAssigned}
                          tabIndex={0}
                          className={`assign-toggle ${isAssigned ? 'on' : ''}`}
                        >
                          <span className="assign-toggle-knob" />
                        </div>
                      </div>
                    );
                  })}
                </article>
              </>
            )}
          </div>
        )}

        {/* Tests Tab — ALM Pillar 10 */}
        {activeTab === 'tests' && <TestsTabSection agentId={id} />}
      </div>

      {/* Delete Modal */}
      <Modal show={deleteConfirm} onHide={() => setDeleteConfirm(false)} centered size="sm">
        <Modal.Body className="text-center py-4">
          <p style={{ fontSize: 'var(--ap-fs-base)', fontWeight: 500, marginBottom: 8 }}>
            Delete "{agent.name}"?
          </p>
          <p style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)', marginBottom: 20 }}>
            This action cannot be undone.
          </p>
          <div className="d-flex justify-content-center gap-2">
            <button type="button" className="ap-btn-secondary ap-btn-sm" onClick={() => setDeleteConfirm(false)}>Cancel</button>
            <button type="button" className="ap-btn-danger ap-btn-sm" onClick={handleDelete} disabled={deleting}>
              {deleting ? 'Deleting...' : 'Delete'}
            </button>
          </div>
        </Modal.Body>
      </Modal>

      {/* Publish to Marketplace Modal (ALM Pillar 9) */}
      <Modal show={publishOpen} onHide={() => setPublishOpen(false)} centered>
        <Modal.Header>
          <Modal.Title style={{ fontSize: 'var(--ap-fs-md)', fontWeight: 600 }}>Publish "{agent.name}" to marketplace</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          <p style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)', marginBottom: 16 }}>
            Other tenants will be able to discover and subscribe to this agent. Only production agents can be published.
          </p>
          {publishError && (
            <div className="ap-card" style={{ padding: 12, marginBottom: 12, color: 'var(--ap-danger)' }}>
              {String(publishError)}
            </div>
          )}
          <div className="row g-2">
            <div className="col-md-6">
              <label style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>Protocol</label>
              <select className="form-select form-select-sm" value={publishForm.protocol} onChange={(e) => setPublishForm({ ...publishForm, protocol: e.target.value })}>
                <option value="openai_chat">OpenAI-compatible</option>
                <option value="mcp_sse">MCP SSE</option>
                <option value="webhook">Webhook</option>
                <option value="a2a">A2A</option>
              </select>
            </div>
            <div className="col-md-6">
              <label style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>Pricing</label>
              <select className="form-select form-select-sm" value={publishForm.pricing_model} onChange={(e) => setPublishForm({ ...publishForm, pricing_model: e.target.value })}>
                <option value="free">Free</option>
                <option value="per_call">Per-call</option>
                <option value="subscription">Subscription</option>
              </select>
            </div>
            <div className="col-12">
              <label style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>Endpoint URL *</label>
              <input className="form-control form-control-sm" required value={publishForm.endpoint_url} onChange={(e) => setPublishForm({ ...publishForm, endpoint_url: e.target.value })} placeholder="https://agentprovision.com/api/v1/agents/…/invoke" />
            </div>
            {publishForm.pricing_model !== 'free' && (
              <div className="col-md-6">
                <label style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>Price per call (USD)</label>
                <input type="number" step="0.01" className="form-control form-control-sm" value={publishForm.price_per_call_usd} onChange={(e) => setPublishForm({ ...publishForm, price_per_call_usd: e.target.value })} />
              </div>
            )}
          </div>
        </Modal.Body>
        <Modal.Footer>
          <button type="button" className="ap-btn-secondary ap-btn-sm" onClick={() => setPublishOpen(false)}>Cancel</button>
          <button
            type="button"
            className="ap-btn-primary ap-btn-sm"
            disabled={publishBusy || !publishForm.endpoint_url.trim()}
            onClick={async () => {
              setPublishBusy(true);
              setPublishError(null);
              try {
                await api.post('/marketplace/listings', {
                  agent_id: id,
                  protocol: publishForm.protocol,
                  endpoint_url: publishForm.endpoint_url || null,
                  pricing_model: publishForm.pricing_model,
                  price_per_call_usd: publishForm.price_per_call_usd ? Number(publishForm.price_per_call_usd) : null,
                  public: true,
                });
                setPublishOpen(false);
              } catch (e) {
                setPublishError(e?.response?.data?.detail || 'Publish failed');
              } finally {
                setPublishBusy(false);
              }
            }}
          >
            {publishBusy ? 'Publishing…' : 'Publish'}
          </button>
        </Modal.Footer>
      </Modal>
    </Layout>
  );
};

export default AgentDetailPage;
