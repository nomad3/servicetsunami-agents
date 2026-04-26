import { useEffect, useState, useMemo } from 'react';
import { Alert, Badge, Button, Col, Form, Modal, Row, Spinner } from 'react-bootstrap';
import { FaFileImport, FaPlus, FaUserPlus } from 'react-icons/fa';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import Layout from '../components/Layout';
import agentService from '../services/agent';
import MarketplaceSection from '../components/agent/MarketplaceSection';
import HireAgentWizard from '../components/HireAgentWizard';
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

  const statusColor = (s) => {
    if (s === 'active') return 'var(--ap-success)';
    if (s === 'error') return 'var(--ap-danger)';
    return 'var(--ap-text-subtle)';
  };

  const externalStatusDot = (s) => {
    if (s === 'online') return 'var(--ap-success)';
    if (s === 'busy') return 'var(--ap-warning)';
    if (s === 'error') return 'var(--ap-danger)';
    return 'var(--ap-text-subtle)';
  };

  const AUTONOMY_LABELS = { full: 'Full Auto', supervised: 'Supervised', approval_required: 'Approval Req.' };

  return (
    <Layout>
      <div style={{ maxWidth: 1100 }}>
        {/* Header */}
        <header className="ap-page-header">
          <div>
            <h1 className="ap-page-title">{t('title')}</h1>
            <p className="ap-page-subtitle">
              {agents.length} agents · {externalAgents.length} external
            </p>
          </div>
          <div className="ap-page-actions">
            <button type="button" className="ap-btn-secondary" onClick={() => setImportModalOpen(true)}>
              <FaFileImport size={12} /> {t('importAgent', 'Import Agent')}
            </button>
            <button type="button" className="ap-btn-secondary" onClick={() => setHireModalOpen(true)}>
              <FaUserPlus size={12} /> {t('hireExternal', 'Hire External Agent')}
            </button>
            <button type="button" className="ap-btn-primary" onClick={() => navigate('/agents/wizard')}>
              <FaPlus size={12} /> {t('agentWizard')}
            </button>
          </div>
        </header>

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
            <div className="ap-section-label">{t('agents', 'Agents')}</div>

            {/* Search + lifecycle filter */}
            <div className="d-flex gap-2 align-items-center mb-3 flex-wrap">
              <Form.Control
                type="text"
                size="sm"
                placeholder={t('searchPlaceholder')}
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                style={{ maxWidth: 260, fontSize: 'var(--ap-fs-sm)' }}
              />
              <div className="d-flex gap-2 flex-wrap" role="group" aria-label="Lifecycle filter">
                {LIFECYCLE_STATUSES.map(status => (
                  <button
                    key={status}
                    type="button"
                    aria-pressed={lifecycleFilter === status}
                    className={`ap-chip-filter ${lifecycleFilter === status ? 'active' : ''}`}
                    onClick={() => setLifecycleFilter(status)}
                  >
                    {status}
                  </button>
                ))}
              </div>
            </div>

            {filteredAgents.length === 0 ? (
              <div className="ap-empty">
                <div className="ap-empty-title">
                  {searchTerm || lifecycleFilter !== 'All' ? t('noAgentsMatch') : t('noAgentsYet')}
                </div>
                <p className="ap-empty-text">
                  {searchTerm || lifecycleFilter !== 'All' ? t('tryDifferent') : t('createFirst')}
                </p>
                {!searchTerm && lifecycleFilter === 'All' && (
                  <button type="button" className="ap-btn-primary" onClick={() => navigate('/agents/wizard')}>
                    {t('createAgent')}
                  </button>
                )}
              </div>
            ) : (
              <Row className="g-3">
                {filteredAgents.map((agent) => {
                  const skills = getSkills(agent);
                  const stats = tasksByAgent[agent.id] || { active: 0, completed: 0, total: 0 };
                  const successRate = stats.total > 0 ? Math.round((stats.completed / stats.total) * 100) : 0;
                  const ls = agent.status || 'draft';
                  const lsKey = ls.toLowerCase();
                  const isDeprecated = lsKey === 'deprecated';
                  const canPromote = ['draft', 'staging'].includes(lsKey);
                  const canDeprecate = lsKey === 'production';

                  return (
                    <Col key={agent.id} md={6} xl={4}>
                      <article
                        className="ap-card"
                        onClick={() => navigate(`/agents/${agent.id}`)}
                        style={{ cursor: 'pointer' }}
                      >
                        <div className="ap-card-body">
                          <div className="d-flex align-items-start justify-content-between mb-2">
                            <div className="d-flex align-items-center gap-2" style={{ minWidth: 0, flex: 1 }}>
                              <span className="ap-status-dot" style={{ color: statusColor(agent.status), flexShrink: 0 }} />
                              <h3
                                className="ap-card-title"
                                style={{
                                  margin: 0,
                                  textDecoration: isDeprecated ? 'line-through' : 'none',
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  whiteSpace: 'nowrap',
                                }}
                              >
                                {agent.name}
                              </h3>
                            </div>
                            <div className="d-flex align-items-center gap-1" style={{ flexShrink: 0 }}>
                              <span className={`ap-status ap-status-${lsKey}`}>
                                {ls}
                              </span>
                              <span
                                className="ap-badge-outline"
                                title="Model tier — actual model is selected by the tenant's routed CLI platform"
                              >
                                {agent.default_model_tier || 'full'} tier
                              </span>
                            </div>
                          </div>

                          <p
                            className="ap-card-text mb-2"
                            style={{
                              display: '-webkit-box',
                              WebkitLineClamp: 2,
                              WebkitBoxOrient: 'vertical',
                              overflow: 'hidden',
                              minHeight: 40,
                            }}
                          >
                            {agent.description || t('noDescription', 'No description')}
                          </p>

                          <div className="d-flex gap-2 mb-2 flex-wrap align-items-center">
                            {agent.role && (
                              <span
                                className="ap-badge-solid"
                                style={{
                                  background: 'var(--ap-primary-tint)',
                                  color: 'var(--ap-primary)',
                                }}
                              >
                                {agent.role}
                              </span>
                            )}
                            <span className="ap-badge-outline">
                              {AUTONOMY_LABELS[agent.autonomy_level] || agent.autonomy_level || 'supervised'}
                            </span>
                            <span
                              style={{
                                fontSize: 'var(--ap-fs-xs)',
                                color: 'var(--ap-text-subtle)',
                                marginLeft: 'auto',
                              }}
                            >
                              {agent.owner_user_id ? 'Owned' : 'Unowned'}
                            </span>
                          </div>

                          {skills.length > 0 && (
                            <div className="d-flex gap-1 mb-2 flex-wrap">
                              {skills.slice(0, 4).map(s => (
                                <span key={s} className="ap-badge-outline" style={{ textTransform: 'none' }}>
                                  {s.replace(/_/g, ' ')}
                                </span>
                              ))}
                              {skills.length > 4 && (
                                <span style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-subtle)' }}>
                                  +{skills.length - 4} more
                                </span>
                              )}
                            </div>
                          )}

                          <div
                            className="d-flex align-items-center gap-3"
                            style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-subtle)' }}
                          >
                            <span>{stats.active} active</span>
                            <span>{stats.completed} completed</span>
                            {stats.total > 0 && (
                              <div className="d-flex align-items-center gap-1">
                                <div
                                  style={{
                                    width: 40,
                                    height: 4,
                                    borderRadius: 2,
                                    background: 'var(--ap-primary-tint-hi)',
                                  }}
                                >
                                  <div
                                    style={{
                                      width: `${successRate}%`,
                                      height: '100%',
                                      borderRadius: 2,
                                      background: 'var(--ap-success)',
                                    }}
                                  />
                                </div>
                                <span>{successRate}%</span>
                              </div>
                            )}
                          </div>
                        </div>

                        <footer
                          className="d-flex align-items-center gap-2 px-3 pb-3"
                          style={{ justifyContent: 'flex-end' }}
                        >
                          {canPromote && (
                            <button
                              type="button"
                              className="ap-btn-ghost ap-btn-sm"
                              onClick={(e) => handlePromote(e, agent)}
                            >
                              Promote
                            </button>
                          )}
                          {canDeprecate && (
                            <button
                              type="button"
                              className="ap-btn-ghost ap-btn-sm"
                              onClick={(e) => handleDeprecate(e, agent)}
                            >
                              Deprecate
                            </button>
                          )}
                          <button
                            type="button"
                            className="ap-btn-danger ap-btn-sm"
                            onClick={(e) => { e.stopPropagation(); setDeleteConfirm(agent); }}
                          >
                            Delete
                          </button>
                        </footer>
                      </article>
                    </Col>
                  );
                })}
              </Row>
            )}

            {/* ── Section 3: External Agents ── */}
            <div className="ap-section-label" style={{ marginTop: 'var(--ap-space-6)' }}>
              {t('externalAgents', 'External Agents')}
            </div>
            {externalAgents.length === 0 ? (
              <div className="ap-empty">
                <p className="ap-empty-text" style={{ marginBottom: 0 }}>
                  {t('noExternalYet', 'No external agents hired yet.')}{' '}
                  <button
                    type="button"
                    onClick={() => setHireModalOpen(true)}
                    className="ap-inline-link"
                  >
                    {t('hireOneNow', 'Hire one now.')}
                  </button>
                </p>
              </div>
            ) : (
              <Row className="g-3">
                {externalAgents.map(ext => (
                  <Col key={ext.id} md={6} xl={4}>
                    <article className="ap-card" style={{ borderLeft: '4px solid var(--ap-success)' }}>
                      <div className="ap-card-body">
                        <div className="d-flex align-items-start justify-content-between mb-2">
                          <div className="d-flex align-items-center gap-2" style={{ minWidth: 0, flex: 1 }}>
                            <span className="ap-status-dot" style={{ color: externalStatusDot(ext.status), flexShrink: 0 }} />
                            <h3 className="ap-card-title" style={{ margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {ext.name}
                            </h3>
                          </div>
                          {ext.protocol && (
                            <span
                              className="ap-badge-solid"
                              style={{
                                background: 'var(--ap-success-tint)',
                                color: 'var(--ap-success)',
                              }}
                            >
                              {ext.protocol}
                            </span>
                          )}
                        </div>

                        <p
                          className="ap-card-text mb-2"
                          style={{
                            display: '-webkit-box',
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: 'vertical',
                            overflow: 'hidden',
                            minHeight: 40,
                          }}
                        >
                          {ext.description || t('noDescription', 'No description')}
                        </p>

                        <div className="d-flex gap-3 mb-2" style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-subtle)' }}>
                          {ext.task_count != null && <span>{ext.task_count} tasks</span>}
                          {ext.success_count != null && <span>{ext.success_count} success</span>}
                          {ext.last_seen && (
                            <span>last seen {new Date(ext.last_seen).toLocaleDateString()}</span>
                          )}
                        </div>
                      </div>

                      <footer
                        className="d-flex align-items-center gap-2 px-3 pb-3"
                        style={{ justifyContent: 'flex-end' }}
                      >
                        <button
                          type="button"
                          className="ap-btn-ghost ap-btn-sm"
                          onClick={(e) => handleHealthCheck(e, ext)}
                        >
                          Health Check
                        </button>
                        <button
                          type="button"
                          className="ap-btn-danger ap-btn-sm"
                          onClick={(e) => handleFireExternal(e, ext)}
                        >
                          Fire
                        </button>
                      </footer>
                    </article>
                  </Col>
                ))}
              </Row>
            )}

            {/* ── Section 4: Marketplace (ALM Pillar 9) ── */}
            <MarketplaceSection />
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

      {/* Hire Agent Wizard (PR-D) — replaces the previous form-only
          modal. Three-step flow: capability search → source picker →
          preview + hire. Reuses /agents/discover, /external-agents,
          /agents/import, and /agent-marketplace endpoints. */}
      <Modal show={hireModalOpen} onHide={closeHireModal} size="lg" centered>
        <Modal.Header closeButton style={{ background: 'var(--surface-elevated)', borderBottom: '1px solid var(--color-border)' }}>
          <Modal.Title style={{ fontSize: '0.95rem', fontWeight: 600 }}>Hire Agent</Modal.Title>
        </Modal.Header>
        <Modal.Body style={{ background: 'var(--surface-elevated)' }}>
          {hireModalOpen && (
            <HireAgentWizard
              onClose={closeHireModal}
              onHired={() => {
                // Refresh the fleet list so the new agent shows up.
                loadAgents();
              }}
            />
          )}
        </Modal.Body>
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
