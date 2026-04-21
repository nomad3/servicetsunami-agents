import React, { useState, useEffect } from 'react';
import {
  Table,
  Badge,
  Modal,
  Form,
  Row,
  Col,
} from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import {
  FaTools,
  FaPlus,
  FaSearch,
  FaPen,
  FaTrash,
  FaPlayCircle,
} from 'react-icons/fa';
import Layout from '../components/Layout';
import { EmptyState, LoadingSpinner, ConfirmModal, useToast } from '../components/common';
import toolService from '../services/tool';
import '../pages/AgentsPage.css';

const ToolsPage = () => {
  const { t } = useTranslation('tools');
  const toast = useToast();
  const [tools, setTools] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [selectedTool, setSelectedTool] = useState(null);
  const [formData, setFormData] = useState({
    name: '',
    description: '',
    tool_type: 'api',
    configuration: '',
    authentication_required: false,
  });
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    fetchTools();
  }, []);

  const fetchTools = async () => {
    try {
      setLoading(true);
      const response = await toolService.getAll();
      setTools(response.data || []);
    } catch (err) {
      console.error('Error fetching tools:', err);
      toast.error(t('errors.load'));
    } finally {
      setLoading(false);
    }
  };

  const handleCreateTool = async (e) => {
    e.preventDefault();
    try {
      setSubmitting(true);
      const payload = {
        ...formData,
        configuration: formData.configuration ? JSON.parse(formData.configuration) : {},
      };
      await toolService.create(payload);
      setShowCreateModal(false);
      resetForm();
      fetchTools();
      toast.success(t('success.created'));
    } catch (err) {
      console.error('Error creating tool:', err);
      toast.error(t('errors.create'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleUpdateTool = async (e) => {
    e.preventDefault();
    try {
      setSubmitting(true);
      const payload = {
        ...formData,
        configuration: formData.configuration ? JSON.parse(formData.configuration) : {},
      };
      await toolService.update(selectedTool.id, payload);
      setShowEditModal(false);
      resetForm();
      fetchTools();
      toast.success(t('success.updated'));
    } catch (err) {
      console.error('Error updating tool:', err);
      toast.error(t('errors.update'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteTool = async () => {
    try {
      setSubmitting(true);
      await toolService.delete(selectedTool.id);
      setShowDeleteModal(false);
      setSelectedTool(null);
      fetchTools();
      toast.success(t('success.deleted'));
    } catch (err) {
      console.error('Error deleting tool:', err);
      toast.error(t('errors.delete'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleTestTool = async (tool) => {
    try {
      toast.info(t('testing'));
      await toolService.test(tool.id, {});
      toast.success(t('success.tested', { name: tool.name }));
    } catch (err) {
      console.error('Error testing tool:', err);
      toast.error(t('errors.test', { detail: err.response?.data?.detail || 'Unknown error' }));
    }
  };

  const openEditModal = (tool) => {
    setSelectedTool(tool);
    setFormData({
      name: tool.name,
      description: tool.description || '',
      tool_type: tool.tool_type || 'api',
      configuration: JSON.stringify(tool.configuration || {}, null, 2),
      authentication_required: tool.authentication_required || false,
    });
    setShowEditModal(true);
  };

  const openDeleteModal = (tool) => {
    setSelectedTool(tool);
    setShowDeleteModal(true);
  };

  const resetForm = () => {
    setFormData({
      name: '',
      description: '',
      tool_type: 'api',
      configuration: '',
      authentication_required: false,
    });
    setSelectedTool(null);
  };

  const filteredTools = tools.filter(
    (tool) =>
      tool.name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      tool.description?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      tool.tool_type?.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const getToolTypeBadge = (type) => {
    const variants = {
      api: 'primary',
      function: 'success',
      database: 'info',
      external: 'warning',
    };
    return <Badge bg={variants[type] || 'secondary'}>{type || 'unknown'}</Badge>;
  };

  return (
    <Layout>
      <header className="ap-page-header">
        <div>
          <h1 className="ap-page-title">{t('title')}</h1>
          <p className="ap-page-subtitle">{t('subtitle')}</p>
        </div>
        <div className="ap-page-actions">
          <button
            type="button"
            className="ap-btn-primary"
            onClick={() => setShowCreateModal(true)}
          >
            <FaPlus size={12} />
            {t('createTool')}
          </button>
        </div>
      </header>

      <div className="ap-search-wrap mb-4" style={{ maxWidth: 'none' }}>
        <FaSearch size={14} />
        <input
          type="text"
          className="ap-search-input"
          placeholder={t('searchPlaceholder')}
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
        />
      </div>

      {loading ? (
        <LoadingSpinner text={t('loading')} />
      ) : filteredTools.length === 0 ? (
        <EmptyState
          icon={FaTools}
          title={searchTerm ? t('noToolsFound') : t('noToolsYet')}
          description={
            searchTerm
              ? t('tryAdjusting')
              : t('getStarted')
          }
          action={
            !searchTerm && (
              <button
                type="button"
                className="ap-btn-primary"
                onClick={() => setShowCreateModal(true)}
              >
                <FaPlus size={12} />
                {t('createFirst')}
              </button>
            )
          }
        />
      ) : (
        <article className="ap-card">
          <Table hover responsive className="ap-table mb-0">
            <thead>
              <tr>
                <th>{t('table.name')}</th>
                <th>{t('table.description')}</th>
                <th>{t('table.type')}</th>
                <th>{t('table.authRequired')}</th>
                <th>{t('table.created')}</th>
                <th className="text-end">{t('table.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {filteredTools.map((tool) => (
                <tr key={tool.id}>
                  <td>
                    <div className="d-flex align-items-center gap-2">
                      <FaTools size={14} aria-hidden="true" />
                      <strong>{tool.name}</strong>
                    </div>
                  </td>
                  <td className="text-muted">{tool.description || '—'}</td>
                  <td>{getToolTypeBadge(tool.tool_type)}</td>
                  <td>
                    {tool.authentication_required ? (
                      <Badge bg="warning">{t('authValues.yes')}</Badge>
                    ) : (
                      <Badge bg="secondary">{t('authValues.no')}</Badge>
                    )}
                  </td>
                  <td className="text-muted">
                    {tool.created_at
                      ? new Date(tool.created_at).toLocaleDateString()
                      : '—'}
                  </td>
                  <td>
                    <div className="d-flex justify-content-end gap-2">
                      <button
                        type="button"
                        className="ap-btn-ghost ap-btn-sm"
                        onClick={() => handleTestTool(tool)}
                        title="Test tool"
                      >
                        <FaPlayCircle size={14} />
                      </button>
                      <button
                        type="button"
                        className="ap-btn-ghost ap-btn-sm"
                        onClick={() => openEditModal(tool)}
                      >
                        <FaPen size={14} />
                      </button>
                      <button
                        type="button"
                        className="ap-btn-danger ap-btn-sm"
                        onClick={() => openDeleteModal(tool)}
                      >
                        <FaTrash size={14} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </article>
      )}

      {/* Create/Edit Modal */}
      <Modal
        show={showCreateModal || showEditModal}
        onHide={() => {
          setShowCreateModal(false);
          setShowEditModal(false);
          resetForm();
        }}
        size="lg"
        centered
        className="agent-modal"
      >
        <Modal.Header closeButton>
          <Modal.Title>
            {showCreateModal ? t('modal.createTitle') : t('modal.editTitle')}
          </Modal.Title>
        </Modal.Header>
        <Form onSubmit={showCreateModal ? handleCreateTool : handleUpdateTool}>
          <Modal.Body>
            <Row>
              <Col md={8}>
                <Form.Group className="mb-3">
                  <Form.Label>{t('modal.name')}</Form.Label>
                  <Form.Control
                    type="text"
                    placeholder={t('modal.namePlaceholder')}
                    value={formData.name}
                    onChange={(e) =>
                      setFormData({ ...formData, name: e.target.value })
                    }
                    required
                  />
                </Form.Group>
              </Col>
              <Col md={4}>
                <Form.Group className="mb-3">
                  <Form.Label>{t('modal.type')}</Form.Label>
                  <Form.Select
                    value={formData.tool_type}
                    onChange={(e) =>
                      setFormData({ ...formData, tool_type: e.target.value })
                    }
                  >
                    <option value="api">API</option>
                    <option value="function">Function</option>
                    <option value="database">Database</option>
                    <option value="external">External</option>
                  </Form.Select>
                </Form.Group>
              </Col>
            </Row>

            <Form.Group className="mb-3">
              <Form.Label>{t('modal.description')}</Form.Label>
              <Form.Control
                as="textarea"
                rows={2}
                placeholder={t('modal.descriptionPlaceholder')}
                value={formData.description}
                onChange={(e) =>
                  setFormData({ ...formData, description: e.target.value })
                }
              />
            </Form.Group>

            <Form.Group className="mb-3">
              <Form.Label>{t('modal.configuration')}</Form.Label>
              <Form.Control
                as="textarea"
                rows={6}
                placeholder={'{\n  "api_key": "your-api-key",\n  "endpoint": "https://api.example.com"\n}'}
                value={formData.configuration}
                onChange={(e) =>
                  setFormData({ ...formData, configuration: e.target.value })
                }
                style={{ fontFamily: 'var(--ap-font-mono)', fontSize: 'var(--ap-fs-sm)' }}
              />
              <Form.Text className="text-muted">
                {t('modal.configurationHelp')}
              </Form.Text>
            </Form.Group>

            <Form.Group className="mb-3">
              <Form.Check
                type="checkbox"
                label={t('modal.authRequired')}
                checked={formData.authentication_required}
                onChange={(e) =>
                  setFormData({
                    ...formData,
                    authentication_required: e.target.checked,
                  })
                }
              />
            </Form.Group>
          </Modal.Body>
          <Modal.Footer>
            <button
              type="button"
              className="ap-btn-secondary"
              onClick={() => {
                setShowCreateModal(false);
                setShowEditModal(false);
                resetForm();
              }}
            >
              {t('modal.cancel')}
            </button>
            <button type="submit" className="ap-btn-primary" disabled={submitting}>
              {submitting
                ? t('modal.saving')
                : showCreateModal
                ? t('modal.createTool')
                : t('modal.saveChanges')}
            </button>
          </Modal.Footer>
        </Form>
      </Modal>

      {/* Delete Confirmation Modal */}
      <ConfirmModal
        show={showDeleteModal}
        onHide={() => {
          setShowDeleteModal(false);
          setSelectedTool(null);
        }}
        onConfirm={handleDeleteTool}
        title={t('deleteModal.title')}
        message={t('deleteModal.message', { name: selectedTool?.name })}
        confirmText={t('deleteModal.confirm')}
        cancelText={t('deleteModal.cancel')}
        variant="danger"
        confirmLoading={submitting}
      />
    </Layout>
  );
};

export default ToolsPage;
