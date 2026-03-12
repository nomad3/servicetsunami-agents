import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Form,
  Modal,
  Row,
  Spinner,
} from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import {
  FaCode,
  FaGithub,
  FaPlay,
  FaPlus,
  FaPuzzlePiece,
  FaTimes,
  FaTrash,
} from 'react-icons/fa';
import Layout from '../components/Layout';
import api from '../services/api';

const SkillsPage = () => {
  const { t } = useTranslation('skills');
  const [skills, setSkills] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  // Execute modal state
  const [executeSkill, setExecuteSkill] = useState(null);
  const [executeInputs, setExecuteInputs] = useState({});
  const [executing, setExecuting] = useState(false);
  const [executeResult, setExecuteResult] = useState(null);

  // Create modal state
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);

  // GitHub import state
  const [showImport, setShowImport] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importUrl, setImportUrl] = useState('');
  const ENGINE_DEFAULTS = {
    python: 'def execute(inputs):\n    # Your skill logic here\n    return {"result": "done"}',
    shell: '#!/bin/bash\n# Inputs are available as SKILL_INPUT_<NAME> env vars\necho "Hello from skill"\n',
    markdown: '# Prompt Template\n\nUse {{input_name}} for placeholders.\n\nInstructions for the agent go here.\n',
  };

  const [newSkill, setNewSkill] = useState({
    name: '',
    description: '',
    engine: 'python',
    script: ENGINE_DEFAULTS.python,
    inputs: [],
  });

  const fetchSkills = useCallback(async () => {
    try {
      setLoading(true);
      const response = await api.get('/skills/library');
      setSkills(response.data || []);
    } catch (err) {
      console.error('Error fetching skills:', err);
      setError(t('errors.load'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    fetchSkills();
  }, [fetchSkills]);

  // ── Execute skill ──
  const handleOpenExecute = (skill) => {
    setExecuteSkill(skill);
    const defaults = {};
    (skill.inputs || []).forEach((inp) => {
      defaults[inp.name] = '';
    });
    setExecuteInputs(defaults);
    setExecuteResult(null);
  };

  const handleExecute = async () => {
    if (!executeSkill) return;
    try {
      setExecuting(true);
      setExecuteResult(null);
      const response = await api.post('/skills/library/execute', {
        skill_name: executeSkill.name,
        inputs: executeInputs,
      });
      setExecuteResult({ success: true, data: response.data });
    } catch (err) {
      const detail = err.response?.data?.detail || t('errors.execute');
      setExecuteResult({ success: false, error: detail });
    } finally {
      setExecuting(false);
    }
  };

  // ── Create skill ──
  const handleAddInput = () => {
    setNewSkill({
      ...newSkill,
      inputs: [...newSkill.inputs, { name: '', type: 'string', description: '', required: false }],
    });
  };

  const handleRemoveInput = (index) => {
    const updated = newSkill.inputs.filter((_, i) => i !== index);
    setNewSkill({ ...newSkill, inputs: updated });
  };

  const handleInputChange = (index, field, value) => {
    const updated = [...newSkill.inputs];
    updated[index] = { ...updated[index], [field]: value };
    setNewSkill({ ...newSkill, inputs: updated });
  };

  const handleCreateSkill = async () => {
    if (!newSkill.name.trim()) return;
    try {
      setCreating(true);
      await api.post('/skills/library/create', {
        name: newSkill.name.trim(),
        description: newSkill.description.trim(),
        engine: newSkill.engine,
        script: newSkill.script,
        inputs: newSkill.inputs.filter((i) => i.name.trim()),
      });
      setSuccess(t('skillCreated'));
      setTimeout(() => setSuccess(null), 4000);
      setShowCreate(false);
      setNewSkill({
        name: '',
        description: '',
        engine: 'python',
        script: ENGINE_DEFAULTS.python,
        inputs: [],
      });
      await fetchSkills();
    } catch (err) {
      const detail = err.response?.data?.detail || t('errors.create');
      setError(detail);
      setTimeout(() => setError(null), 5000);
    } finally {
      setCreating(false);
    }
  };

  // ── GitHub import ──
  const handleImportGithub = async () => {
    if (!importUrl.trim()) return;
    try {
      setImporting(true);
      const response = await api.post('/skills/library/import-github', {
        repo_url: importUrl.trim(),
      });
      const data = response.data;
      const imported = data.imported || (data.skill ? [data.skill.name] : []);
      setSuccess(`Imported ${imported.length} skill(s): ${imported.join(', ')}`);
      setTimeout(() => setSuccess(null), 5000);
      setShowImport(false);
      setImportUrl('');
      await fetchSkills();
    } catch (err) {
      const detail = err.response?.data?.detail || 'Import failed';
      setError(detail);
      setTimeout(() => setError(null), 5000);
    } finally {
      setImporting(false);
    }
  };

  return (
    <Layout>
      <div className="py-4 px-3" style={{ maxWidth: 1200, margin: '0 auto' }}>
        {/* Header */}
        <div className="d-flex align-items-center justify-content-between mb-4">
          <div className="d-flex align-items-center gap-3">
            <div
              style={{
                width: 48,
                height: 48,
                borderRadius: 12,
                background: 'linear-gradient(135deg, rgba(99, 102, 241, 0.2), rgba(139, 92, 246, 0.2))',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <FaPuzzlePiece size={22} style={{ color: 'var(--color-primary, #6366f1)' }} />
            </div>
            <div>
              <h2 className="mb-0" style={{ color: 'var(--color-foreground)', fontSize: '1.5rem' }}>
                {t('title')}
              </h2>
              <p className="text-muted mb-0 small">{t('subtitle')}</p>
            </div>
          </div>
          <div className="d-flex gap-2">
            <Button
              variant="outline-secondary"
              onClick={() => setShowImport(true)}
              style={{ borderRadius: 8 }}
            >
              <FaGithub className="me-2" size={14} />
              Import from GitHub
            </Button>
            <Button
              variant="primary"
              onClick={() => setShowCreate(true)}
              style={{ borderRadius: 8 }}
            >
              <FaPlus className="me-2" size={12} />
              {t('createSkill')}
            </Button>
          </div>
        </div>

        {/* Stats */}
        {!loading && skills.length > 0 && (
          <div className="mb-4">
            <Badge
              bg="secondary"
              style={{
                fontSize: '0.8rem',
                fontWeight: 500,
                padding: '6px 12px',
                borderRadius: 8,
              }}
            >
              {skills.length} {t('totalSkills')}
            </Badge>
          </div>
        )}

        {/* Alerts */}
        {error && (
          <Alert variant="danger" onClose={() => setError(null)} dismissible className="mb-3">
            {error}
          </Alert>
        )}
        {success && (
          <Alert variant="success" onClose={() => setSuccess(null)} dismissible className="mb-3">
            {success}
          </Alert>
        )}

        {/* Loading */}
        {loading && (
          <div className="text-center py-5">
            <Spinner animation="border" variant="primary" />
            <p className="text-muted mt-3 mb-0">{t('loading')}</p>
          </div>
        )}

        {/* Empty state */}
        {!loading && skills.length === 0 && (
          <Card
            className="text-center py-5"
            style={{
              background: 'var(--surface-elevated)',
              border: '1px solid var(--color-border)',
              borderRadius: 16,
            }}
          >
            <Card.Body>
              <FaPuzzlePiece
                size={48}
                className="mb-3"
                style={{ color: 'var(--color-foreground-muted)', opacity: 0.4 }}
              />
              <h5 style={{ color: 'var(--color-foreground)' }}>{t('noSkills')}</h5>
              <p className="text-muted mb-3">{t('noSkillsDesc')}</p>
              <Button variant="primary" onClick={() => setShowCreate(true)}>
                <FaPlus className="me-2" size={12} />
                {t('createSkill')}
              </Button>
            </Card.Body>
          </Card>
        )}

        {/* Skills Grid */}
        {!loading && skills.length > 0 && (
          <Row xs={1} md={2} lg={3} className="g-4">
            {skills.map((skill) => (
              <Col key={skill.name}>
                <Card
                  className="h-100"
                  style={{
                    background: 'var(--surface-elevated)',
                    border: '1px solid var(--color-border)',
                    borderRadius: 14,
                    transition: 'all 0.2s ease',
                    boxShadow: '0 2px 12px rgba(100, 130, 170, 0.08)',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.transform = 'translateY(-3px)';
                    e.currentTarget.style.boxShadow = '0 6px 20px rgba(100, 130, 170, 0.15)';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.transform = 'translateY(0)';
                    e.currentTarget.style.boxShadow = '0 2px 12px rgba(100, 130, 170, 0.08)';
                  }}
                >
                  <Card.Body className="d-flex flex-column" style={{ padding: '1.25rem' }}>
                    {/* Header */}
                    <div className="d-flex align-items-start justify-content-between mb-2">
                      <div className="d-flex align-items-center gap-2">
                        <div
                          style={{
                            width: 36,
                            height: 36,
                            borderRadius: 8,
                            background: 'rgba(99, 102, 241, 0.12)',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            flexShrink: 0,
                          }}
                        >
                          <FaCode size={16} style={{ color: 'var(--color-primary, #6366f1)' }} />
                        </div>
                        <h6
                          className="mb-0"
                          style={{
                            color: 'var(--color-foreground)',
                            fontSize: '0.95rem',
                            fontWeight: 600,
                          }}
                        >
                          {skill.name}
                        </h6>
                      </div>
                      <Badge
                        bg="dark"
                        style={{
                          fontSize: '0.68rem',
                          fontWeight: 500,
                          borderRadius: 6,
                          padding: '4px 8px',
                          textTransform: 'capitalize',
                        }}
                      >
                        {skill.engine}
                      </Badge>
                    </div>

                    {/* Description */}
                    {skill.description && (
                      <p
                        className="mb-3 flex-grow-1"
                        style={{
                          fontSize: '0.82rem',
                          color: 'var(--color-foreground-muted)',
                          lineHeight: 1.5,
                        }}
                      >
                        {skill.description.length > 140
                          ? skill.description.substring(0, 140) + '...'
                          : skill.description}
                      </p>
                    )}

                    {/* Inputs */}
                    {skill.inputs && skill.inputs.length > 0 && (
                      <div className="mb-3">
                        <small
                          className="text-muted fw-semibold d-block mb-2"
                          style={{ fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.5px' }}
                        >
                          {t('inputs')}
                        </small>
                        {skill.inputs.map((input) => (
                          <div
                            key={input.name}
                            className="d-flex align-items-center gap-2 mb-1"
                          >
                            <code
                              style={{
                                fontSize: '0.78rem',
                                color: 'var(--color-primary, #6366f1)',
                                background: 'rgba(99, 102, 241, 0.08)',
                                padding: '1px 6px',
                                borderRadius: 4,
                              }}
                            >
                              {input.name}
                            </code>
                            <Badge
                              bg={input.required ? 'primary' : 'secondary'}
                              style={{ fontSize: '0.62rem', fontWeight: 500 }}
                            >
                              {input.required ? t('required') : t('optional')}
                            </Badge>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Actions */}
                    <div className="d-flex gap-2 mt-auto pt-2" style={{ borderTop: '1px solid var(--color-border)' }}>
                      <Button
                        variant="primary"
                        size="sm"
                        className="flex-grow-1"
                        onClick={() => handleOpenExecute(skill)}
                        style={{ borderRadius: 8, fontSize: '0.82rem' }}
                      >
                        <FaPlay className="me-1" size={10} />
                        {t('tryIt')}
                      </Button>
                    </div>
                  </Card.Body>
                </Card>
              </Col>
            ))}
          </Row>
        )}

        {/* ── Execute Modal ── */}
        <Modal
          show={!!executeSkill}
          onHide={() => {
            setExecuteSkill(null);
            setExecuteResult(null);
          }}
          size="lg"
          centered
        >
          <Modal.Header
            closeButton
            style={{
              background: 'var(--surface-elevated)',
              borderBottom: '1px solid var(--color-border)',
            }}
          >
            <Modal.Title style={{ fontSize: '1.1rem' }}>
              <FaPlay className="me-2" size={14} />
              {t('execute.title')}: {executeSkill?.name}
            </Modal.Title>
          </Modal.Header>
          <Modal.Body style={{ background: 'var(--surface-elevated)' }}>
            {executeSkill?.inputs?.length > 0 ? (
              <div className="mb-3">
                {executeSkill.inputs.map((input) => (
                  <Form.Group key={input.name} className="mb-3">
                    <Form.Label style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>
                      {input.name}
                      {input.required && <span className="text-danger ms-1">*</span>}
                      {input.description && (
                        <span className="text-muted ms-2" style={{ fontSize: '0.75rem' }}>
                          — {input.description}
                        </span>
                      )}
                    </Form.Label>
                    <Form.Control
                      type="text"
                      placeholder={`${t('execute.inputValue')}...`}
                      value={executeInputs[input.name] || ''}
                      onChange={(e) =>
                        setExecuteInputs({ ...executeInputs, [input.name]: e.target.value })
                      }
                      style={{
                        background: 'var(--surface-contrast, rgba(0,0,0,0.2))',
                        border: '1px solid var(--color-border)',
                        color: 'var(--color-foreground)',
                        borderRadius: 8,
                      }}
                    />
                  </Form.Group>
                ))}
              </div>
            ) : (
              <p className="text-muted">{t('noSkillsDesc')}</p>
            )}

            {/* Result */}
            {executeResult && (
              <div className="mt-3">
                <Alert variant={executeResult.success ? 'success' : 'danger'}>
                  <strong>{executeResult.success ? t('execute.success') : t('execute.error')}</strong>
                </Alert>
                {executeResult.data && (
                  <pre
                    style={{
                      background: 'rgba(0,0,0,0.3)',
                      color: '#e2e8f0',
                      padding: '1rem',
                      borderRadius: 8,
                      fontSize: '0.82rem',
                      maxHeight: 300,
                      overflow: 'auto',
                    }}
                  >
                    {JSON.stringify(executeResult.data, null, 2)}
                  </pre>
                )}
                {executeResult.error && typeof executeResult.error === 'string' && (
                  <pre
                    style={{
                      background: 'rgba(220, 53, 69, 0.1)',
                      color: '#f87171',
                      padding: '1rem',
                      borderRadius: 8,
                      fontSize: '0.82rem',
                    }}
                  >
                    {executeResult.error}
                  </pre>
                )}
              </div>
            )}
          </Modal.Body>
          <Modal.Footer style={{ background: 'var(--surface-elevated)', borderTop: '1px solid var(--color-border)' }}>
            <Button
              variant="outline-secondary"
              onClick={() => {
                setExecuteSkill(null);
                setExecuteResult(null);
              }}
              style={{ borderRadius: 8 }}
            >
              {t('close')}
            </Button>
            <Button
              variant="primary"
              onClick={handleExecute}
              disabled={executing}
              style={{ borderRadius: 8 }}
            >
              {executing ? (
                <>
                  <Spinner animation="border" size="sm" className="me-2" style={{ width: 14, height: 14, borderWidth: 1.5 }} />
                  {t('running')}
                </>
              ) : (
                <>
                  <FaPlay className="me-2" size={10} />
                  {t('execute.submit')}
                </>
              )}
            </Button>
          </Modal.Footer>
        </Modal>

        {/* ── Create Modal ── */}
        <Modal show={showCreate} onHide={() => setShowCreate(false)} size="lg" centered>
          <Modal.Header
            closeButton
            style={{
              background: 'var(--surface-elevated)',
              borderBottom: '1px solid var(--color-border)',
            }}
          >
            <Modal.Title style={{ fontSize: '1.1rem' }}>
              <FaPlus className="me-2" size={14} />
              {t('createSkill')}
            </Modal.Title>
          </Modal.Header>
          <Modal.Body style={{ background: 'var(--surface-elevated)' }}>
            <Row>
              <Col md={8}>
                <Form.Group className="mb-3">
                  <Form.Label style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>
                    {t('form.name')} <span className="text-danger">*</span>
                  </Form.Label>
                  <Form.Control
                    type="text"
                    placeholder={t('form.namePlaceholder')}
                    value={newSkill.name}
                    onChange={(e) => setNewSkill({ ...newSkill, name: e.target.value })}
                    style={{
                      background: 'var(--surface-contrast, rgba(0,0,0,0.2))',
                      border: '1px solid var(--color-border)',
                      color: 'var(--color-foreground)',
                      borderRadius: 8,
                    }}
                  />
                </Form.Group>
              </Col>
              <Col md={4}>
                <Form.Group className="mb-3">
                  <Form.Label style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>
                    {t('form.engine')}
                  </Form.Label>
                  <Form.Select
                    value={newSkill.engine}
                    onChange={(e) => {
                      const eng = e.target.value;
                      setNewSkill({ ...newSkill, engine: eng, script: ENGINE_DEFAULTS[eng] || '' });
                    }}
                    style={{
                      background: 'var(--surface-contrast, rgba(0,0,0,0.2))',
                      border: '1px solid var(--color-border)',
                      color: 'var(--color-foreground)',
                      borderRadius: 8,
                    }}
                  >
                    <option value="python">Python</option>
                    <option value="shell">Shell</option>
                    <option value="markdown">Markdown</option>
                  </Form.Select>
                </Form.Group>
              </Col>
            </Row>

            <Form.Group className="mb-3">
              <Form.Label style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>
                {t('form.description')}
              </Form.Label>
              <Form.Control
                as="textarea"
                rows={2}
                placeholder={t('form.descriptionPlaceholder')}
                value={newSkill.description}
                onChange={(e) => setNewSkill({ ...newSkill, description: e.target.value })}
                style={{
                  background: 'var(--surface-contrast, rgba(0,0,0,0.2))',
                  border: '1px solid var(--color-border)',
                  color: 'var(--color-foreground)',
                  borderRadius: 8,
                }}
              />
            </Form.Group>

            {/* Inputs section */}
            <div className="mb-3">
              <div className="d-flex align-items-center justify-content-between mb-2">
                <Form.Label className="mb-0" style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>
                  {t('form.inputsSection')}
                </Form.Label>
                <Button variant="outline-primary" size="sm" onClick={handleAddInput} style={{ borderRadius: 6, fontSize: '0.78rem' }}>
                  <FaPlus className="me-1" size={10} />
                  {t('form.addInput')}
                </Button>
              </div>
              {newSkill.inputs.map((input, index) => (
                <div
                  key={index}
                  className="d-flex align-items-center gap-2 mb-2 p-2"
                  style={{
                    background: 'rgba(100, 130, 170, 0.05)',
                    borderRadius: 8,
                    border: '1px solid var(--color-border)',
                  }}
                >
                  <Form.Control
                    size="sm"
                    placeholder={t('form.inputName')}
                    value={input.name}
                    onChange={(e) => handleInputChange(index, 'name', e.target.value)}
                    style={{
                      background: 'var(--surface-contrast, rgba(0,0,0,0.2))',
                      border: '1px solid var(--color-border)',
                      color: 'var(--color-foreground)',
                      borderRadius: 6,
                      flex: '0 0 140px',
                    }}
                  />
                  <Form.Select
                    size="sm"
                    value={input.type}
                    onChange={(e) => handleInputChange(index, 'type', e.target.value)}
                    style={{
                      background: 'var(--surface-contrast, rgba(0,0,0,0.2))',
                      border: '1px solid var(--color-border)',
                      color: 'var(--color-foreground)',
                      borderRadius: 6,
                      flex: '0 0 100px',
                    }}
                  >
                    <option value="string">string</option>
                    <option value="number">number</option>
                    <option value="boolean">boolean</option>
                  </Form.Select>
                  <Form.Control
                    size="sm"
                    placeholder={t('form.inputDescription')}
                    value={input.description}
                    onChange={(e) => handleInputChange(index, 'description', e.target.value)}
                    style={{
                      background: 'var(--surface-contrast, rgba(0,0,0,0.2))',
                      border: '1px solid var(--color-border)',
                      color: 'var(--color-foreground)',
                      borderRadius: 6,
                      flex: 1,
                    }}
                  />
                  <Form.Check
                    type="switch"
                    label={t('form.inputRequired')}
                    checked={input.required}
                    onChange={(e) => handleInputChange(index, 'required', e.target.checked)}
                    style={{ fontSize: '0.75rem', whiteSpace: 'nowrap' }}
                  />
                  <Button
                    variant="outline-danger"
                    size="sm"
                    onClick={() => handleRemoveInput(index)}
                    style={{ borderRadius: 6, padding: '2px 8px' }}
                  >
                    <FaTimes size={10} />
                  </Button>
                </div>
              ))}
            </div>

            {/* Script */}
            <Form.Group className="mb-3">
              <Form.Label style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>
                {newSkill.engine === 'python' ? 'Script (Python)' : newSkill.engine === 'shell' ? 'Script (Shell)' : 'Prompt (Markdown)'}
              </Form.Label>
              <Form.Control
                as="textarea"
                rows={10}
                value={newSkill.script}
                onChange={(e) => setNewSkill({ ...newSkill, script: e.target.value })}
                style={{
                  background: 'rgba(0,0,0,0.3)',
                  border: '1px solid var(--color-border)',
                  color: '#e2e8f0',
                  borderRadius: 8,
                  fontFamily: 'monospace',
                  fontSize: '0.82rem',
                  lineHeight: 1.6,
                }}
              />
            </Form.Group>
          </Modal.Body>
          <Modal.Footer style={{ background: 'var(--surface-elevated)', borderTop: '1px solid var(--color-border)' }}>
            <Button variant="outline-secondary" onClick={() => setShowCreate(false)} style={{ borderRadius: 8 }}>
              {t('cancel')}
            </Button>
            <Button
              variant="primary"
              onClick={handleCreateSkill}
              disabled={creating || !newSkill.name.trim()}
              style={{ borderRadius: 8 }}
            >
              {creating ? (
                <>
                  <Spinner animation="border" size="sm" className="me-2" style={{ width: 14, height: 14, borderWidth: 1.5 }} />
                  {t('creating')}
                </>
              ) : (
                <>
                  <FaPlus className="me-2" size={10} />
                  {t('create')}
                </>
              )}
            </Button>
          </Modal.Footer>
        </Modal>

        {/* ── GitHub Import Modal ── */}
        <Modal show={showImport} onHide={() => setShowImport(false)} centered>
          <Modal.Header
            closeButton
            style={{
              background: 'var(--surface-elevated)',
              borderBottom: '1px solid var(--color-border)',
            }}
          >
            <Modal.Title style={{ fontSize: '1.1rem' }}>
              <FaGithub className="me-2" size={18} />
              Import from GitHub
            </Modal.Title>
          </Modal.Header>
          <Modal.Body style={{ background: 'var(--surface-elevated)' }}>
            <p className="text-muted small mb-3">
              Paste a GitHub repo URL containing skill directories (each with a <code>skill.md</code> file).
            </p>
            <Form.Group>
              <Form.Label style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>
                Repository URL
              </Form.Label>
              <Form.Control
                type="text"
                placeholder="https://github.com/owner/repo or owner/repo/path/to/skills"
                value={importUrl}
                onChange={(e) => setImportUrl(e.target.value)}
                style={{
                  background: 'var(--surface-contrast, rgba(0,0,0,0.2))',
                  border: '1px solid var(--color-border)',
                  color: 'var(--color-foreground)',
                  borderRadius: 8,
                }}
              />
              <Form.Text className="text-muted" style={{ fontSize: '0.75rem' }}>
                Supports: full URLs, <code>owner/repo</code>, or <code>owner/repo/path/to/skill</code>
              </Form.Text>
            </Form.Group>
          </Modal.Body>
          <Modal.Footer style={{ background: 'var(--surface-elevated)', borderTop: '1px solid var(--color-border)' }}>
            <Button variant="outline-secondary" onClick={() => setShowImport(false)} style={{ borderRadius: 8 }}>
              {t('cancel')}
            </Button>
            <Button
              variant="primary"
              onClick={handleImportGithub}
              disabled={importing || !importUrl.trim()}
              style={{ borderRadius: 8 }}
            >
              {importing ? (
                <>
                  <Spinner animation="border" size="sm" className="me-2" style={{ width: 14, height: 14, borderWidth: 1.5 }} />
                  Importing...
                </>
              ) : (
                <>
                  <FaGithub className="me-2" size={14} />
                  Import
                </>
              )}
            </Button>
          </Modal.Footer>
        </Modal>
      </div>
    </Layout>
  );
};

export default SkillsPage;
