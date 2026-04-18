import { useEffect, useState } from 'react';
import { Badge, Button, Col, Modal, Nav, Row, Spinner } from 'react-bootstrap';
import { useNavigate, useParams } from 'react-router-dom';
import Layout from '../components/Layout';
import agentService from '../services/agent';
import './AgentDetailPage.css';

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
          <p style={{ color: 'var(--color-muted)' }}>Agent not found.</p>
          <Button variant="outline-secondary" size="sm" onClick={() => navigate('/agents')}>Back to Fleet</Button>
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
            onClick={() => navigate('/agents')}
            style={{ background: 'none', border: 'none', color: 'var(--color-muted)', cursor: 'pointer', fontSize: '0.82rem', padding: 0, marginBottom: 12 }}
          >
            &larr; Back to Agent Fleet
          </button>

          <div className="d-flex justify-content-between align-items-start">
            <div>
              <div className="d-flex align-items-center gap-2 mb-1">
                <span style={{ width: 10, height: 10, borderRadius: '50%', background: STATUS_COLORS[status] || '#94a3b8' }} />
                <h4 style={{ fontWeight: 600, margin: 0, color: 'var(--color-foreground)' }}>{agent.name}</h4>
              </div>
              <p style={{ fontSize: '0.85rem', color: 'var(--color-muted)', margin: '4px 0 8px 0' }}>
                {agent.description || 'No description'}
              </p>
              <div className="d-flex gap-2 flex-wrap">
                <Badge bg="none" style={{ fontSize: '0.7rem', backgroundColor: 'var(--surface-contrast, rgba(255,255,255,0.06))', color: 'var(--color-muted)' }}>
                  {agent.config?.model || 'gpt-4'}
                </Badge>
                {agent.role && (
                  <Badge bg="none" style={{ fontSize: '0.7rem', backgroundColor: ROLE_COLORS[agent.role] || '#6c757d' }}>
                    {agent.role}
                  </Badge>
                )}
                <Badge bg="none" style={{ fontSize: '0.7rem', backgroundColor: 'rgba(255,255,255,0.1)', color: 'var(--color-muted)' }}>
                  {agent.autonomy_level || 'supervised'}
                </Badge>
              </div>
            </div>
            <div className="d-flex gap-2">
              <Button variant="outline-danger" size="sm" onClick={() => setDeleteConfirm(true)} style={{ fontSize: '0.78rem' }}>
                Delete
              </Button>
            </div>
          </div>
        </div>

        {/* Tabs */}
        <Nav className="tab-nav" as="ul">
          {['overview', 'relations', 'tasks', 'config', 'performance', 'audit', 'versions', 'integrations'].map(tab => (
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
                  <div className="stat-tile">
                    <div className="stat-value">{s.value}</div>
                    <div className="stat-label">{s.label}</div>
                  </div>
                </Col>
              ))}
            </Row>

            <div style={{ fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--color-muted)', marginBottom: 12 }}>
              Skills ({allSkills.length})
            </div>
            {allSkills.length === 0 ? (
              <p style={{ fontSize: '0.82rem', color: 'var(--color-muted)' }}>No skills configured.</p>
            ) : (
              <Row className="g-2 mb-4">
                {allSkills.map(skill => (
                  <Col md={6} lg={4} key={skill.skill_name}>
                    <div className="skill-card">
                      <div className="d-flex justify-content-between align-items-center mb-1">
                        <span style={{ fontSize: '0.85rem', fontWeight: 500, color: 'var(--color-foreground)' }}>
                          {skill.skill_name.replace(/_/g, ' ')}
                        </span>
                        {skill.learned_from && (
                          <span style={{ fontSize: '0.65rem', padding: '1px 5px', borderRadius: 3, background: 'rgba(255,255,255,0.06)', color: 'var(--color-muted)' }}>
                            {skill.learned_from}
                          </span>
                        )}
                      </div>
                      {skill.proficiency !== null && skill.proficiency !== undefined && (
                        <div className="d-flex align-items-center gap-2 mb-1">
                          <div className="proficiency-bar" style={{ flex: 1 }}>
                            <div className="fill" style={{ width: `${Math.round(skill.proficiency * 100)}%` }} />
                          </div>
                          <span style={{ fontSize: '0.68rem', color: 'var(--color-muted)', minWidth: 28 }}>
                            {Math.round(skill.proficiency * 100)}%
                          </span>
                        </div>
                      )}
                      <div className="d-flex gap-3" style={{ fontSize: '0.68rem', color: 'var(--color-muted)' }}>
                        <span>Used {skill.times_used || 0}x</span>
                        {skill.success_rate > 0 && <span>Success {Math.round(skill.success_rate * 100)}%</span>}
                      </div>
                    </div>
                  </Col>
                ))}
              </Row>
            )}
          </div>
        )}

        {/* Relations Tab */}
        {activeTab === 'relations' && (
          <div>
            <div style={{ background: 'var(--surface-elevated)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '20px 24px' }}>
              {agents.filter(a => a.id !== agent.id).length === 0 ? (
                <p style={{ fontSize: '0.82rem', color: 'var(--color-muted)', margin: 0 }}>No other agents in the fleet.</p>
              ) : (
                agents.filter(a => a.id !== agent.id).map(other => (
                  <div key={other.id} className="relation-row">
                    <span style={{ color: 'var(--color-muted)' }}>&harr;</span>
                    <span
                      style={{ color: '#4dabf7', cursor: 'pointer', fontWeight: 500 }}
                      onClick={() => navigate(`/agents/${other.id}`)}
                    >
                      {other.name}
                    </span>
                    <Badge bg="none" style={{ fontSize: '0.65rem', backgroundColor: 'rgba(255,255,255,0.08)', color: 'var(--color-muted)' }}>
                      {other.role || 'agent'}
                    </Badge>
                    <span style={{ fontSize: '0.72rem', color: 'var(--color-muted)', marginLeft: 'auto' }}>
                      {other.status || 'inactive'}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        )}

        {/* Tasks Tab */}
        {activeTab === 'tasks' && (
          <div style={{ background: 'var(--surface-elevated)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '20px 24px' }}>
            {tasks.length === 0 ? (
              <p style={{ fontSize: '0.82rem', color: 'var(--color-muted)', margin: 0 }}>No tasks assigned to this agent.</p>
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
                        <Badge bg="none" style={{ fontSize: '0.65rem', backgroundColor: TASK_STATUS_COLORS[task.status] || '#94a3b8' }}>
                          {task.status}
                        </Badge>
                      </td>
                      <td>
                        <Badge bg="none" style={{ fontSize: '0.65rem', backgroundColor: PRIORITY_COLORS[task.priority] || '#94a3b8' }}>
                          {task.priority || 'normal'}
                        </Badge>
                      </td>
                      <td style={{ color: 'var(--color-muted)' }}>
                        {task.created_at ? new Date(task.created_at).toLocaleDateString() : '\u2014'}
                      </td>
                      <td style={{ color: 'var(--color-muted)' }}>
                        {task.confidence != null ? `${Math.round(task.confidence * 100)}%` : '\u2014'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {/* Config Tab */}
        {activeTab === 'config' && (
          <div>
            {(agent.config?.system_prompt || agent.system_prompt) && (
              <div className="mb-4">
                <div style={{ fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--color-muted)', marginBottom: 8 }}>
                  System Prompt
                </div>
                <div className="config-block">
                  {agent.config?.system_prompt || agent.system_prompt}
                </div>
              </div>
            )}

            <div style={{ fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--color-muted)', marginBottom: 8 }}>
              Parameters
            </div>
            <Row className="g-3 mb-4">
              {[
                { label: 'Model', value: agent.config?.model || 'gpt-4' },
                { label: 'Temperature', value: agent.config?.temperature ?? 0.7 },
                { label: 'Max Tokens', value: agent.config?.max_tokens ?? 2000 },
                { label: 'Autonomy', value: agent.autonomy_level || 'supervised' },
              ].map(p => (
                <Col md={3} sm={6} key={p.label}>
                  <div className="stat-tile">
                    <div className="stat-label">{p.label}</div>
                    <div style={{ fontSize: '1rem', fontWeight: 600, color: 'var(--color-foreground)', marginTop: 4 }}>
                      {p.value}
                    </div>
                  </div>
                </Col>
              ))}
            </Row>

            <div style={{ fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--color-muted)', marginBottom: 8 }}>
              Raw Configuration
            </div>
            <div className="config-block">
              {JSON.stringify(agent.config || {}, null, 2)}
            </div>
          </div>
        )}

        {/* Performance Tab */}
        {activeTab === 'performance' && (
          <div>
            <div className="d-flex align-items-center gap-2 mb-4">
              {['24h', '7d', '30d'].map(w => (
                <Button
                  key={w}
                  size="sm"
                  variant={perfWindow === w ? 'primary' : 'outline-secondary'}
                  onClick={() => handlePerfWindow(w)}
                  style={{ fontSize: '0.78rem' }}
                >
                  {w}
                </Button>
              ))}
            </div>
            {perfLoading ? (
              <div className="text-center py-4">
                <Spinner animation="border" size="sm" variant="primary" />
              </div>
            ) : !performanceData || performanceData.snapshot_count === 0 ? (
              <p style={{ fontSize: '0.82rem', color: 'var(--color-muted)' }}>
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
                    <div className="stat-tile">
                      <div className="stat-value">{s.value}</div>
                      <div className="stat-label">{s.label}</div>
                    </div>
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
                <label style={{ fontSize: '0.78rem', color: 'var(--color-muted)', marginBottom: 0 }}>From</label>
                <input
                  type="datetime-local"
                  value={auditFromDt}
                  onChange={e => handleAuditDateChange(e.target.value, auditToDt)}
                  style={{ fontSize: '0.78rem', background: 'var(--surface-elevated)', border: '1px solid var(--color-border)', borderRadius: 4, padding: '4px 8px', color: 'var(--color-foreground)' }}
                />
              </div>
              <div className="d-flex align-items-center gap-2">
                <label style={{ fontSize: '0.78rem', color: 'var(--color-muted)', marginBottom: 0 }}>To</label>
                <input
                  type="datetime-local"
                  value={auditToDt}
                  onChange={e => handleAuditDateChange(auditFromDt, e.target.value)}
                  style={{ fontSize: '0.78rem', background: 'var(--surface-elevated)', border: '1px solid var(--color-border)', borderRadius: 4, padding: '4px 8px', color: 'var(--color-foreground)' }}
                />
              </div>
              <a
                href={auditExportUrl()}
                download
                style={{ fontSize: '0.78rem', color: '#4dabf7', textDecoration: 'none', padding: '5px 12px', border: '1px solid #4dabf7', borderRadius: 4 }}
              >
                Export CSV
              </a>
            </div>
            {auditLoading ? (
              <div className="text-center py-4">
                <Spinner animation="border" size="sm" variant="primary" />
              </div>
            ) : auditLogs.length === 0 ? (
              <p style={{ fontSize: '0.82rem', color: 'var(--color-muted)' }}>No audit log entries yet.</p>
            ) : (
              <div style={{ overflowX: 'auto' }}>
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
                        <td style={{ color: 'var(--color-muted)', whiteSpace: 'nowrap' }}>
                          {log.created_at ? new Date(log.created_at).toLocaleString() : '\u2014'}
                        </td>
                        <td style={{ color: 'var(--color-muted)' }}>{log.event_type || log.type || '\u2014'}</td>
                        <td>
                          <Badge bg="none" style={{ fontSize: '0.65rem', backgroundColor: AUDIT_STATUS_COLORS[log.status] || '#94a3b8' }}>
                            {log.status || '\u2014'}
                          </Badge>
                        </td>
                        <td style={{ color: 'var(--color-muted)' }}>{log.latency_ms ?? '\u2014'}</td>
                        <td style={{ color: 'var(--color-muted)' }}>{log.cost_usd != null ? `$${log.cost_usd.toFixed(4)}` : '\u2014'}</td>
                        <td style={{ color: 'var(--color-muted)' }}>{log.quality_score != null ? log.quality_score.toFixed(1) : '\u2014'}</td>
                        <td style={{ color: 'var(--color-muted)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {log.input ? String(log.input).slice(0, 100) : '\u2014'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
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
              <p style={{ fontSize: '0.82rem', color: 'var(--color-muted)' }}>No version history available.</p>
            ) : (
              <div style={{ background: 'var(--surface-elevated)', border: '1px solid var(--color-border)', borderRadius: 8 }}>
                {versions.map((v, i) => (
                  <div
                    key={v.id || i}
                    style={{
                      padding: '14px 20px',
                      borderBottom: i < versions.length - 1 ? '1px solid var(--color-border)' : 'none',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 12,
                    }}
                  >
                    <span style={{ fontWeight: 600, fontSize: '0.88rem', color: 'var(--color-foreground)', minWidth: 32 }}>
                      v{v.version}
                    </span>
                    {v.is_current ? (
                      <Badge bg="none" style={{ fontSize: '0.65rem', backgroundColor: '#22c55e' }}>Current</Badge>
                    ) : (
                      <Badge bg="none" style={{ fontSize: '0.65rem', backgroundColor: 'rgba(255,255,255,0.08)', color: 'var(--color-muted)' }}>
                        {v.status || 'archived'}
                      </Badge>
                    )}
                    <span style={{ fontSize: '0.78rem', color: 'var(--color-muted)' }}>
                      {v.promoted_by ? `by ${v.promoted_by}` : ''}
                    </span>
                    <span style={{ fontSize: '0.78rem', color: 'var(--color-muted)' }}>
                      {v.promoted_at ? new Date(v.promoted_at).toLocaleDateString() : ''}
                    </span>
                    {v.notes && (
                      <span style={{ fontSize: '0.78rem', color: 'var(--color-muted)', flex: 1 }}>{v.notes}</span>
                    )}
                    {!v.is_current && (
                      <Button
                        size="sm"
                        variant="outline-secondary"
                        onClick={() => handleRollback(v.version)}
                        style={{ fontSize: '0.72rem', marginLeft: 'auto' }}
                      >
                        Rollback to this version
                      </Button>
                    )}
                  </div>
                ))}
              </div>
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
              <p style={{ fontSize: '0.82rem', color: 'var(--color-muted)' }}>
                No integrations configured for this tenant. Add integrations in the Integrations page.
              </p>
            ) : (
              <>
                {assignedIds.size === 0 && (
                  <p style={{ fontSize: '0.82rem', color: 'var(--color-muted)', marginBottom: 16 }}>
                    Inherits all tenant integrations. Assign specific integrations to restrict access.
                  </p>
                )}
                <div style={{ background: 'var(--surface-elevated)', border: '1px solid var(--color-border)', borderRadius: 8 }}>
                  {allIntegrations.map((intg, i) => {
                    const cfgId = intg.integration_config_id || intg.id;
                    const isAssigned = assignedIds.has(cfgId);
                    return (
                      <div
                        key={cfgId || i}
                        style={{
                          padding: '14px 20px',
                          borderBottom: i < allIntegrations.length - 1 ? '1px solid var(--color-border)' : 'none',
                          display: 'flex',
                          alignItems: 'center',
                          gap: 12,
                        }}
                      >
                        <div style={{ flex: 1 }}>
                          <div style={{ fontSize: '0.88rem', fontWeight: 500, color: 'var(--color-foreground)' }}>
                            {intg.name || intg.integration_name || cfgId}
                          </div>
                          {intg.description && (
                            <div style={{ fontSize: '0.75rem', color: 'var(--color-muted)' }}>{intg.description}</div>
                          )}
                        </div>
                        <div
                          onClick={() => handleIntegrationToggle(cfgId, isAssigned)}
                          style={{
                            width: 40,
                            height: 22,
                            borderRadius: 11,
                            background: isAssigned ? '#22c55e' : 'rgba(255,255,255,0.12)',
                            cursor: 'pointer',
                            position: 'relative',
                            transition: 'background 0.2s',
                          }}
                        >
                          <span
                            style={{
                              position: 'absolute',
                              top: 3,
                              left: isAssigned ? 21 : 3,
                              width: 16,
                              height: 16,
                              borderRadius: '50%',
                              background: '#fff',
                              transition: 'left 0.2s',
                            }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {/* Delete Modal */}
      <Modal show={deleteConfirm} onHide={() => setDeleteConfirm(false)} centered size="sm">
        <Modal.Body className="text-center py-4">
          <p style={{ fontSize: '0.88rem', fontWeight: 500, marginBottom: 8 }}>
            Delete "{agent.name}"?
          </p>
          <p style={{ fontSize: '0.78rem', color: 'var(--color-muted)', marginBottom: 20 }}>
            This action cannot be undone.
          </p>
          <div className="d-flex justify-content-center gap-2">
            <Button variant="outline-secondary" size="sm" onClick={() => setDeleteConfirm(false)}>Cancel</Button>
            <Button variant="danger" size="sm" onClick={handleDelete} disabled={deleting}>
              {deleting ? 'Deleting...' : 'Delete'}
            </Button>
          </div>
        </Modal.Body>
      </Modal>
    </Layout>
  );
};

export default AgentDetailPage;
