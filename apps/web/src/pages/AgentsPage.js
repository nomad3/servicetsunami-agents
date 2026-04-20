import { useEffect, useState, useMemo } from 'react';
import { Alert, Badge, Button, Col, Form, Modal, Row, Spinner } from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import Layout from '../components/Layout';
import agentService from '../services/agent';
import api from '../services/api';

const AgentsPage = () => {
  const { t } = useTranslation('agents');
  const navigate = useNavigate();
  const [agents, setAgents] = useState([]);
  const [externalAgents, setExternalAgents] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [lifecycleFilter, setLifecycleFilter] = useState('All');
  const [deleteConfirm, setDeleteConfirm] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [importContent, setImportContent] = useState('');
  const [importing, setImporting] = useState(false);
  const [hireModalOpen, setHireModalOpen] = useState(false);
  const [hireForm, setHireForm] = useState({ name: '', description: '', endpoint_url: '', protocol: 'openai_chat', auth_type: 'bearer', credential_id: '', capabilities: '' });
  const [hiring, setHiring] = useState(false);

  const loadAgents = () =>
    agentService.getAll().then(r => setAgents(r.data || []));

  useEffect(() => {
    Promise.all([
      loadAgents(),
      api.get('/external-agents').then(r => setExternalAgents(r.data || [])).catch(() => {}),
      agentService.getTasks().then(r => setTasks(r.data || [])).catch(() => {}),
    ])
      .catch(err => { console.error(err); setError(t('errors.load')); })
      .finally(() => setLoading(false));
  }, [t]);

  const tasksByAgent = useMemo(() => {
    const map = {};
    tasks.forEach(task => {
      const aid = task.assigned_agent_id;
      if (!aid) return;
      if (!map[aid]) map[aid] = { active: 0, completed: 0, total: 0 };
      map[aid].total++;
      if (task.status === 'completed') map[aid].completed++;
      else if (['queued', 'thinking', 'executing'].includes(task.status)) map[aid].active++;
    });
    return map;
  }, [tasks]);

  const LIFECYCLE_STATUSES = ['All', 'Production', 'Staging', 'Draft', 'Deprecated'];

  const filteredAgents = agents.filter(a => {
    const matchesSearch = a.name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      a.description?.toLowerCase().includes(searchTerm.toLowerCase());
    const agentLifecycle = a.status || 'draft';
    const matchesFilter = lifecycleFilter === 'All' || agentLifecycle.toLowerCase() === lifecycleFilter.toLowerCase();
    return matchesSearch && matchesFilter;
  });

  const handleDelete = async (agent) => {
    try {
      setSubmitting(true);
      await agentService.delete(agent.id);
      setDeleteConfirm(null);
      setSuccess(t('success.deleted', { name: agent.name }));
      setAgents(prev => prev.filter(a => a.id !== agent.id));
      setTimeout(() => setSuccess(''), 3000);
    } catch (err) {
      console.error(err);
      setError(t('errors.delete'));
    } finally {
      setSubmitting(false);
    }
  };

  const handlePromote = async (e, agent) => {
    e.stopPropagation();
    try {
      await api.post(`/agents/${agent.id}/promote`);
      await loadAgents();
      setSuccess(`${agent.name} promoted.`);
      setTimeout(() => setSuccess(''), 3000);
    } catch (err) {
      console.error(err);
      setError('Failed to promote agent.');
    }
  };

  const handleDeprecate = async (e, agent) => {
    e.stopPropagation();
    try {
      await api.post(`/agents/${agent.id}/deprecate`);
      await loadAgents();
      setSuccess(`${agent.name} deprecated.`);
      setTimeout(() => setSuccess(''), 3000);
    } catch (err) {
      console.error(err);
      setError('Failed to deprecate agent.');
    }
  };

  const handleHealthCheck = async (e, extAgent) => {
    e.stopPropagation();
    try {
      await api.post(`/external-agents/${extAgent.id}/health-check`);
      const r = await api.get('/external-agents');
      setExternalAgents(r.data || []);
      setSuccess(`Health check sent for ${extAgent.name}.`);
      setTimeout(() => setSuccess(''), 3000);
    } catch (err) {
      console.error(err);
      setError('Health check failed.');
    }
  };

  const handleFireExternal = async (e, extAgent) => {
    e.stopPropagation();
    if (!window.confirm(`Remove external agent "${extAgent.name}"? This cannot be undone.`)) return;
    try {
      await api.delete(`/external-agents/${extAgent.id}`);
      setExternalAgents(prev => prev.filter(a => a.id !== extAgent.id));
      setSuccess(`${extAgent.name} removed.`);
      setTimeout(() => setSuccess(''), 3000);
    } catch (err) {
      console.error(err);
      setError('Failed to remove external agent.');
    }
  };

  const handleImport = async () => {
    if (!importContent.trim()) return;
    try {
      setImporting(true);
      await api.post('/agents/import', { content: importContent });
      await loadAgents();
      setImportModalOpen(false);
      setImportContent('');
      setSuccess('Agent imported successfully.');
      setTimeout(() => setSuccess(''), 3000);
    } catch (err) {
      console.error(err);
      setError('Import failed. Check your YAML/JSON format.');
    } finally {
      setImporting(false);
    }
  };

  const resetHireForm = () =>
    setHireForm({ name: '', description: '', endpoint_url: '', protocol: 'openai_chat', auth_type: 'bearer', credential_id: '', capabilities: '' });

  const closeHireModal = () => {
    setHireModalOpen(false);
    resetHireForm();
  };

  const closeImportModal = () => {
    setImportModalOpen(false);
    setImportContent('');
  };

  const handleHire = async () => {
    if (!hireForm.name.trim() || !hireForm.endpoint_url.trim()) return;
    try {
      setHiring(true);
      const rawCaps = hireForm.capabilities
        ? hireForm.capabilities.split(',').map(s => s.trim()).filter(Boolean)
        : [];
      const payload = {
        name: hireForm.name.trim(),
        description: hireForm.description.trim() || undefined,
        endpoint_url: hireForm.endpoint_url.trim(),
        protocol: hireForm.protocol,
        auth_type: hireForm.auth_type,
        capabilities: [...new Set(rawCaps)],
      };
      if (hireForm.credential_id && hireForm.credential_id.trim()) {
        payload.credential_id = hireForm.credential_id.trim();
      }
      await api.post('/external-agents', payload);
      const r = await api.get('/external-agents');
      setExternalAgents(r.data || []);
      setHireModalOpen(false);
      resetHireForm();
      setSuccess('External agent hired successfully.');
      setTimeout(() => setSuccess(''), 3000);
    } catch (err) {
      console.error(err);
      const detail = err.response?.data?.detail || 'Hire failed. Check the endpoint URL.';
      setError(typeof detail === 'string' ? detail : JSON.stringify(detail));
    } finally {
      setHiring(false);
    }
  };

  const getSkills = (agent) => {
    const configSkills = agent.config?.skills || agent.config?.tools || [];
    const agentSkills = (agent.skills || []).map(s => s.skill_name);
    return [...new Set([...configSkills, ...agentSkills])];
  };

  const statusColor = (s) => s === 'active' ? '#22c55e' : s === 'error' ? '#ef4444' : '#94a3b8';

  const externalStatusDot = (s) => {
    if (s === 'online') return '#22c55e';
    if (s === 'busy') return '#f59e0b';
    if (s === 'error') return '#ef4444';
    return '#94a3b8';
  };

  const lifecycleBadge = (ls) => {
    const status = (ls || 'draft').toLowerCase();
    if (status === 'production') return { bg: '#166534', color: '#86efac', label: 'Production' };
    if (status === 'staging') return { bg: '#78350f', color: '#fde68a', label: 'Staging' };
    if (status === 'deprecated') return { bg: '#7f1d1d', color: '#fca5a5', label: 'Deprecated' };
    return { bg: 'rgba(255,255,255,0.08)', color: '#94a3b8', label: 'Draft' };
  };

  const ROLE_COLORS = { analyst: '#6f42c1', manager: '#0d6efd', specialist: '#fd7e14' };
  const AUTONOMY_LABELS = { full: 'Full Auto', supervised: 'Supervised', approval_required: 'Approval Req.' };

  const cardStyle = {
    background: 'var(--surface-elevated)',
    border: '1px solid var(--color-border)',
    borderRadius: 8,
    padding: '20px 24px',
    cursor: 'pointer',
    transition: 'transform 0.15s ease, box-shadow 0.15s ease',
  };

  const sectionHeadingStyle = {
    fontSize: '0.75rem',
    fontWeight: 600,
    letterSpacing: '0.06em',
    textTransform: 'uppercase',
    color: 'var(--color-muted)',
    marginBottom: 12,
    marginTop: 28,
  };

  return (
    <Layout>
      <div style={{ maxWidth: 1100 }}>
        {/* Header */}
        <div className="d-flex justify-content-between align-items-start mb-4">
          <div>
            <h4 style={{ fontWeight: 600, marginBottom: 4, color: 'var(--color-foreground)' }}>
              {t('title')}
            </h4>
            <p style={{ fontSize: '0.85rem', color: 'var(--color-muted)', margin: 0 }}>
              {agents.length} agents · {externalAgents.length} external
            </p>
          </div>
          <div className="d-flex gap-2 align-items-center">
            <Button
              variant="outline-secondary"
              size="sm"
              onClick={() => setImportModalOpen(true)}
              style={{ fontSize: '0.78rem' }}
            >
              + Import Agent
            </Button>
            <Button
              variant="outline-secondary"
              size="sm"
              onClick={() => setHireModalOpen(true)}
              style={{ fontSize: '0.82rem' }}
            >
              + Hire External Agent
            </Button>
            <Button
              variant="outline-secondary"
              size="sm"
              onClick={() => navigate('/agents/wizard')}
              style={{ fontSize: '0.82rem' }}
            >
              + {t('agentWizard')}
            </Button>
          </div>
        </div>

        {error && <Alert variant="danger" dismissible onClose={() => setError('')} style={{ fontSize: '0.82rem' }}>{error}</Alert>}
        {success && <Alert variant="success" dismissible onClose={() => setSuccess('')} style={{ fontSize: '0.82rem' }}>{success}</Alert>}

        {loading ? (
          <div className="text-center py-5">
            <Spinner animation="border" size="sm" variant="primary" />
            <p className="mt-2 text-muted" style={{ fontSize: '0.82rem' }}>{t('loading')}</p>
          </div>
        ) : (
          <>
            {/* ── Agents ── */}
            <p style={{ ...sectionHeadingStyle, marginTop: 0 }}>Agents</p>

            {/* Search + lifecycle filter */}
            <div className="d-flex gap-2 align-items-center mb-3 flex-wrap">
              <Form.Control
                type="text"
                size="sm"
                placeholder={t('searchPlaceholder')}
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                style={{ maxWidth: 260, fontSize: '0.82rem' }}
              />
              <div className="d-flex gap-1">
                {LIFECYCLE_STATUSES.map(status => (
                  <button
                    key={status}
                    onClick={() => setLifecycleFilter(status)}
                    style={{
                      background: lifecycleFilter === status ? 'rgba(255,255,255,0.12)' : 'transparent',
                      border: '1px solid var(--color-border)',
                      borderRadius: 4,
                      padding: '3px 10px',
                      fontSize: '0.72rem',
                      color: lifecycleFilter === status ? 'var(--color-foreground)' : 'var(--color-muted)',
                      cursor: 'pointer',
                    }}
                  >
                    {status}
                  </button>
                ))}
              </div>
            </div>

            {filteredAgents.length === 0 ? (
              <div style={{ ...cardStyle, textAlign: 'center', padding: '48px 24px', cursor: 'default' }}>
                <p style={{ fontSize: '0.88rem', color: 'var(--color-foreground)', fontWeight: 500, marginBottom: 4 }}>
                  {searchTerm || lifecycleFilter !== 'All' ? t('noAgentsMatch') : t('noAgentsYet')}
                </p>
                <p style={{ fontSize: '0.78rem', color: 'var(--color-muted)', marginBottom: 16 }}>
                  {searchTerm || lifecycleFilter !== 'All' ? t('tryDifferent') : t('createFirst')}
                </p>
                {!searchTerm && lifecycleFilter === 'All' && (
                  <Button variant="primary" size="sm" onClick={() => navigate('/agents/wizard')}>
                    {t('createAgent')}
                  </Button>
                )}
              </div>
            ) : (
              <Row className="g-3">
                {filteredAgents.map((agent) => {
                  const skills = getSkills(agent);
                  const stats = tasksByAgent[agent.id] || { active: 0, completed: 0, total: 0 };
                  const successRate = stats.total > 0 ? Math.round((stats.completed / stats.total) * 100) : 0;
                  const ls = agent.status || 'draft';
                  const lsBadge = lifecycleBadge(ls);
                  const isDeprecated = ls.toLowerCase() === 'deprecated';
                  const canPromote = ['draft', 'staging'].includes(ls.toLowerCase());
                  const canDeprecate = ls.toLowerCase() === 'production';

                  return (
                    <Col key={agent.id} md={6} xl={4}>
                      <div
                        style={cardStyle}
                        onClick={() => navigate(`/agents/${agent.id}`)}
                        onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-2px)'; e.currentTarget.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)'; }}
                        onMouseLeave={e => { e.currentTarget.style.transform = 'none'; e.currentTarget.style.boxShadow = 'none'; }}
                      >
                        <div className="d-flex align-items-center justify-content-between mb-2">
                          <div className="d-flex align-items-center gap-2">
                            <span style={{ width: 8, height: 8, borderRadius: '50%', background: statusColor(agent.status), flexShrink: 0 }} />
                            <span style={{
                              fontSize: '0.95rem', fontWeight: 600, color: 'var(--color-foreground)',
                              textDecoration: isDeprecated ? 'line-through' : 'none',
                            }}>
                              {agent.name}
                            </span>
                          </div>
                          <div className="d-flex align-items-center gap-1">
                            <span style={{
                              fontSize: '0.63rem', padding: '2px 7px', borderRadius: 4,
                              background: lsBadge.bg, color: lsBadge.color, fontWeight: 600,
                            }}>
                              {lsBadge.label}
                            </span>
                            <span style={{
                              fontSize: '0.68rem', padding: '2px 8px', borderRadius: 4,
                              background: 'var(--surface-contrast, rgba(255,255,255,0.06))',
                              color: 'var(--color-muted)', fontWeight: 500,
                            }} title="Model tier — actual model is selected by the tenant's routed CLI platform">
                              {agent.default_model_tier || 'full'} tier
                            </span>
                          </div>
                        </div>

                        <p style={{
                          fontSize: '0.78rem', color: 'var(--color-muted)', margin: '0 0 10px 0',
                          display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
                        }}>
                          {agent.description || 'No description'}
                        </p>

                        <div className="d-flex gap-1 mb-2 flex-wrap">
                          {agent.role && (
                            <Badge bg="none" style={{ fontSize: '0.65rem', backgroundColor: ROLE_COLORS[agent.role] || '#6c757d' }}>
                              {agent.role}
                            </Badge>
                          )}
                          <Badge bg="none" style={{ fontSize: '0.65rem', backgroundColor: 'rgba(255,255,255,0.1)', color: 'var(--color-muted)' }}>
                            {AUTONOMY_LABELS[agent.autonomy_level] || agent.autonomy_level || 'supervised'}
                          </Badge>
                          <span style={{ fontSize: '0.65rem', color: 'var(--color-muted)', marginLeft: 'auto' }}>
                            {agent.owner_user_id ? 'Owned' : 'Unowned'}
                          </span>
                        </div>

                        {skills.length > 0 && (
                          <div className="d-flex gap-1 mb-2 flex-wrap">
                            {skills.slice(0, 4).map(s => (
                              <span key={s} style={{
                                fontSize: '0.65rem', padding: '1px 6px', borderRadius: 3,
                                background: 'rgba(77,171,247,0.12)', color: '#4dabf7',
                              }}>
                                {s.replace(/_/g, ' ')}
                              </span>
                            ))}
                            {skills.length > 4 && (
                              <span style={{ fontSize: '0.65rem', color: 'var(--color-muted)' }}>
                                +{skills.length - 4} more
                              </span>
                            )}
                          </div>
                        )}

                        <div className="d-flex align-items-center gap-3" style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>
                          <span>{stats.active} active</span>
                          <span>{stats.completed} completed</span>
                          {stats.total > 0 && (
                            <div className="d-flex align-items-center gap-1">
                              <div style={{ width: 40, height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.1)' }}>
                                <div style={{ width: `${successRate}%`, height: '100%', borderRadius: 2, background: '#22c55e' }} />
                              </div>
                              <span>{successRate}%</span>
                            </div>
                          )}
                        </div>

                        <div className="d-flex justify-content-end gap-1 mt-2">
                          {canPromote && (
                            <button
                              onClick={(e) => handlePromote(e, agent)}
                              style={{
                                background: 'none', border: '1px solid var(--color-border)',
                                borderRadius: 4, padding: '2px 8px', fontSize: '0.68rem',
                                color: '#4dabf7', cursor: 'pointer',
                              }}
                            >
                              Promote
                            </button>
                          )}
                          {canDeprecate && (
                            <button
                              onClick={(e) => handleDeprecate(e, agent)}
                              style={{
                                background: 'none', border: '1px solid var(--color-border)',
                                borderRadius: 4, padding: '2px 8px', fontSize: '0.68rem',
                                color: '#f59e0b', cursor: 'pointer',
                              }}
                            >
                              Deprecate
                            </button>
                          )}
                          <button
                            onClick={(e) => { e.stopPropagation(); setDeleteConfirm(agent); }}
                            style={{
                              background: 'none', border: '1px solid var(--color-border)',
                              borderRadius: 4, padding: '2px 8px', fontSize: '0.68rem',
                              color: '#ef4444', cursor: 'pointer',
                            }}
                          >
                            Delete
                          </button>
                        </div>
                      </div>
                    </Col>
                  );
                })}
              </Row>
            )}

            {/* ── Section 3: External Agents ── */}
            <p style={sectionHeadingStyle}>External Agents</p>
            {externalAgents.length === 0 ? (
              <div style={{ ...cardStyle, textAlign: 'center', padding: '32px 24px', cursor: 'default' }}>
                <p style={{ fontSize: '0.85rem', color: 'var(--color-muted)', margin: 0 }}>
                  No external agents hired yet.{' '}
                  <button
                    type="button"
                    onClick={() => setHireModalOpen(true)}
                    className="inline-link-button"
                  >
                    Hire one now.
                  </button>
                </p>
              </div>
            ) : (
              <Row className="g-3">
                {externalAgents.map(ext => (
                  <Col key={ext.id} md={6} xl={4}>
                    <div style={{ ...cardStyle, borderLeft: '4px solid #22c55e', cursor: 'default' }}>
                      <div className="d-flex align-items-center justify-content-between mb-2">
                        <div className="d-flex align-items-center gap-2">
                          <span style={{
                            width: 8, height: 8, borderRadius: '50%',
                            background: externalStatusDot(ext.status), flexShrink: 0,
                          }} />
                          <span style={{ fontSize: '0.95rem', fontWeight: 600, color: 'var(--color-foreground)' }}>
                            {ext.name}
                          </span>
                        </div>
                        {ext.protocol && (
                          <span style={{
                            fontSize: '0.65rem', padding: '2px 8px', borderRadius: 4,
                            background: 'rgba(34,197,94,0.12)', color: '#86efac', fontWeight: 600,
                          }}>
                            {ext.protocol}
                          </span>
                        )}
                      </div>

                      <p style={{
                        fontSize: '0.78rem', color: 'var(--color-muted)', margin: '0 0 10px 0',
                        display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
                      }}>
                        {ext.description || 'No description'}
                      </p>

                      <div className="d-flex gap-3 mb-2" style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>
                        {ext.task_count != null && <span>{ext.task_count} tasks</span>}
                        {ext.success_count != null && <span>{ext.success_count} success</span>}
                        {ext.last_seen && (
                          <span>last seen {new Date(ext.last_seen).toLocaleDateString()}</span>
                        )}
                      </div>

                      <div className="d-flex justify-content-end gap-1 mt-1">
                        <button
                          onClick={(e) => handleHealthCheck(e, ext)}
                          style={{
                            background: 'none', border: '1px solid var(--color-border)',
                            borderRadius: 4, padding: '2px 8px', fontSize: '0.68rem',
                            color: '#4dabf7', cursor: 'pointer',
                          }}
                        >
                          Health Check
                        </button>
                        <button
                          onClick={(e) => handleFireExternal(e, ext)}
                          style={{
                            background: 'none', border: '1px solid var(--color-border)',
                            borderRadius: 4, padding: '2px 8px', fontSize: '0.68rem',
                            color: '#ef4444', cursor: 'pointer',
                          }}
                        >
                          Fire
                        </button>
                      </div>
                    </div>
                  </Col>
                ))}
              </Row>
            )}
          </>
        )}
      </div>

      {/* Delete Confirmation Modal */}
      <Modal show={!!deleteConfirm} onHide={() => setDeleteConfirm(null)} centered size="sm">
        <Modal.Body className="text-center py-4">
          <p style={{ fontSize: '0.88rem', fontWeight: 500, marginBottom: 8 }}>
            {t('deleteModal.title', { name: deleteConfirm?.name })}
          </p>
          <p style={{ fontSize: '0.78rem', color: 'var(--color-muted)', marginBottom: 20 }}>
            {t('deleteModal.warning')}
          </p>
          <div className="d-flex justify-content-center gap-2">
            <Button variant="outline-secondary" size="sm" onClick={() => setDeleteConfirm(null)}>
              {t('deleteModal.cancel')}
            </Button>
            <Button variant="danger" size="sm" onClick={() => handleDelete(deleteConfirm)} disabled={submitting}>
              {submitting ? t('deleteModal.deleting') : t('deleteModal.delete')}
            </Button>
          </div>
        </Modal.Body>
      </Modal>

      {/* Hire External Agent Modal */}
      <Modal show={hireModalOpen} onHide={closeHireModal} centered>
        <Modal.Header style={{ background: 'var(--surface-elevated)', borderBottom: '1px solid var(--color-border)' }}>
          <Modal.Title style={{ fontSize: '0.95rem', fontWeight: 600 }}>Hire External Agent</Modal.Title>
        </Modal.Header>
        <Modal.Body style={{ background: 'var(--surface-elevated)' }}>
          <p style={{ fontSize: '0.78rem', color: 'var(--color-muted)', marginBottom: 16 }}>
            Register an external agent (OpenAI-compatible API, webhook, or A2A endpoint) so this platform can route tasks to it.
          </p>
          <Form.Group className="mb-3">
            <Form.Label style={{ fontSize: '0.82rem' }}>Name *</Form.Label>
            <Form.Control size="sm" value={hireForm.name} onChange={e => setHireForm(f => ({ ...f, name: e.target.value }))} placeholder="e.g. Billing Agent" />
          </Form.Group>
          <Form.Group className="mb-3">
            <Form.Label style={{ fontSize: '0.82rem' }}>Description</Form.Label>
            <Form.Control size="sm" value={hireForm.description} onChange={e => setHireForm(f => ({ ...f, description: e.target.value }))} placeholder="What does this agent do?" />
          </Form.Group>
          <Form.Group className="mb-3">
            <Form.Label style={{ fontSize: '0.82rem' }}>Endpoint URL *</Form.Label>
            <Form.Control size="sm" value={hireForm.endpoint_url} onChange={e => setHireForm(f => ({ ...f, endpoint_url: e.target.value }))} placeholder="https://agent.example.com/v1/chat" />
            <Form.Text className="text-muted" style={{ fontSize: '0.72rem' }}>Must be a public HTTPS URL. Private IPs are blocked.</Form.Text>
          </Form.Group>
          <Row className="mb-3">
            <Col>
              <Form.Label style={{ fontSize: '0.82rem' }}>Protocol</Form.Label>
              <Form.Select size="sm" value={hireForm.protocol} onChange={e => setHireForm(f => ({ ...f, protocol: e.target.value }))}>
                <option value="openai_chat">OpenAI Chat</option>
                <option value="webhook">Webhook</option>
                <option value="mcp_sse">MCP (SSE)</option>
                <option value="a2a">A2A</option>
                <option value="copilot_extension">Copilot Extension</option>
              </Form.Select>
            </Col>
            <Col>
              <Form.Label style={{ fontSize: '0.82rem' }}>Auth Type</Form.Label>
              <Form.Select size="sm" value={hireForm.auth_type} onChange={e => setHireForm(f => ({ ...f, auth_type: e.target.value }))}>
                <option value="none">None</option>
                <option value="bearer">Bearer Token</option>
                <option value="api_key">API Key</option>
                <option value="hmac">HMAC</option>
              </Form.Select>
            </Col>
          </Row>
          <Form.Group className="mb-3">
            <Form.Label style={{ fontSize: '0.82rem' }}>Credential ID (optional)</Form.Label>
            <Form.Control size="sm" value={hireForm.credential_id} onChange={e => setHireForm(f => ({ ...f, credential_id: e.target.value }))} placeholder="UUID of a stored credential" />
            <Form.Text className="text-muted" style={{ fontSize: '0.72rem' }}>
              Create credentials via the Integrations page. Leave empty if auth type is "none".
            </Form.Text>
          </Form.Group>
          {hireForm.auth_type !== 'none' && !hireForm.credential_id && (
            <Alert variant="warning" style={{ fontSize: '0.75rem', padding: '8px 12px' }}>
              Auth type is <b>{hireForm.auth_type}</b> but no credential is attached — dispatches will fail until you link one.
            </Alert>
          )}
          <Form.Group className="mb-3">
            <Form.Label style={{ fontSize: '0.82rem' }}>Capabilities (comma-separated)</Form.Label>
            <Form.Control size="sm" value={hireForm.capabilities} onChange={e => setHireForm(f => ({ ...f, capabilities: e.target.value }))} placeholder="sql_query, data_summary, report_generation" />
            <Form.Text className="text-muted" style={{ fontSize: '0.72rem' }}>Skills this agent can handle. Used for auto-routing.</Form.Text>
          </Form.Group>
        </Modal.Body>
        <Modal.Footer style={{ background: 'var(--surface-elevated)', borderTop: '1px solid var(--color-border)' }}>
          <Button variant="outline-secondary" size="sm" onClick={closeHireModal}>Cancel</Button>
          <Button variant="primary" size="sm" onClick={handleHire} disabled={hiring || !hireForm.name.trim() || !hireForm.endpoint_url.trim()}>
            {hiring ? 'Hiring...' : 'Hire Agent'}
          </Button>
        </Modal.Footer>
      </Modal>

      {/* Import Agent Modal */}
      <Modal show={importModalOpen} onHide={closeImportModal} centered>
        <Modal.Header style={{ background: 'var(--surface-elevated)', borderBottom: '1px solid var(--color-border)' }}>
          <Modal.Title style={{ fontSize: '0.95rem', fontWeight: 600 }}>Import Agent</Modal.Title>
        </Modal.Header>
        <Modal.Body style={{ background: 'var(--surface-elevated)' }}>
          <p style={{ fontSize: '0.78rem', color: 'var(--color-muted)', marginBottom: 8 }}>
            Paste a YAML or JSON agent definition. Supports CrewAI, LangChain, AutoGen, and native formats.
          </p>
          <p style={{ fontSize: '0.72rem', color: 'var(--color-muted)', marginBottom: 12 }}>
            Minimum required fields: <code>name</code> and <code>description</code>. Optional: <code>capabilities</code>, <code>persona_prompt</code>, <code>role</code>.
          </p>
          <Form.Control
            as="textarea"
            rows={10}
            value={importContent}
            onChange={e => setImportContent(e.target.value)}
            placeholder={'name: My Agent\ndescription: What this agent does\ncapabilities:\n  - knowledge_search\n  - sql_query\npersona_prompt: You are a helpful agent that...'}
            style={{ fontSize: '0.78rem', fontFamily: 'monospace' }}
          />
        </Modal.Body>
        <Modal.Footer style={{ background: 'var(--surface-elevated)', borderTop: '1px solid var(--color-border)' }}>
          <Button variant="outline-secondary" size="sm" onClick={closeImportModal}>
            Cancel
          </Button>
          <Button variant="primary" size="sm" onClick={handleImport} disabled={importing || !importContent.trim()}>
            {importing ? 'Importing...' : 'Import'}
          </Button>
        </Modal.Footer>
      </Modal>
    </Layout>
  );
};

export default AgentsPage;
