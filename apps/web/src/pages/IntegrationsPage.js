import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Form,
  Modal,
  Nav,
  Row,
  Spinner,
  Table
} from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import {
  FaBolt,
  FaCalendarAlt,
  FaCheckCircle,
  FaCloud,
  FaCloudUploadAlt,

  FaDatabase,
  FaEdit,
  FaExclamationTriangle,
  FaEye,
  FaEyeSlash,
  FaFileUpload,
  FaKey,
  FaMicrochip,
  FaNetworkWired,
  FaPen,
  FaPlay,
  FaPlug,
  FaPlus,
  FaPlusCircle,
  FaServer,
  FaSyncAlt,
  FaTimesCircle,
  FaTrash,
  FaWhatsapp
} from 'react-icons/fa';
import { useSearchParams } from 'react-router-dom';
import Layout from '../components/Layout';
import IntegrationsPanel from '../components/IntegrationsPanel';

import WhatsAppChannelCard from '../components/WhatsAppChannelCard';
import api from '../services/api';
import connectorService from '../services/connector';
import dataPipelineService from '../services/dataPipeline';
import dataSourceService from '../services/dataSource';
import datasetService from '../services/dataset';
import datasetGroupService from '../services/datasetGroup';
import llmService from '../services/llm';
import './IntegrationsPage.css';

// ─── Constants ────────────────────────────────────────────────────────────────

const CONNECTOR_TYPES = {
  postgres: { label: 'PostgreSQL', icon: '🐘', color: '#336791' },
  mysql: { label: 'MySQL', icon: '🐬', color: '#00758F' },
  s3: { label: 'Amazon S3', icon: '📦', color: '#FF9900' },
  gcs: { label: 'Google Cloud Storage', icon: '☁️', color: '#4285F4' },
  api: { label: 'REST API', icon: '🔗', color: '#6C757D' }
};

const CONNECTOR_FIELDS = {
  postgres: [
    { name: 'host', label: 'Host', type: 'text', required: true },
    { name: 'port', label: 'Port', type: 'number', placeholder: '5432' },
    { name: 'database', label: 'Database', type: 'text', required: true },
    { name: 'user', label: 'Username', type: 'text', required: true },
    { name: 'password', label: 'Password', type: 'password', required: true }
  ],
  mysql: [
    { name: 'host', label: 'Host', type: 'text', required: true },
    { name: 'port', label: 'Port', type: 'number', placeholder: '3306' },
    { name: 'database', label: 'Database', type: 'text', required: true },
    { name: 'user', label: 'Username', type: 'text', required: true },
    { name: 'password', label: 'Password', type: 'password', required: true }
  ],
  s3: [
    { name: 'bucket', label: 'Bucket Name', type: 'text', required: true },
    { name: 'region', label: 'Region', type: 'text', placeholder: 'us-east-1' },
    { name: 'access_key', label: 'Access Key ID', type: 'text', required: true },
    { name: 'secret_key', label: 'Secret Access Key', type: 'password', required: true }
  ],
  gcs: [
    { name: 'bucket', label: 'Bucket Name', type: 'text', required: true },
    { name: 'project_id', label: 'Project ID', type: 'text', required: true }
  ],
  api: [
    { name: 'base_url', label: 'Base URL', type: 'text', required: true },
    { name: 'auth_type', label: 'Auth Type', type: 'select', options: ['none', 'api_key', 'bearer', 'jwt'] },
    { name: 'api_key', label: 'API Key', type: 'password' }
  ]
};

const TAB_KEYS = ['integrations', 'connectors', 'data-sources', 'datasets', 'ai-models', 'skills'];

// ─── Main Component ───────────────────────────────────────────────────────────

const IntegrationsPage = () => {
  const { t } = useTranslation('integrations');
  const [searchParams, setSearchParams] = useSearchParams();
  const initialTab = TAB_KEYS.includes(searchParams.get('tab')) ? searchParams.get('tab') : 'integrations';
  const [activeTab, setActiveTab] = useState(initialTab);

  // ── Shared state ──
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  // ── Connectors state ──
  const [connectors, setConnectors] = useState([]);
  const [syncs, setSyncs] = useState([]);
  const [connectorsLoading, setConnectorsLoading] = useState(true);
  const [showConnectorModal, setShowConnectorModal] = useState(false);
  const [showSyncModal, setShowSyncModal] = useState(false);
  const [editingConnector, setEditingConnector] = useState(null);
  const [connectorForm, setConnectorForm] = useState({ name: '', description: '', type: 'postgres', config: {} });
  const [syncForm, setSyncForm] = useState({ connector_id: '', table_name: '', frequency: 'daily', mode: 'full' });
  const [testing, setTesting] = useState(null);
  const [syncing, setSyncing] = useState(null);
  const [testResult, setTestResult] = useState(null);
  const [saving, setSaving] = useState(false);

  // ── Data Sources state ──
  const [dataSources, setDataSources] = useState([]);
  const [dsLoading, setDsLoading] = useState(true);
  const [showDsModal, setShowDsModal] = useState(false);
  const [editingDsId, setEditingDsId] = useState(null);
  const [dsForm, setDsForm] = useState({ name: '', type: 'postgres', config: {} });
  const [dsSubmitting, setDsSubmitting] = useState(false);

  // ── Datasets state ──
  const [datasets, setDatasets] = useState([]);
  const [datasetsLoading, setDatasetsLoading] = useState(false);
  const [groups, setGroups] = useState([]);
  const [groupsLoading, setGroupsLoading] = useState(false);
  const [datasetSubTab, setDatasetSubTab] = useState('datasets');
  const [showUpload, setShowUpload] = useState(false);
  const [uploadState, setUploadState] = useState({ name: '', description: '', file: null });
  const [uploadLoading, setUploadLoading] = useState(false);
  const [showGroupModal, setShowGroupModal] = useState(false);
  const [groupState, setGroupState] = useState({ name: '', description: '', dataset_ids: [] });
  const [groupSubmitLoading, setGroupSubmitLoading] = useState(false);
  const [previewDataset, setPreviewDataset] = useState(null);
  const [previewData, setPreviewData] = useState(null);
  const [summaryData, setSummaryData] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [summaryLoading, setSummaryLoading] = useState(false);

  // ── Gemini CLI state ──
  const [showGeminiModal, setShowGeminiModal] = useState(false);
  const [geminiCode, setGeminiCode] = useState('');
  const [geminiSubmitting, setGeminiSubmitting] = useState(false);

  const handleGeminiLogin = async (e) => {
    e.preventDefault();
    if (!geminiCode) return;
    
    try {
      setGeminiSubmitting(true);
      await api.post('/oauth/gemini-cli/login', { code: geminiCode });
      setSuccess("Gemini CLI connected successfully!");
      setShowGeminiModal(false);
      setGeminiCode('');
      // Refresh integrations status if needed
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to connect Gemini CLI");
    } finally {
      setGeminiSubmitting(false);
    }
  };

  // ── AI Models (LLM Providers) state ──
  const [llmProviders, setLlmProviders] = useState([]);
  const [llmLoading, setLlmLoading] = useState(true);
  const [llmApiKeys, setLlmApiKeys] = useState({});
  const [llmShowKeys, setLlmShowKeys] = useState({});
  const [llmSaving, setLlmSaving] = useState(null);
  const [llmSaveSuccess, setLlmSaveSuccess] = useState({});

  // ── Tab change handler ──
  const handleTabChange = (tab) => {
    setActiveTab(tab);
    setSearchParams({ tab });
    setError(null);
    setSuccess(null);
  };

  // ── Fetch on mount ──
  useEffect(() => {
    fetchConnectors();
    fetchDataSources();
    fetchDatasets();
    fetchGroups();
    fetchLlmProviders();
  }, []);

  // ═══════════════════════════════════════════════════════════════════════════
  // CONNECTORS logic
  // ═══════════════════════════════════════════════════════════════════════════

  const fetchConnectors = async () => {
    try {
      setConnectorsLoading(true);
      const [connectorsRes, syncsRes] = await Promise.all([
        connectorService.getAll(),
        dataPipelineService.getAll().catch(() => ({ data: [] }))
      ]);
      setConnectors(connectorsRes.data || []);
      setSyncs((syncsRes.data || []).filter(s => s.config?.type === 'connector_sync'));
    } catch (err) {
      console.error(err);
    } finally {
      setConnectorsLoading(false);
    }
  };

  const connectorStats = {
    total: connectors.length,
    active: connectors.filter(c => c.status === 'active').length,
    error: connectors.filter(c => c.status === 'error').length,
    syncsActive: syncs.filter(s => s.config?.is_active !== false).length
  };

  const handleOpenConnectorModal = (connector = null) => {
    if (connector) {
      setEditingConnector(connector);
      setConnectorForm({ name: connector.name, description: connector.description || '', type: connector.type, config: connector.config || {} });
    } else {
      setEditingConnector(null);
      setConnectorForm({ name: '', description: '', type: 'snowflake', config: {} });
    }
    setTestResult(null);
    setShowConnectorModal(true);
  };

  const handleSaveConnector = async () => {
    try {
      setSaving(true);
      if (editingConnector) {
        await connectorService.update(editingConnector.id, connectorForm);
        setSuccess(t('connectors.success.updated'));
      } else {
        await connectorService.create(connectorForm);
        setSuccess(t('connectors.success.created'));
      }
      setShowConnectorModal(false);
      fetchConnectors();
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(t('connectors.errors.save'));
    } finally {
      setSaving(false);
    }
  };

  const handleTestConnector = async (connectorId = null) => {
    try {
      if (connectorId) {
        setTesting(connectorId);
        await connectorService.testExisting(connectorId);
        setSuccess(t('connectors.success.testPassed'));
        fetchConnectors();
      } else {
        setTesting(true);
        const res = await connectorService.testConnection(connectorForm.type, connectorForm.config);
        setTestResult(res.data);
      }
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(t('connectors.errors.test'));
      if (!connectorId) setTestResult({ success: false, message: err.response?.data?.detail || 'Connection failed' });
    } finally {
      setTesting(null);
    }
  };

  const handleDeleteConnector = async (id) => {
    if (window.confirm(t('connectors.errors.deleteConfirm'))) {
      try {
        await connectorService.delete(id);
        setSuccess(t('connectors.success.deleted'));
        fetchConnectors();
        setTimeout(() => setSuccess(null), 3000);
      } catch (err) {
        setError(t('connectors.errors.delete'));
      }
    }
  };

  const handleOpenSyncModal = (connector = null) => {
    setSyncForm({ connector_id: connector?.id || '', table_name: '', frequency: 'daily', mode: 'full' });
    setShowSyncModal(true);
  };

  const handleCreateSync = async () => {
    try {
      setSaving(true);
      const connector = connectors.find(c => c.id === syncForm.connector_id);
      await dataPipelineService.create({
        name: `Sync: ${connector?.name || 'Unknown'} - ${syncForm.table_name}`,
        config: { type: 'connector_sync', connector_id: syncForm.connector_id, table_name: syncForm.table_name, frequency: syncForm.frequency, mode: syncForm.mode }
      });
      setSuccess(t('connectors.success.syncCreated'));
      setShowSyncModal(false);
      fetchConnectors();
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(t('connectors.errors.syncCreate'));
    } finally {
      setSaving(false);
    }
  };

  const handleRunSync = async (syncId) => {
    try {
      setSyncing(syncId);
      await dataPipelineService.execute(syncId);
      setSuccess(t('connectors.success.syncStarted'));
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(t('connectors.errors.syncStart'));
    } finally {
      setSyncing(null);
    }
  };

  const getStatusBadge = (status) => {
    const configs = {
      active: { bg: 'success', icon: FaCheckCircle, text: t('connectors.status.active') },
      error: { bg: 'danger', icon: FaTimesCircle, text: t('connectors.status.error') },
      pending: { bg: 'warning', icon: FaExclamationTriangle, text: t('connectors.status.pending') }
    };
    const config = configs[status] || configs.pending;
    return <Badge bg={config.bg}><config.icon className="me-1" size={10} />{config.text}</Badge>;
  };

  // ═══════════════════════════════════════════════════════════════════════════
  // DATA SOURCES logic
  // ═══════════════════════════════════════════════════════════════════════════

  const fetchDataSources = async () => {
    try {
      setDsLoading(true);
      const response = await dataSourceService.getAll();
      setDataSources(response.data);
    } catch (err) {
      console.error('Error fetching data sources:', err);
    } finally {
      setDsLoading(false);
    }
  };

  const handleShowDsModal = (dataSource = null) => {
    if (dataSource) {
      setEditingDsId(dataSource.id);
      setDsForm({ name: dataSource.name, type: dataSource.type, config: dataSource.config || {} });
    } else {
      setEditingDsId(null);
      setDsForm({ name: '', type: 'postgres', config: {} });
    }
    setShowDsModal(true);
  };

  const handleDsConfigChange = (key, value) => {
    setDsForm(prev => ({ ...prev, config: { ...prev.config, [key]: value } }));
  };

  const handleDsSubmit = async (e) => {
    e.preventDefault();
    try {
      setDsSubmitting(true);
      if (editingDsId) {
        await dataSourceService.update(editingDsId, dsForm);
        setSuccess(t('dataSources.success.updated'));
      } else {
        await dataSourceService.create(dsForm);
        setSuccess(t('dataSources.success.created'));
      }
      fetchDataSources();
      setShowDsModal(false);
      setEditingDsId(null);
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(t('dataSources.errors.save'));
    } finally {
      setDsSubmitting(false);
    }
  };

  const handleDeleteDs = async (id) => {
    if (window.confirm(t('dataSources.errors.deleteConfirm'))) {
      try {
        await dataSourceService.remove(id);
        setSuccess(t('dataSources.success.deleted'));
        fetchDataSources();
        setTimeout(() => setSuccess(null), 3000);
      } catch (err) {
        setError(t('dataSources.errors.delete'));
      }
    }
  };

  const getDsTypeIcon = (type) => {
    switch (type) {
      case 'postgres': return <FaDatabase size={24} className="text-primary" />;
      case 'rest_api': case 'api': return <FaNetworkWired size={24} className="text-success" />;
      default: return <FaServer size={24} className="text-secondary" />;
    }
  };

  const renderDsConfigFields = () => {
    switch (dsForm.type) {
      case 'postgres':
        return (
          <>
            <Row>
              <Col md={8}>
                <Form.Group className="mb-3">
                  <Form.Label>{t('dataSources.fields.host')}</Form.Label>
                  <Form.Control type="text" placeholder="localhost" value={dsForm.config.host || ''} onChange={(e) => handleDsConfigChange('host', e.target.value)} required />
                </Form.Group>
              </Col>
              <Col md={4}>
                <Form.Group className="mb-3">
                  <Form.Label>{t('dataSources.fields.port')}</Form.Label>
                  <Form.Control type="number" placeholder="5432" value={dsForm.config.port || ''} onChange={(e) => handleDsConfigChange('port', e.target.value)} required />
                </Form.Group>
              </Col>
            </Row>
            <Form.Group className="mb-3">
              <Form.Label>{t('dataSources.fields.database')}</Form.Label>
              <Form.Control type="text" value={dsForm.config.database || ''} onChange={(e) => handleDsConfigChange('database', e.target.value)} required />
            </Form.Group>
            <Row>
              <Col md={6}>
                <Form.Group className="mb-3">
                  <Form.Label>{t('dataSources.fields.username')}</Form.Label>
                  <Form.Control type="text" value={dsForm.config.username || ''} onChange={(e) => handleDsConfigChange('username', e.target.value)} required />
                </Form.Group>
              </Col>
              <Col md={6}>
                <Form.Group className="mb-3">
                  <Form.Label>{t('dataSources.fields.password')}</Form.Label>
                  <Form.Control type="password" value={dsForm.config.password || ''} onChange={(e) => handleDsConfigChange('password', e.target.value)} required />
                </Form.Group>
              </Col>
            </Row>
          </>
        );
      default:
        return (
          <Form.Group className="mb-3">
            <Form.Label>{t('dataSources.fields.configJson')}</Form.Label>
            <Form.Control
              as="textarea"
              rows={5}
              value={JSON.stringify(dsForm.config, null, 2)}
              onChange={(e) => {
                try { setDsForm(prev => ({ ...prev, config: JSON.parse(e.target.value) })); } catch (err) { /* allow invalid JSON while typing */ }
              }}
            />
          </Form.Group>
        );
    }
  };

  // ═══════════════════════════════════════════════════════════════════════════
  // DATASETS logic
  // ═══════════════════════════════════════════════════════════════════════════

  const fetchDatasets = async () => {
    setDatasetsLoading(true);
    try {
      const response = await datasetService.getAll();
      setDatasets(response.data);
    } catch (err) {
      console.error(err);
    } finally {
      setDatasetsLoading(false);
    }
  };

  const fetchGroups = async () => {
    setGroupsLoading(true);
    try {
      const response = await datasetGroupService.getAll();
      setGroups(response.data);
    } catch (err) {
      console.error(err);
    } finally {
      setGroupsLoading(false);
    }
  };

  const openPreview = async (dataset) => {
    setPreviewDataset(dataset);
    setPreviewLoading(true);
    setSummaryLoading(true);
    setPreviewData(null);
    setSummaryData(null);
    try {
      const [previewResp, summaryResp] = await Promise.all([
        datasetService.getPreview(dataset.id),
        datasetService.getSummary(dataset.id),
      ]);
      setPreviewData(previewResp.data);
      setSummaryData(summaryResp.data);
    } catch (err) {
      console.error(err);
    } finally {
      setPreviewLoading(false);
      setSummaryLoading(false);
    }
  };

  const handleUploadSubmit = async (event) => {
    event.preventDefault();
    if (!uploadState.file) return;
    const formData = new FormData();
    formData.append('file', uploadState.file);
    formData.append('name', uploadState.name || uploadState.file.name);
    if (uploadState.description) formData.append('description', uploadState.description);
    setUploadLoading(true);
    try {
      await datasetService.upload(formData);
      setShowUpload(false);
      setUploadState({ name: '', description: '', file: null });
      setSuccess(t('datasets.success.uploaded'));
      await fetchDatasets();
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(t('datasets.errors.upload'));
    } finally {
      setUploadLoading(false);
    }
  };

  const handleGroupSubmit = async (event) => {
    event.preventDefault();
    if (groupState.dataset_ids.length < 2) { setError(t('datasets.errors.groupMin')); return; }
    setGroupSubmitLoading(true);
    try {
      await datasetGroupService.create(groupState);
      setShowGroupModal(false);
      setGroupState({ name: '', description: '', dataset_ids: [] });
      setSuccess(t('datasets.success.groupCreated'));
      await fetchGroups();
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(t('datasets.errors.groupCreate'));
    } finally {
      setGroupSubmitLoading(false);
    }
  };

  const renderPreviewTable = useMemo(() => {
    if (!previewData?.sample_rows?.length) return <p className="text-muted">{t('datasets.noPreview')}</p>;
    const columns = Object.keys(previewData.sample_rows[0]);
    return (
      <Table striped bordered hover responsive size="sm" className="mt-3">
        <thead><tr>{columns.map(col => <th key={col}>{col}</th>)}</tr></thead>
        <tbody>{previewData.sample_rows.map((row, i) => <tr key={i}>{columns.map(col => <td key={col}>{row[col]}</td>)}</tr>)}</tbody>
      </Table>
    );
  }, [previewData]);

  const renderSummaryCards = useMemo(() => {
    if (!summaryData?.numeric_columns?.length) return <p className="text-muted">{t('datasets.noSummary')}</p>;
    return (
      <Row className="g-3 mt-1">
        {summaryData.numeric_columns.map(metric => (
          <Col md={4} key={metric.column}>
            <Card className="h-100" style={{ background: 'var(--surface-contrast)', border: '1px solid var(--color-border)' }}>
              <Card.Body>
                <Card.Title className="small">{metric.column}</Card.Title>
                <div className="small"><strong>Avg:</strong> {metric.avg ?? '...'}</div>
                <div className="small"><strong>Min:</strong> {metric.min ?? '...'}</div>
                <div className="small"><strong>Max:</strong> {metric.max ?? '...'}</div>
              </Card.Body>
            </Card>
          </Col>
        ))}
      </Row>
    );
  }, [summaryData]);

  // ═══════════════════════════════════════════════════════════════════════════
  // RENDER: Connector Form fields
  // ═══════════════════════════════════════════════════════════════════════════

  const renderConnectorForm = () => {
    const fields = CONNECTOR_FIELDS[connectorForm.type] || [];
    return fields.map(field => (
      <Form.Group key={field.name} className="mb-3">
        <Form.Label>{field.label}{field.required && <span className="text-danger">*</span>}</Form.Label>
        {field.type === 'select' ? (
          <Form.Select
            value={connectorForm.config[field.name] || ''}
            onChange={(e) => setConnectorForm(prev => ({ ...prev, config: { ...prev.config, [field.name]: e.target.value } }))}
          >
            <option value="">Select...</option>
            {field.options?.map(opt => <option key={opt} value={opt}>{opt}</option>)}
          </Form.Select>
        ) : (
          <Form.Control
            type={field.type}
            placeholder={field.placeholder}
            value={connectorForm.config[field.name] || ''}
            onChange={(e) => setConnectorForm(prev => ({ ...prev, config: { ...prev.config, [field.name]: e.target.value } }))}
            required={field.required}
          />
        )}
      </Form.Group>
    ));
  };

  // ═══════════════════════════════════════════════════════════════════════════
  // AI MODELS (LLM Providers) logic
  // ═══════════════════════════════════════════════════════════════════════════

  const fetchLlmProviders = async () => {
    try {
      setLlmLoading(true);
      const data = await llmService.getProviderStatus();
      setLlmProviders(data);
    } catch (err) {
      console.error('Failed to load LLM providers:', err);
    } finally {
      setLlmLoading(false);
    }
  };

  const handleLlmKeyChange = (providerName, value) => {
    setLlmApiKeys(prev => ({ ...prev, [providerName]: value }));
    setLlmSaveSuccess(prev => ({ ...prev, [providerName]: false }));
  };

  const handleLlmSaveKey = async (providerName) => {
    const key = llmApiKeys[providerName];
    if (!key) return;
    try {
      setLlmSaving(providerName);
      await llmService.setProviderKey(providerName, key);
      setLlmSaveSuccess(prev => ({ ...prev, [providerName]: true }));
      setLlmApiKeys(prev => ({ ...prev, [providerName]: '' }));
      await fetchLlmProviders();
    } catch (err) {
      setError(t('aiModels.errors.saveKey', { provider: providerName }));
    } finally {
      setLlmSaving(null);
    }
  };

  // ═══════════════════════════════════════════════════════════════════════════
  // TAB CONTENT: Skills
  // ═══════════════════════════════════════════════════════════════════════════

  const renderSkillsTab = () => (
    <div className="tab-content-inner">
      <IntegrationsPanel />
    </div>
  );

  // ═══════════════════════════════════════════════════════════════════════════
  // TAB CONTENT: Connectors
  // ═══════════════════════════════════════════════════════════════════════════

  const renderConnectorsTab = () => (
    <div className="tab-content-inner">
      {/* Stats */}
      <Row className="g-4 mb-4">
        <Col md={3}>
          <Card className="stat-card stat-total">
            <Card.Body>
              <div className="stat-icon"><FaDatabase size={24} /></div>
              <div className="stat-content">
                <div className="stat-value">{connectorStats.total}</div>
                <div className="stat-label">{t('connectors.total')}</div>
              </div>
            </Card.Body>
          </Card>
        </Col>
        <Col md={3}>
          <Card className="stat-card stat-active">
            <Card.Body>
              <div className="stat-icon"><FaCheckCircle size={24} /></div>
              <div className="stat-content">
                <div className="stat-value">{connectorStats.active}</div>
                <div className="stat-label">{t('connectors.active')}</div>
              </div>
            </Card.Body>
          </Card>
        </Col>
        <Col md={3}>
          <Card className="stat-card stat-syncs">
            <Card.Body>
              <div className="stat-icon"><FaSyncAlt size={24} /></div>
              <div className="stat-content">
                <div className="stat-value">{connectorStats.syncsActive}</div>
                <div className="stat-label">{t('connectors.activeSyncs')}</div>
              </div>
            </Card.Body>
          </Card>
        </Col>
        <Col md={3}>
          <Card className="stat-card stat-error">
            <Card.Body>
              <div className="stat-icon"><FaExclamationTriangle size={24} /></div>
              <div className="stat-content">
                <div className="stat-value">{connectorStats.error}</div>
                <div className="stat-label">{t('connectors.needAttention')}</div>
              </div>
            </Card.Body>
          </Card>
        </Col>
      </Row>

      {connectorsLoading ? (
        <div className="text-center py-5"><Spinner animation="border" variant="primary" /></div>
      ) : (
        <Row className="g-4">
          <Col lg={8}>
            <Card className="activity-card">
              <Card.Header className="d-flex justify-content-between align-items-center">
                <h5 className="mb-0"><FaBolt className="me-2" />{t('connectors.connectedSystems')}</h5>
                <Button variant="primary" size="sm" onClick={() => handleOpenConnectorModal()}>
                  <FaPlus className="me-2" />{t('connectors.addConnector')}
                </Button>
              </Card.Header>
              <Card.Body className="p-0">
                {connectors.length === 0 ? (
                  <div className="text-center py-5">
                    <FaCloudUploadAlt size={48} className="text-muted mb-3" />
                    <h5>{t('connectors.noConnectors')}</h5>
                    <p className="text-muted">{t('connectors.noConnectorsDesc')}</p>
                    <Button variant="primary" onClick={() => handleOpenConnectorModal()}>
                      <FaPlus className="me-2" />{t('connectors.connectFirst')}
                    </Button>
                  </div>
                ) : (
                  <Table hover className="mb-0 connectors-table">
                    <thead><tr><th>{t('connectors.source')}</th><th>Status</th><th>{t('connectors.lastTested')}</th><th className="text-end">Actions</th></tr></thead>
                    <tbody>
                      {connectors.map(connector => (
                        <tr key={connector.id}>
                          <td>
                            <div className="d-flex align-items-center">
                              <span className="connector-icon me-2">{CONNECTOR_TYPES[connector.type]?.icon || '🔌'}</span>
                              <div>
                                <div className="fw-medium">{connector.name}</div>
                                <small className="text-muted">{CONNECTOR_TYPES[connector.type]?.label}</small>
                              </div>
                            </div>
                          </td>
                          <td>{getStatusBadge(connector.status)}</td>
                          <td><small className="text-muted">{connector.last_test_at ? new Date(connector.last_test_at).toLocaleDateString() : t('connectors.never')}</small></td>
                          <td className="text-end">
                            <Button variant="outline-success" size="sm" className="me-1" onClick={() => handleTestConnector(connector.id)} disabled={testing === connector.id}>
                              {testing === connector.id ? <Spinner size="sm" /> : <FaPlay />}
                            </Button>
                            <Button variant="outline-primary" size="sm" className="me-1" onClick={() => handleOpenSyncModal(connector)} disabled={connector.status !== 'active'}>
                              <FaSyncAlt />
                            </Button>
                            <Button variant="outline-secondary" size="sm" className="me-1" onClick={() => handleOpenConnectorModal(connector)}>
                              <FaPen />
                            </Button>
                            <Button variant="outline-danger" size="sm" onClick={() => handleDeleteConnector(connector.id)}>
                              <FaTrash />
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                )}
              </Card.Body>
            </Card>
          </Col>
          <Col lg={4}>
            <Card className="syncs-card">
              <Card.Header><h5 className="mb-0"><FaCalendarAlt className="me-2" />{t('connectors.dataSyncs')}</h5></Card.Header>
              <Card.Body className="p-0">
                {syncs.length === 0 ? (
                  <div className="text-center py-4">
                    <FaSyncAlt size={32} className="text-muted mb-2" />
                    <p className="text-muted mb-0 small">{t('connectors.noSyncsScheduled')}</p>
                  </div>
                ) : (
                  <div className="syncs-list">
                    {syncs.slice(0, 5).map(sync => (
                      <div key={sync.id} className="sync-item">
                        <div className="sync-info">
                          <div className="sync-name">{sync.name}</div>
                          <small className="text-muted">{sync.config?.frequency || 'Manual'} &bull; {sync.config?.mode || 'Full'}</small>
                        </div>
                        <Button variant="link" size="sm" onClick={() => handleRunSync(sync.id)} disabled={syncing === sync.id}>
                          {syncing === sync.id ? <Spinner size="sm" /> : <FaPlay />}
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </Card.Body>
            </Card>
          </Col>
        </Row>
      )}
    </div>
  );

  // ═══════════════════════════════════════════════════════════════════════════
  // TAB CONTENT: Data Sources
  // ═══════════════════════════════════════════════════════════════════════════

  const renderDataSourcesTab = () => (
    <div className="tab-content-inner">
      <div className="d-flex justify-content-between align-items-center mb-4">
        <p className="text-muted mb-0">{t('dataSources.subtitle')}</p>
        <Button variant="primary" onClick={() => handleShowDsModal()}>
          <FaPlusCircle className="me-2" />{t('dataSources.addSource')}
        </Button>
      </div>

      {dsLoading ? (
        <div className="text-center py-5"><Spinner animation="border" variant="primary" /></div>
      ) : (
        <Row xs={1} md={2} lg={3} className="g-4">
          {dataSources.map(ds => (
            <Col key={ds.id}>
              <Card className="h-100 datasource-card">
                <Card.Body>
                  <div className="d-flex justify-content-between align-items-start mb-3">
                    <div className="datasource-icon-wrapper">{getDsTypeIcon(ds.type)}</div>
                    <div>
                      <Button variant="link" className="text-primary p-0 me-2" onClick={() => handleShowDsModal(ds)}><FaEdit size={16} /></Button>
                      <Button variant="link" className="text-danger p-0" onClick={() => handleDeleteDs(ds.id)}><FaTrash size={16} /></Button>
                    </div>
                  </div>
                  <Card.Title>{ds.name}</Card.Title>
                  <div className="mb-2">
                    <Badge className="border" style={{ background: 'var(--surface-contrast)', color: 'var(--color-soft)', borderColor: 'var(--color-border)' }}>
                      {ds.type}
                    </Badge>
                  </div>
                  <Card.Text className="text-muted small">
                    {ds.config?.host ? <span className="text-truncate d-block" title={ds.config.host}>Host: {ds.config.host}</span>
                      : ds.config?.base_url ? <span className="text-truncate d-block" title={ds.config.base_url}>{ds.config.base_url}</span>
                        : <span>Configured</span>}
                  </Card.Text>
                  <div className="mt-3 pt-3 border-top">
                    <div className="d-flex align-items-center text-success small">
                      <FaCheckCircle className="me-1" />{t('dataSources.connected')}
                    </div>
                  </div>
                </Card.Body>
              </Card>
            </Col>
          ))}
          {dataSources.length === 0 && (
            <Col xs={12}>
              <div className="text-center py-5 text-muted">
                <FaDatabase size={48} className="mb-3 opacity-50" />
                <h5>{t('dataSources.noSources')}</h5>
                <p>{t('dataSources.noSourcesDesc')}</p>
              </div>
            </Col>
          )}
        </Row>
      )}
    </div>
  );

  // ═══════════════════════════════════════════════════════════════════════════
  // TAB CONTENT: Datasets
  // ═══════════════════════════════════════════════════════════════════════════

  const renderDatasetsTab = () => (
    <div className="tab-content-inner">
      <div className="d-flex justify-content-between align-items-center mb-4">
        <Nav variant="pills" activeKey={datasetSubTab} onSelect={setDatasetSubTab}>
          <Nav.Item><Nav.Link eventKey="datasets">{t('datasets.subTabs.datasets')}</Nav.Link></Nav.Item>
          <Nav.Item><Nav.Link eventKey="groups">{t('datasets.subTabs.groups')}</Nav.Link></Nav.Item>
        </Nav>
        <div>
          {datasetSubTab === 'datasets' && (
            <Button variant="primary" onClick={() => setShowUpload(true)}>
              <FaFileUpload className="me-2" />{t('datasets.upload')}
            </Button>
          )}
          {datasetSubTab === 'groups' && (
            <Button variant="primary" onClick={() => setShowGroupModal(true)}>
              <FaPlus className="me-2" />{t('datasets.createGroup')}
            </Button>
          )}
        </div>
      </div>

      {datasetSubTab === 'datasets' && (
        <>
          {datasetsLoading ? (
            <div className="text-center py-4"><Spinner animation="border" size="sm" /> {t('datasets.loadingDatasets')}</div>
          ) : (
            <Table striped bordered hover responsive>
              <thead>
                <tr>
                  <th>{t('datasets.table.name')}</th>
                  <th>{t('datasets.table.description')}</th>
                  <th>{t('datasets.table.rows')}</th>
                  <th>{t('datasets.table.sync')}</th>
                  <th>{t('datasets.table.created')}</th>
                  <th>{t('datasets.table.actions')}</th>
                </tr>
              </thead>
              <tbody>
                {datasets.map(dataset => (
                  <tr key={dataset.id}>
                    <td>{dataset.name}</td>
                    <td>{dataset.description}</td>
                    <td>{dataset.row_count}</td>
                    <td><Badge bg="secondary">{dataset.metadata?.sync_status || 'unknown'}</Badge></td>
                    <td>{dataset.created_at ? new Date(dataset.created_at).toLocaleString() : '...'}</td>
                    <td>
                      <Button variant="info" size="sm" onClick={() => openPreview(dataset)} className="me-2">{t('datasets.preview')}</Button>
                      <Button
                        variant="outline-primary"
                        size="sm"
                        onClick={async () => {
                          try { await datasetService.sync(dataset.id); fetchDatasets(); } catch (e) { setError(t('datasets.syncError')); }
                        }}
                        disabled={dataset.metadata?.sync_status === 'syncing'}
                      >
                        {dataset.metadata?.sync_status === 'syncing' ? t('datasets.syncing') : t('datasets.sync')}
                      </Button>
                    </td>
                  </tr>
                ))}
                {datasets.length === 0 && !datasetsLoading && (
                  <tr><td colSpan={6} className="text-center text-muted">{t('datasets.noDatasets')}</td></tr>
                )}
              </tbody>
            </Table>
          )}
        </>
      )}

      {datasetSubTab === 'groups' && (
        <>
          {groupsLoading ? (
            <div className="text-center py-4"><Spinner animation="border" size="sm" /> {t('datasets.loadingGroups')}</div>
          ) : (
            <Table striped bordered hover responsive>
              <thead><tr><th>Name</th><th>Description</th><th>Datasets</th><th>Created</th></tr></thead>
              <tbody>
                {groups.map(group => (
                  <tr key={group.id}>
                    <td>{group.name}</td>
                    <td>{group.description}</td>
                    <td><Badge bg="secondary">{group.datasets ? group.datasets.length : 0}</Badge></td>
                    <td>{group.created_at ? new Date(group.created_at).toLocaleString() : '...'}</td>
                  </tr>
                ))}
                {groups.length === 0 && !groupsLoading && (
                  <tr><td colSpan={4} className="text-center text-muted">{t('datasets.noGroups')}</td></tr>
                )}
              </tbody>
            </Table>
          )}
        </>
      )}
    </div>
  );

  // ═══════════════════════════════════════════════════════════════════════════
  // RENDER: Page
  // ═══════════════════════════════════════════════════════════════════════════

  return (
    <Layout>
      <div className="integrations-page">
        <div className="page-header mb-4">
          <div>
            <h1 className="page-title">
              <FaPlug className="me-2" />
              {t('title')}
            </h1>
            <p className="page-subtitle text-muted">
              {t('subtitle')}
            </p>
          </div>
        </div>

        {error && <Alert variant="danger" onClose={() => setError(null)} dismissible>{error}</Alert>}
        {success && <Alert variant="success" onClose={() => setSuccess(null)} dismissible>{success}</Alert>}

        {/* Tab Navigation */}
        <Nav variant="tabs" activeKey={activeTab} onSelect={handleTabChange} className="mb-4 integrations-tabs">
          <Nav.Item>
            <Nav.Link eventKey="integrations"><FaPlug className="me-2" />{t('tabs.integrations')}</Nav.Link>
          </Nav.Item>
          <Nav.Item>
            <Nav.Link eventKey="connectors"><FaBolt className="me-2" />{t('tabs.connectors')}</Nav.Link>
          </Nav.Item>
          <Nav.Item>
            <Nav.Link eventKey="data-sources"><FaDatabase className="me-2" />{t('tabs.dataSources')}</Nav.Link>
          </Nav.Item>
          <Nav.Item>
            <Nav.Link eventKey="datasets"><FaFileUpload className="me-2" />{t('tabs.datasets')}</Nav.Link>
          </Nav.Item>
          <Nav.Item>
            <Nav.Link eventKey="ai-models"><FaMicrochip className="me-2" />{t('tabs.aiModels')}</Nav.Link>
          </Nav.Item>
        </Nav>

        {/* Tab Content */}
        {activeTab === 'integrations' && renderSkillsTab()}
        {activeTab === 'connectors' && (
          <div className="tab-content-inner">
            {renderConnectorsTab()}
          </div>
        )}
        {activeTab === 'data-sources' && renderDataSourcesTab()}
        {activeTab === 'datasets' && renderDatasetsTab()}
        {activeTab === 'ai-models' && (
          <div className="tab-content-inner">
            <p className="text-muted mb-4">{t('aiModels.subtitle')}</p>
            {llmLoading ? (
              <div className="text-center py-5"><Spinner animation="border" variant="primary" /></div>
            ) : (
              <Row xs={1} md={2} lg={3} className="g-4">
                {llmProviders.map((provider) => (
                  <Col key={provider.name}>
                    <Card className="h-100" style={{ background: 'var(--surface-elevated)', border: '1px solid var(--color-border)' }}>
                      <Card.Body>
                        <div className="d-flex align-items-center justify-content-between mb-3">
                          <strong>{provider.display_name}</strong>
                          {provider.configured ? (
                            <Badge bg="success" className="bg-opacity-25 text-success border border-success">
                              <FaCheckCircle className="me-1" size={10} /> {t('aiModels.connected')}
                            </Badge>
                          ) : (
                            <Badge bg="secondary" className="bg-opacity-25 text-secondary border border-secondary">
                              {t('aiModels.notConfigured')}
                            </Badge>
                          )}
                        </div>
                        <Form.Label className="small text-muted"><FaKey className="me-1" size={10} />{t('aiModels.apiKey')}</Form.Label>
                        <div className="d-flex gap-1 mb-2">
                          <Form.Control
                            size="sm"
                            type={llmShowKeys[provider.name] ? 'text' : 'password'}
                            placeholder={provider.configured ? t('aiModels.apiKeyMasked') : t('aiModels.apiKeyPlaceholder')}
                            value={llmApiKeys[provider.name] || ''}
                            onChange={(e) => handleLlmKeyChange(provider.name, e.target.value)}
                            disabled={llmSaving === provider.name}
                          />
                          <Button
                            variant="outline-secondary"
                            size="sm"
                            onClick={() => setLlmShowKeys(prev => ({ ...prev, [provider.name]: !prev[provider.name] }))}
                          >
                            {llmShowKeys[provider.name] ? <FaEyeSlash size={12} /> : <FaEye size={12} />}
                          </Button>
                        </div>
                        {llmSaveSuccess[provider.name] && (
                          <small className="text-success d-block mb-2"><FaCheckCircle className="me-1" size={10} />{t('aiModels.keySaved')}</small>
                        )}
                        <Button
                          variant="primary"
                          size="sm"
                          className="w-100"
                          onClick={() => handleLlmSaveKey(provider.name)}
                          disabled={!llmApiKeys[provider.name] || llmSaving === provider.name}
                        >
                          {llmSaving === provider.name ? <Spinner animation="border" size="sm" /> : t('aiModels.saveKey')}
                        </Button>
                        <div className="text-center mt-2">
                          <small className="text-muted">{provider.is_openai_compatible ? t('aiModels.openaiCompatible') : t('aiModels.nativeApi')}</small>
                        </div>
                      </Card.Body>
                    </Card>
                  </Col>
                ))}
              </Row>
            )}
          </div>
        )}

        {/* ── Connector Modal ── */}
        <Modal show={showConnectorModal} onHide={() => setShowConnectorModal(false)} size="lg">
          <Modal.Header closeButton>
            <Modal.Title>{editingConnector ? t('connectors.modal.editTitle') : t('connectors.modal.createTitle')}</Modal.Title>
          </Modal.Header>
          <Modal.Body>
            <Form>
              <Row>
                <Col md={6}>
                  <Form.Group className="mb-3">
                    <Form.Label>{t('connectors.modal.name')}<span className="text-danger">*</span></Form.Label>
                    <Form.Control type="text" placeholder={t('connectors.modal.namePlaceholder')} value={connectorForm.name} onChange={(e) => setConnectorForm({ ...connectorForm, name: e.target.value })} required />
                  </Form.Group>
                </Col>
                <Col md={6}>
                  <Form.Group className="mb-3">
                    <Form.Label>{t('connectors.modal.type')}<span className="text-danger">*</span></Form.Label>
                    <Form.Select value={connectorForm.type} onChange={(e) => setConnectorForm({ ...connectorForm, type: e.target.value, config: {} })} disabled={!!editingConnector}>
                      {Object.entries(CONNECTOR_TYPES).map(([key, cfg]) => (
                        <option key={key} value={key}>{cfg.icon} {cfg.label}</option>
                      ))}
                    </Form.Select>
                  </Form.Group>
                </Col>
              </Row>
              <Form.Group className="mb-3">
                <Form.Label>{t('connectors.modal.description')}</Form.Label>
                <Form.Control as="textarea" rows={2} placeholder={t('connectors.modal.descriptionPlaceholder')} value={connectorForm.description} onChange={(e) => setConnectorForm({ ...connectorForm, description: e.target.value })} />
              </Form.Group>
              <hr />
              <h6 className="mb-3">{t('connectors.modal.connectionSettings')}</h6>
              {renderConnectorForm()}
              {testResult && (
                <Alert variant={testResult.success ? 'success' : 'danger'} className="mt-3">
                  {testResult.success ? <FaCheckCircle className="me-2" /> : <FaTimesCircle className="me-2" />}
                  {testResult.message}
                </Alert>
              )}
            </Form>
          </Modal.Body>
          <Modal.Footer>
            <Button variant="outline-secondary" onClick={() => handleTestConnector()} disabled={testing}>
              {testing ? <Spinner size="sm" className="me-2" /> : <FaPlay className="me-2" />}{t('connectors.modal.testConnection')}
            </Button>
            <Button variant="secondary" onClick={() => setShowConnectorModal(false)}>{t('connectors.modal.cancel')}</Button>
            <Button variant="primary" onClick={handleSaveConnector} disabled={saving || !connectorForm.name}>
              {saving ? <Spinner size="sm" /> : (editingConnector ? t('connectors.modal.update') : t('connectors.modal.create'))}
            </Button>
          </Modal.Footer>
        </Modal>

        {/* ── Sync Modal ── */}
        <Modal show={showSyncModal} onHide={() => setShowSyncModal(false)}>
          <Modal.Header closeButton><Modal.Title>{t('connectors.sync.title')}</Modal.Title></Modal.Header>
          <Modal.Body>
            <Form>
              <Form.Group className="mb-3">
                <Form.Label>{t('connectors.sync.connector')}<span className="text-danger">*</span></Form.Label>
                <Form.Select value={syncForm.connector_id} onChange={(e) => setSyncForm({ ...syncForm, connector_id: e.target.value })} required>
                  <option value="">{t('connectors.sync.selectConnector')}</option>
                  {connectors.filter(c => c.status === 'active').map(c => (
                    <option key={c.id} value={c.id}>{CONNECTOR_TYPES[c.type]?.icon} {c.name}</option>
                  ))}
                </Form.Select>
              </Form.Group>
              <Form.Group className="mb-3">
                <Form.Label>{t('connectors.sync.tableName')}<span className="text-danger">*</span></Form.Label>
                <Form.Control type="text" placeholder={t('connectors.sync.tablePlaceholder')} value={syncForm.table_name} onChange={(e) => setSyncForm({ ...syncForm, table_name: e.target.value })} required />
              </Form.Group>
              <Row>
                <Col>
                  <Form.Group className="mb-3">
                    <Form.Label>{t('connectors.sync.frequency')}</Form.Label>
                    <Form.Select value={syncForm.frequency} onChange={(e) => setSyncForm({ ...syncForm, frequency: e.target.value })}>
                      <option value="hourly">{t('connectors.sync.frequencyHourly')}</option>
                      <option value="daily">{t('connectors.sync.frequencyDaily')}</option>
                      <option value="weekly">{t('connectors.sync.frequencyWeekly')}</option>
                    </Form.Select>
                  </Form.Group>
                </Col>
                <Col>
                  <Form.Group className="mb-3">
                    <Form.Label>{t('connectors.sync.mode')}</Form.Label>
                    <Form.Select value={syncForm.mode} onChange={(e) => setSyncForm({ ...syncForm, mode: e.target.value })}>
                      <option value="full">{t('connectors.sync.modeFull')}</option>
                      <option value="incremental">{t('connectors.sync.modeIncremental')}</option>
                    </Form.Select>
                  </Form.Group>
                </Col>
              </Row>
            </Form>
          </Modal.Body>
          <Modal.Footer>
            <Button variant="secondary" onClick={() => setShowSyncModal(false)}>{t('connectors.sync.cancel')}</Button>
            <Button variant="primary" onClick={handleCreateSync} disabled={saving || !syncForm.connector_id || !syncForm.table_name}>
              {saving ? <Spinner size="sm" /> : t('connectors.sync.schedule')}
            </Button>
          </Modal.Footer>
        </Modal>

        {/* ── Data Source Modal ── */}
        <Modal show={showDsModal} onHide={() => { setShowDsModal(false); setEditingDsId(null); }} size="lg">
          <Modal.Header closeButton>
            <Modal.Title>{editingDsId ? t('dataSources.modal.editTitle') : t('dataSources.modal.createTitle')}</Modal.Title>
          </Modal.Header>
          <Form onSubmit={handleDsSubmit}>
            <Modal.Body>
              <Row>
                <Col md={6}>
                  <Form.Group className="mb-3">
                    <Form.Label>{t('dataSources.modal.name')}</Form.Label>
                    <Form.Control type="text" placeholder={t('dataSources.modal.namePlaceholder')} value={dsForm.name} onChange={(e) => setDsForm({ ...dsForm, name: e.target.value })} required />
                  </Form.Group>
                </Col>
                <Col md={6}>
                  <Form.Group className="mb-3">
                    <Form.Label>{t('dataSources.modal.type')}</Form.Label>
                    <Form.Select value={dsForm.type} onChange={(e) => setDsForm({ ...dsForm, type: e.target.value, config: {} })}>
                      <option value="postgres">PostgreSQL</option>
                      <option value="s3">Amazon S3</option>
                      <option value="api">REST API</option>
                    </Form.Select>
                  </Form.Group>
                </Col>
              </Row>
              <div className="config-section p-3 rounded mb-3" style={{ background: 'var(--surface-contrast)', border: '1px solid var(--color-border)' }}>
                <h6 className="mb-3 text-muted">{t('dataSources.modal.connectionDetails')}</h6>
                {renderDsConfigFields()}
              </div>
            </Modal.Body>
            <Modal.Footer>
              <Button variant="secondary" onClick={() => { setShowDsModal(false); setEditingDsId(null); }}>{t('dataSources.modal.cancel')}</Button>
              <Button variant="primary" type="submit" disabled={dsSubmitting}>
                {dsSubmitting ? <Spinner size="sm" animation="border" /> : (editingDsId ? t('dataSources.modal.update') : t('dataSources.modal.connect'))}
              </Button>
            </Modal.Footer>
          </Form>
        </Modal>

        {/* ── Upload Dataset Modal ── */}
        <Modal show={showUpload} onHide={() => { setShowUpload(false); setUploadState({ name: '', description: '', file: null }); }} centered>
          <Form onSubmit={handleUploadSubmit}>
            <Modal.Header closeButton><Modal.Title>{t('datasets.uploadModal.title')}</Modal.Title></Modal.Header>
            <Modal.Body>
              <Form.Group className="mb-3">
                <Form.Label>{t('datasets.uploadModal.name')}</Form.Label>
                <Form.Control type="text" name="name" placeholder={t('datasets.uploadModal.namePlaceholder')} value={uploadState.name} onChange={(e) => setUploadState(prev => ({ ...prev, name: e.target.value }))} />
              </Form.Group>
              <Form.Group className="mb-3">
                <Form.Label>{t('datasets.uploadModal.description')}</Form.Label>
                <Form.Control as="textarea" rows={2} name="description" placeholder={t('datasets.uploadModal.descriptionPlaceholder')} value={uploadState.description} onChange={(e) => setUploadState(prev => ({ ...prev, description: e.target.value }))} />
              </Form.Group>
              <Form.Group className="mb-3">
                <Form.Label>{t('datasets.uploadModal.file')}</Form.Label>
                <Form.Control type="file" accept=".xlsx,.xls,.csv" onChange={(e) => setUploadState(prev => ({ ...prev, file: e.target.files[0] || null }))} required />
                <Form.Text className="text-muted">{t('datasets.uploadModal.fileHelp')}</Form.Text>
              </Form.Group>
            </Modal.Body>
            <Modal.Footer>
              <Button variant="secondary" onClick={() => { setShowUpload(false); setUploadState({ name: '', description: '', file: null }); }}>{t('datasets.uploadModal.cancel')}</Button>
              <Button variant="primary" type="submit" disabled={uploadLoading}>
                {uploadLoading ? t('datasets.uploadModal.uploading') : t('datasets.uploadModal.upload')}
              </Button>
            </Modal.Footer>
          </Form>
        </Modal>

        {/* ── Create Group Modal ── */}
        <Modal show={showGroupModal} onHide={() => { setShowGroupModal(false); setGroupState({ name: '', description: '', dataset_ids: [] }); }} centered size="lg">
          <Form onSubmit={handleGroupSubmit}>
            <Modal.Header closeButton><Modal.Title>{t('datasets.groupModal.title')}</Modal.Title></Modal.Header>
            <Modal.Body>
              <Form.Group className="mb-3">
                <Form.Label>{t('datasets.groupModal.name')}</Form.Label>
                <Form.Control type="text" placeholder="e.g., Q1 Financials" value={groupState.name} onChange={(e) => setGroupState(prev => ({ ...prev, name: e.target.value }))} required />
              </Form.Group>
              <Form.Group className="mb-3">
                <Form.Label>{t('datasets.groupModal.description')}</Form.Label>
                <Form.Control as="textarea" rows={2} placeholder="" value={groupState.description} onChange={(e) => setGroupState(prev => ({ ...prev, description: e.target.value }))} />
              </Form.Group>
              <Form.Group className="mb-3">
                <Form.Label>{t('datasets.groupModal.selectDatasets')}</Form.Label>
                <div style={{ maxHeight: '300px', overflowY: 'auto', border: '1px solid var(--color-border)', padding: '10px', borderRadius: '4px' }}>
                  {datasets.map(ds => (
                    <Form.Check
                      key={ds.id}
                      type="checkbox"
                      id={`group-ds-${ds.id}`}
                      label={`${ds.name} (${ds.row_count} rows)`}
                      checked={groupState.dataset_ids.includes(ds.id)}
                      onChange={() => setGroupState(prev => ({
                        ...prev,
                        dataset_ids: prev.dataset_ids.includes(ds.id)
                          ? prev.dataset_ids.filter(id => id !== ds.id)
                          : [...prev.dataset_ids, ds.id]
                      }))}
                      className="mb-2"
                    />
                  ))}
                  {datasets.length === 0 && <p className="text-muted">{t('datasets.noDatasetsAvailable')}</p>}
                </div>
              </Form.Group>
            </Modal.Body>
            <Modal.Footer>
              <Button variant="secondary" onClick={() => { setShowGroupModal(false); setGroupState({ name: '', description: '', dataset_ids: [] }); }}>{t('datasets.groupModal.cancel')}</Button>
              <Button variant="primary" type="submit" disabled={groupSubmitLoading}>
                {groupSubmitLoading ? t('datasets.groupModal.creating') : t('datasets.groupModal.create')}
              </Button>
            </Modal.Footer>
          </Form>
        </Modal>

        {/* ── Preview Dataset Modal ── */}
        <Modal show={Boolean(previewDataset)} onHide={() => { setPreviewDataset(null); setPreviewData(null); setSummaryData(null); }} size="lg" centered>
          <Modal.Header closeButton>
            <Modal.Title>Preview &mdash; {previewDataset?.name}</Modal.Title>
          </Modal.Header>
          <Modal.Body>
            {previewLoading ? (
              <div className="text-center py-3"><Spinner animation="border" size="sm" /> {t('datasets.loadingPreview')}</div>
            ) : renderPreviewTable}
            <hr />
            <h6 className="text-uppercase text-muted">{t('datasets.summaryStats')}</h6>
            {summaryLoading ? (
              <div className="text-center py-3"><Spinner animation="border" size="sm" /> {t('datasets.loadingSummary')}</div>
            ) : renderSummaryCards}
          </Modal.Body>
          <Modal.Footer>
            <Button variant="primary" onClick={() => { setPreviewDataset(null); setPreviewData(null); setSummaryData(null); }}>{t('datasets.close')}</Button>
          </Modal.Footer>
        </Modal>
        {/* ── Gemini CLI Manual Login Modal ── */}
        <Modal show={showGeminiModal} onHide={() => setShowGeminiModal(false)} centered>
          <Form onSubmit={handleGeminiLogin}>
            <Modal.Header closeButton>
              <Modal.Title>Connect Gemini CLI</Modal.Title>
            </Modal.Header>
            <Modal.Body>
              <p className="small text-muted mb-3">
                To connect Gemini CLI with your Gmail and Code Assist entitlements, 
                please follow the link below to authorize, then paste the code from the success page.
              </p>
              <div className="mb-4 text-center">
                <Button 
                  variant="primary" 
                  href="https://accounts.google.com/v3/signin/accountchooser?access_type=offline&client_id=681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com&redirect_uri=http%3A%2F%2F127.0.0.1%3A62347%2Foauth2callback&response_type=code&scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fcloud-platform+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fuserinfo.email+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fuserinfo.profile+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.readonly+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.send" 
                  target="_blank"
                >
                  1. Authorize Gemini CLI
                </Button>
              </div>
              <Form.Group className="mb-3">
                <Form.Label>2. Paste Authorization Code</Form.Label>
                <Form.Control 
                  type="text" 
                  placeholder="Paste code from the success page URL or response" 
                  value={geminiCode}
                  onChange={(e) => setGeminiCode(e.target.value)}
                  required
                />
                <Form.Text className="text-muted">
                  Look for the <code>code=...</code> parameter in the URL of the success page.
                </Form.Text>
              </Form.Group>
            </Modal.Body>
            <Modal.Footer>
              <Button variant="secondary" onClick={() => setShowGeminiModal(false)}>Cancel</Button>
              <Button variant="primary" type="submit" disabled={geminiSubmitting || !geminiCode}>
                {geminiSubmitting ? <Spinner size="sm" animation="border" /> : "Complete Connection"}
              </Button>
            </Modal.Footer>
          </Form>
        </Modal>
      </div>
    </Layout>
  );
};

export default IntegrationsPage;
