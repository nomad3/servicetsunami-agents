import { useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Col,
  Form,
  Modal,
  Row,
  Spinner
} from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate } from 'react-router-dom';
import Layout from '../components/Layout';
import agentService from '../services/agent';

const AgentsPage = () => {
  const { t } = useTranslation('agents');
  const navigate = useNavigate();
  const location = useLocation();
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [showModal, setShowModal] = useState(false);
  const [editingAgent, setEditingAgent] = useState(null);
  const [deleteConfirm, setDeleteConfirm] = useState(null);
  const [formData, setFormData] = useState({
    name: '',
    description: '',
    model: 'gpt-4',
    system_prompt: '',
    temperature: 0.7,
    max_tokens: 2000,
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  useEffect(() => {
    fetchAgents();
  }, []);

  useEffect(() => {
    if (location.state?.showQuickForm) {
      openCreateModal();
      window.history.replaceState({}, document.title);
    }
    if (location.state?.success) {
      setSuccess(location.state.success);
      window.history.replaceState({}, document.title);
      setTimeout(() => setSuccess(''), 3000);
    }
  }, [location]);

  const fetchAgents = async () => {
    try {
      setLoading(true);
      const response = await agentService.getAll();
      setAgents(response.data || []);
      setError('');
    } catch (err) {
      console.error('Error fetching agents:', err);
      setError(t('errors.load'));
    } finally {
      setLoading(false);
    }
  };

  const openCreateModal = () => {
    setEditingAgent(null);
    setFormData({ name: '', description: '', model: 'gpt-4', system_prompt: '', temperature: 0.7, max_tokens: 2000 });
    setShowModal(true);
  };

  const openEditModal = (agent) => {
    setEditingAgent(agent);
    setFormData({
      name: agent.name,
      description: agent.description || '',
      model: agent.model || 'gpt-4',
      system_prompt: agent.system_prompt || '',
      temperature: agent.temperature || 0.7,
      max_tokens: agent.max_tokens || 2000,
    });
    setShowModal(true);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      setSubmitting(true);
      if (editingAgent) {
        await agentService.update(editingAgent.id, formData);
        setSuccess(t('success.updated'));
      } else {
        await agentService.create(formData);
        setSuccess(t('success.created'));
      }
      setShowModal(false);
      fetchAgents();
      setTimeout(() => setSuccess(''), 3000);
    } catch (err) {
      console.error('Error saving agent:', err);
      setError(t('errors.save'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (agent) => {
    try {
      setSubmitting(true);
      await agentService.delete(agent.id);
      setDeleteConfirm(null);
      setSuccess(t('success.deleted', { name: agent.name }));
      fetchAgents();
      setTimeout(() => setSuccess(''), 3000);
    } catch (err) {
      console.error('Error deleting agent:', err);
      setError(t('errors.delete'));
    } finally {
      setSubmitting(false);
    }
  };

  const filtered = agents.filter(
    (a) =>
      a.name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      a.description?.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const statusDot = (status) => ({
    width: 6,
    height: 6,
    borderRadius: '50%',
    background: status === 'active' ? '#22c55e' : status === 'error' ? '#ef4444' : '#94a3b8',
    display: 'inline-block',
    marginRight: 6,
    flexShrink: 0,
  });

  const cardStyle = {
    background: 'var(--surface-elevated)',
    border: '1px solid var(--color-border)',
    borderRadius: 8,
    padding: '20px 24px',
  };

  const sectionLabel = {
    fontSize: '0.7rem',
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
    color: 'var(--color-muted)',
    marginBottom: 12,
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
              {t('subtitle', { count: agents.length })}
            </p>
          </div>
          <div className="d-flex gap-2">
            <Button
              variant="outline-secondary"
              size="sm"
              onClick={() => navigate('/agents/wizard')}
              style={{ fontSize: '0.82rem' }}
            >
              + {t('agentWizard')}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={openCreateModal}
              style={{ fontSize: '0.82rem' }}
            >
              + {t('quickCreate')}
            </Button>
          </div>
        </div>

        {error && <Alert variant="danger" dismissible onClose={() => setError('')} style={{ fontSize: '0.82rem' }}>{error}</Alert>}
        {success && <Alert variant="success" dismissible onClose={() => setSuccess('')} style={{ fontSize: '0.82rem' }}>{success}</Alert>}

        {/* Search */}
        <div style={{ marginBottom: 16 }}>
          <Form.Control
            type="text"
            size="sm"
            placeholder={t('searchPlaceholder')}
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            style={{ maxWidth: 300, fontSize: '0.82rem' }}
          />
        </div>

        {/* Agent List */}
        {loading ? (
          <div className="text-center py-5">
            <Spinner animation="border" size="sm" variant="primary" />
            <p className="mt-2 text-muted" style={{ fontSize: '0.82rem' }}>{t('loading')}</p>
          </div>
        ) : filtered.length === 0 ? (
          <div style={{ ...cardStyle, textAlign: 'center', padding: '48px 24px' }}>
            <p style={{ fontSize: '0.88rem', color: 'var(--color-foreground)', fontWeight: 500, marginBottom: 4 }}>
              {searchTerm ? t('noAgentsMatch') : t('noAgentsYet')}
            </p>
            <p style={{ fontSize: '0.78rem', color: 'var(--color-muted)', marginBottom: 16 }}>
              {searchTerm ? t('tryDifferent') : t('createFirst')}
            </p>
            {!searchTerm && (
              <Button variant="primary" size="sm" onClick={openCreateModal}>
                {t('createAgent')}
              </Button>
            )}
          </div>
        ) : (
          <div style={cardStyle}>
            {/* Table header */}
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '2fr 3fr 100px 80px 120px',
                padding: '0 0 10px 0',
                borderBottom: '1px solid var(--color-border)',
                gap: 12,
              }}
            >
              {[t('table.name'), t('table.description'), t('table.model'), t('table.status'), ''].map((h) => (
                <div key={h} style={{ ...sectionLabel, marginBottom: 0 }}>{h}</div>
              ))}
            </div>

            {/* Rows */}
            {filtered.map((agent, idx) => (
              <div
                key={agent.id}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '2fr 3fr 100px 80px 120px',
                  padding: '12px 0',
                  borderBottom: idx < filtered.length - 1 ? '1px solid var(--color-border)' : 'none',
                  alignItems: 'center',
                  gap: 12,
                }}
              >
                <div style={{ fontSize: '0.85rem', fontWeight: 500, color: 'var(--color-foreground)' }}>
                  {agent.name}
                </div>
                <div style={{ fontSize: '0.78rem', color: 'var(--color-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {agent.description || '\u2014'}
                </div>
                <div>
                  <span style={{
                    fontSize: '0.7rem',
                    padding: '2px 8px',
                    borderRadius: 4,
                    background: 'var(--surface-contrast, #f0f0f0)',
                    color: 'var(--color-muted)',
                    fontWeight: 500,
                  }}>
                    {agent.model || 'gpt-4'}
                  </span>
                </div>
                <div className="d-flex align-items-center">
                  <span style={statusDot(agent.status)} />
                  <span style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>
                    {agent.status || 'inactive'}
                  </span>
                </div>
                <div className="d-flex justify-content-end gap-1">
                  <button
                    onClick={() => openEditModal(agent)}
                    style={{
                      background: 'none',
                      border: '1px solid var(--color-border)',
                      borderRadius: 4,
                      padding: '4px 10px',
                      fontSize: '0.72rem',
                      color: 'var(--color-foreground)',
                      cursor: 'pointer',
                    }}
                  >
                    {t('actions.edit')}
                  </button>
                  <button
                    onClick={() => setDeleteConfirm(agent)}
                    style={{
                      background: 'none',
                      border: '1px solid var(--color-border)',
                      borderRadius: 4,
                      padding: '4px 10px',
                      fontSize: '0.72rem',
                      color: '#ef4444',
                      cursor: 'pointer',
                    }}
                  >
                    {t('actions.delete')}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Create/Edit Modal */}
      <Modal
        show={showModal}
        onHide={() => setShowModal(false)}
        size="lg"
        centered
      >
        <Modal.Header closeButton>
          <Modal.Title style={{ fontSize: '1rem', fontWeight: 600 }}>
            {editingAgent ? t('modal.editTitle') : t('modal.createTitle')}
          </Modal.Title>
        </Modal.Header>
        <Form onSubmit={handleSubmit}>
          <Modal.Body>
            <Row>
              <Col md={6}>
                <Form.Group className="mb-3">
                  <Form.Label className="small">{t('modal.name')}</Form.Label>
                  <Form.Control
                    size="sm"
                    type="text"
                    placeholder={t('modal.namePlaceholder')}
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    required
                  />
                </Form.Group>
              </Col>
              <Col md={6}>
                <Form.Group className="mb-3">
                  <Form.Label className="small">{t('modal.model')}</Form.Label>
                  <Form.Select
                    size="sm"
                    value={formData.model}
                    onChange={(e) => setFormData({ ...formData, model: e.target.value })}
                  >
                    <option value="gpt-4">GPT-4</option>
                    <option value="gpt-4-turbo">GPT-4 Turbo</option>
                    <option value="gpt-4o">GPT-4o</option>
                    <option value="gpt-3.5-turbo">GPT-3.5 Turbo</option>
                    <option value="claude-3-opus">Claude 3 Opus</option>
                    <option value="claude-3-sonnet">Claude 3 Sonnet</option>
                    <option value="claude-4-sonnet">Claude 4 Sonnet</option>
                    <option value="gemini-2.0-flash">Gemini 2.0 Flash</option>
                  </Form.Select>
                </Form.Group>
              </Col>
            </Row>

            <Form.Group className="mb-3">
              <Form.Label className="small">{t('modal.description')}</Form.Label>
              <Form.Control
                size="sm"
                as="textarea"
                rows={2}
                placeholder={t('modal.descriptionPlaceholder')}
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              />
            </Form.Group>

            <Form.Group className="mb-3">
              <Form.Label className="small">{t('modal.systemPrompt')}</Form.Label>
              <Form.Control
                size="sm"
                as="textarea"
                rows={4}
                placeholder={t('modal.systemPromptPlaceholder')}
                value={formData.system_prompt}
                onChange={(e) => setFormData({ ...formData, system_prompt: e.target.value })}
              />
            </Form.Group>

            <Row>
              <Col md={6}>
                <Form.Group className="mb-3">
                  <Form.Label className="small">{t('modal.temperature', { value: formData.temperature })}</Form.Label>
                  <Form.Range
                    min={0} max={1} step={0.1}
                    value={formData.temperature}
                    onChange={(e) => setFormData({ ...formData, temperature: parseFloat(e.target.value) })}
                  />
                  <Form.Text className="text-muted" style={{ fontSize: '0.72rem' }}>
                    {t('modal.temperatureHelp')}
                  </Form.Text>
                </Form.Group>
              </Col>
              <Col md={6}>
                <Form.Group className="mb-3">
                  <Form.Label className="small">{t('modal.maxTokens')}</Form.Label>
                  <Form.Control
                    size="sm"
                    type="number"
                    min={100} max={8000}
                    value={formData.max_tokens}
                    onChange={(e) => setFormData({ ...formData, max_tokens: parseInt(e.target.value) })}
                  />
                </Form.Group>
              </Col>
            </Row>
          </Modal.Body>
          <Modal.Footer>
            <Button variant="outline-secondary" size="sm" onClick={() => setShowModal(false)}>
              {t('modal.cancel')}
            </Button>
            <Button variant="primary" size="sm" type="submit" disabled={submitting}>
              {submitting ? t('modal.saving') : editingAgent ? t('modal.saveChanges') : t('modal.createTitle')}
            </Button>
          </Modal.Footer>
        </Form>
      </Modal>

      {/* Delete Confirmation */}
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
            <Button
              variant="danger"
              size="sm"
              onClick={() => handleDelete(deleteConfirm)}
              disabled={submitting}
            >
              {submitting ? t('deleteModal.deleting') : t('deleteModal.delete')}
            </Button>
          </div>
        </Modal.Body>
      </Modal>
    </Layout>
  );
};

export default AgentsPage;
