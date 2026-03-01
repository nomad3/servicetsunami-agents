import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
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
import {
  FaSyncAlt,
  FaCalendarAlt,
  FaCheckCircle,
  FaCloud,
  FaCloudUploadAlt,
  FaDatabase,
  FaEdit,
  FaExclamationTriangle,
  FaBolt,
  FaFileUpload,
  FaNetworkWired,
  FaPen,
  FaPlay,
  FaPlug,
  FaPlus,
  FaPlusCircle,
  FaServer,
  FaTrash,
  FaTimesCircle,
  FaCogs
} from 'react-icons/fa';
import { useTranslation } from 'react-i18next';
import Layout from '../components/Layout';
import SkillsConfigPanel from '../components/SkillsConfigPanel';
import SyncStatusBadge from '../components/SyncStatusBadge';
import connectorService from '../services/connector';
import dataPipelineService from '../services/dataPipeline';
import dataSourceService from '../services/dataSource';
import datasetService from '../services/dataset';
import datasetGroupService from '../services/datasetGroup';
import './IntegrationsPage.css';

// ─── Constants ────────────────────────────────────────────────────────────────

const CONNECTOR_TYPES = {
  snowflake: { label: 'Snowflake', icon: '❄️', color: '#29B5E8' },
  postgres: { label: 'PostgreSQL', icon: '🐘', color: '#336791' },
  mysql: { label: 'MySQL', icon: '🐬', color: '#00758F' },
  databricks: { label: 'Databricks', icon: '⚡', color: '#FF3621' },
  s3: { label: 'Amazon S3', icon: '📦', color: '#FF9900' },
  gcs: { label: 'Google Cloud Storage', icon: '☁️', color: '#4285F4' },
  api: { label: 'REST API', icon: '🔗', color: '#6C757D' }
};

const CONNECTOR_FIELDS = {
  snowflake: [
    { name: 'account', label: 'Account', type: 'text', placeholder: 'xy12345.us-east-1', required: true },
    { name: 'user', label: 'Username', type: 'text', required: true },
    { name: 'password', label: 'Password', type: 'password', required: true },
    { name: 'warehouse', label: 'Warehouse', type: 'text', required: true },
    { name: 'database', label: 'Database', type: 'text', required: true },
    { name: 'schema', label: 'Schema', type: 'text', placeholder: 'PUBLIC' }
  ],
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
  databricks: [
    { name: 'host', label: 'Workspace URL', type: 'text', required: true },
    { name: 'token', label: 'Access Token', type: 'password', required: true },
    { name: 'http_path', label: 'SQL Warehouse Path', type: 'text', required: true }
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

const TAB_KEYS = ['connected-apps', 'connectors', 'data-sources', 'datasets'];

// ─── Main Component ───────────────────────────────────────────────────────────

const IntegrationsPage = () => {
  const { t } = useTranslation('datasets');
  const [searchParams, setSearchParams] = useSearchParams();
  const initialTab = TAB_KEYS.includes(searchParams.get('tab')) ? searchParams.get('tab') : 'connected-apps';
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
  const [connectorForm, setConnectorForm] = useState({ name: '', description: '', type: 'snowflake', config: {} });
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
  const [dsForm, setDsForm] = useState({ name: '', type: 'databricks', config: {} });
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
        setSuccess('Connector updated');
      } else {
        await connectorService.create(connectorForm);
        setSuccess('Connector created');
      }
      setShowConnectorModal(false);
      fetchConnectors();
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError('Failed to save connector');
    } finally {
      setSaving(false);
    }
  };

  const handleTestConnector = async (connectorId = null) => {
    try {
      if (connectorId) {
        setTesting(connectorId);
        await connectorService.testExisting(connectorId);
        setSuccess('Connection successful!');
        fetchConnectors();
      } else {
        setTesting(true);
        const res = await connectorService.testConnection(connectorForm.type, connectorForm.config);
        setTestResult(res.data);
      }
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError('Connection test failed');
      if (!connectorId) setTestResult({ success: false, message: err.response?.data?.detail || 'Connection failed' });
    } finally {
      setTesting(null);
    }
  };

  const handleDeleteConnector = async (id) => {
    if (window.confirm('Delete this connector? This will also remove any scheduled syncs.')) {
      try {
        await connectorService.delete(id);
        setSuccess('Connector deleted');
        fetchConnectors();
        setTimeout(() => setSuccess(null), 3000);
      } catch (err) {
        setError('Failed to delete connector');
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
      setSuccess('Sync schedule created');
      setShowSyncModal(false);
      fetchConnectors();
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError('Failed to create sync');
    } finally {
      setSaving(false);
    }
  };

  const handleRunSync = async (syncId) => {
    try {
      setSyncing(syncId);
      await dataPipelineService.execute(syncId);
      setSuccess('Sync started!');
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError('Failed to start sync');
    } finally {
      setSyncing(null);
    }
  };

  const getStatusBadge = (status) => {
    const configs = {
      active: { bg: 'success', icon: FaCheckCircle, text: 'Active' },
      error: { bg: 'danger', icon: FaTimesCircle, text: 'Error' },
      pending: { bg: 'warning', icon: FaExclamationTriangle, text: 'Pending' }
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
      setDsForm({ name: '', type: 'databricks', config: {} });
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
        setSuccess('Data source updated');
      } else {
        await dataSourceService.create(dsForm);
        setSuccess('Data source created');
      }
      fetchDataSources();
      setShowDsModal(false);
      setEditingDsId(null);
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError('Failed to save data source');
    } finally {
      setDsSubmitting(false);
    }
  };

  const handleDeleteDs = async (id) => {
    if (window.confirm('Are you sure you want to delete this data source?')) {
      try {
        await dataSourceService.remove(id);
        setSuccess('Data source deleted');
        fetchDataSources();
        setTimeout(() => setSuccess(null), 3000);
      } catch (err) {
        setError('Failed to delete data source');
      }
    }
  };

  const getDsTypeIcon = (type) => {
    switch (type) {
      case 'databricks': return <FaCloud size={24} className="text-info" />;
      case 'postgres': return <FaDatabase size={24} className="text-primary" />;
      case 'rest_api': case 'api': return <FaNetworkWired size={24} className="text-success" />;
      default: return <FaServer size={24} className="text-secondary" />;
    }
  };

  const renderDsConfigFields = () => {
    switch (dsForm.type) {
      case 'databricks':
        return (
          <>
            <Form.Group className="mb-3">
              <Form.Label>Databricks Host</Form.Label>
              <Form.Control type="text" placeholder="https://adb-xxxx.xx.azuredatabricks.net" value={dsForm.config.host || ''} onChange={(e) => handleDsConfigChange('host', e.target.value)} required />
            </Form.Group>
            <Form.Group className="mb-3">
              <Form.Label>Access Token</Form.Label>
              <Form.Control type="password" placeholder="dapi..." value={dsForm.config.token || ''} onChange={(e) => handleDsConfigChange('token', e.target.value)} required />
            </Form.Group>
            <Form.Group className="mb-3">
              <Form.Label>HTTP Path / Warehouse ID</Form.Label>
              <Form.Control type="text" placeholder="/sql/1.0/warehouses/..." value={dsForm.config.http_path || ''} onChange={(e) => handleDsConfigChange('http_path', e.target.value)} />
            </Form.Group>
          </>
        );
      case 'postgres':
        return (
          <>
            <Row>
              <Col md={8}>
                <Form.Group className="mb-3">
                  <Form.Label>Host</Form.Label>
                  <Form.Control type="text" placeholder="localhost" value={dsForm.config.host || ''} onChange={(e) => handleDsConfigChange('host', e.target.value)} required />
                </Form.Group>
              </Col>
              <Col md={4}>
                <Form.Group className="mb-3">
                  <Form.Label>Port</Form.Label>
                  <Form.Control type="number" placeholder="5432" value={dsForm.config.port || ''} onChange={(e) => handleDsConfigChange('port', e.target.value)} required />
                </Form.Group>
              </Col>
            </Row>
            <Form.Group className="mb-3">
              <Form.Label>Database Name</Form.Label>
              <Form.Control type="text" value={dsForm.config.database || ''} onChange={(e) => handleDsConfigChange('database', e.target.value)} required />
            </Form.Group>
            <Row>
              <Col md={6}>
                <Form.Group className="mb-3">
                  <Form.Label>Username</Form.Label>
                  <Form.Control type="text" value={dsForm.config.username || ''} onChange={(e) => handleDsConfigChange('username', e.target.value)} required />
                </Form.Group>
              </Col>
              <Col md={6}>
                <Form.Group className="mb-3">
                  <Form.Label>Password</Form.Label>
                  <Form.Control type="password" value={dsForm.config.password || ''} onChange={(e) => handleDsConfigChange('password', e.target.value)} required />
                </Form.Group>
              </Col>
            </Row>
          </>
        );
      default:
        return (
          <Form.Group className="mb-3">
            <Form.Label>Configuration (JSON)</Form.Label>
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
      setSuccess('Dataset uploaded');
      await fetchDatasets();
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError('Failed to upload dataset');
    } finally {
      setUploadLoading(false);
    }
  };

  const handleGroupSubmit = async (event) => {
    event.preventDefault();
    if (groupState.dataset_ids.length < 2) { setError('Select at least 2 datasets to group.'); return; }
    setGroupSubmitLoading(true);
    try {
      await datasetGroupService.create(groupState);
      setShowGroupModal(false);
      setGroupState({ name: '', description: '', dataset_ids: [] });
      setSuccess('Group created');
      await fetchGroups();
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError('Failed to create group');
    } finally {
      setGroupSubmitLoading(false);
    }
  };

  const renderPreviewTable = useMemo(() => {
    if (!previewData?.sample_rows?.length) return <p className="text-muted">No preview data available</p>;
    const columns = Object.keys(previewData.sample_rows[0]);
    return (
      <Table striped bordered hover responsive size="sm" className="mt-3">
        <thead><tr>{columns.map(col => <th key={col}>{col}</th>)}</tr></thead>
        <tbody>{previewData.sample_rows.map((row, i) => <tr key={i}>{columns.map(col => <td key={col}>{row[col]}</td>)}</tr>)}</tbody>
      </Table>
    );
  }, [previewData]);

  const renderSummaryCards = useMemo(() => {
    if (!summaryData?.numeric_columns?.length) return <p className="text-muted">No summary statistics</p>;
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
  // TAB CONTENT: Skills
  // ═══════════════════════════════════════════════════════════════════════════

  const renderSkillsTab = () => (
    <div className="tab-content-inner">
      <SkillsConfigPanel />
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
                <div className="stat-label">Connectors</div>
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
                <div className="stat-label">Active</div>
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
                <div className="stat-label">Active Syncs</div>
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
                <div className="stat-label">Need Attention</div>
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
                <h5 className="mb-0"><FaBolt className="me-2" />Connected Systems</h5>
                <Button variant="primary" size="sm" onClick={() => handleOpenConnectorModal()}>
                  <FaPlus className="me-2" />Add Connector
                </Button>
              </Card.Header>
              <Card.Body className="p-0">
                {connectors.length === 0 ? (
                  <div className="text-center py-5">
                    <FaCloudUploadAlt size={48} className="text-muted mb-3" />
                    <h5>No connectors yet</h5>
                    <p className="text-muted">Connect your first system to start syncing data</p>
                    <Button variant="primary" onClick={() => handleOpenConnectorModal()}>
                      <FaPlus className="me-2" />Connect Your First System
                    </Button>
                  </div>
                ) : (
                  <Table hover className="mb-0 connectors-table">
                    <thead><tr><th>Source</th><th>Status</th><th>Last Tested</th><th className="text-end">Actions</th></tr></thead>
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
                          <td><small className="text-muted">{connector.last_test_at ? new Date(connector.last_test_at).toLocaleDateString() : 'Never'}</small></td>
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
              <Card.Header><h5 className="mb-0"><FaCalendarAlt className="me-2" />Data Syncs</h5></Card.Header>
              <Card.Body className="p-0">
                {syncs.length === 0 ? (
                  <div className="text-center py-4">
                    <FaSyncAlt size={32} className="text-muted mb-2" />
                    <p className="text-muted mb-0 small">No data syncs scheduled</p>
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
        <p className="text-muted mb-0">External databases and APIs your AI agents can query</p>
        <Button variant="primary" onClick={() => handleShowDsModal()}>
          <FaPlusCircle className="me-2" />Add Data Source
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
                      <FaCheckCircle className="me-1" />Connected
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
                <h5>No data sources yet</h5>
                <p>Add a data source so your AI agents can query external databases and APIs.</p>
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
          <Nav.Item><Nav.Link eventKey="datasets">Datasets</Nav.Link></Nav.Item>
          <Nav.Item><Nav.Link eventKey="groups">Dataset Groups</Nav.Link></Nav.Item>
        </Nav>
        <div>
          {datasetSubTab === 'datasets' && (
            <Button variant="primary" onClick={() => setShowUpload(true)}>
              <FaFileUpload className="me-2" />Upload Dataset
            </Button>
          )}
          {datasetSubTab === 'groups' && (
            <Button variant="primary" onClick={() => setShowGroupModal(true)}>
              <FaPlus className="me-2" />Create Group
            </Button>
          )}
        </div>
      </div>

      {datasetSubTab === 'datasets' && (
        <>
          {datasetsLoading ? (
            <div className="text-center py-4"><Spinner animation="border" size="sm" /> Loading datasets...</div>
          ) : (
            <Table striped bordered hover responsive>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Description</th>
                  <th>Rows</th>
                  <th>Sync Status</th>
                  <th>Created</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {datasets.map(dataset => (
                  <tr key={dataset.id}>
                    <td>{dataset.name}</td>
                    <td>{dataset.description}</td>
                    <td>{dataset.row_count}</td>
                    <td><SyncStatusBadge status={dataset.metadata?.sync_status} /></td>
                    <td>{dataset.created_at ? new Date(dataset.created_at).toLocaleString() : '...'}</td>
                    <td>
                      <Button variant="info" size="sm" onClick={() => openPreview(dataset)} className="me-2">Preview</Button>
                      <Button
                        variant="outline-primary"
                        size="sm"
                        onClick={async () => {
                          try { await datasetService.sync(dataset.id); fetchDatasets(); } catch (e) { setError('Failed to trigger sync'); }
                        }}
                        disabled={dataset.metadata?.sync_status === 'syncing'}
                      >
                        {dataset.metadata?.sync_status === 'syncing' ? 'Syncing...' : 'Sync'}
                      </Button>
                    </td>
                  </tr>
                ))}
                {datasets.length === 0 && !datasetsLoading && (
                  <tr><td colSpan={6} className="text-center text-muted">No datasets uploaded yet</td></tr>
                )}
              </tbody>
            </Table>
          )}
        </>
      )}

      {datasetSubTab === 'groups' && (
        <>
          {groupsLoading ? (
            <div className="text-center py-4"><Spinner animation="border" size="sm" /> Loading groups...</div>
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
                  <tr><td colSpan={4} className="text-center text-muted">No dataset groups</td></tr>
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
              Integrations
            </h1>
            <p className="page-subtitle text-muted">
              Connected apps, connectors, data sources, and datasets in one place
            </p>
          </div>
        </div>

        {error && <Alert variant="danger" onClose={() => setError(null)} dismissible>{error}</Alert>}
        {success && <Alert variant="success" onClose={() => setSuccess(null)} dismissible>{success}</Alert>}

        {/* Tab Navigation */}
        <Nav variant="tabs" activeKey={activeTab} onSelect={handleTabChange} className="mb-4 integrations-tabs">
          <Nav.Item>
            <Nav.Link eventKey="connected-apps"><FaPlug className="me-2" />Connected Apps</Nav.Link>
          </Nav.Item>
          <Nav.Item>
            <Nav.Link eventKey="connectors"><FaBolt className="me-2" />Connectors</Nav.Link>
          </Nav.Item>
          <Nav.Item>
            <Nav.Link eventKey="data-sources"><FaDatabase className="me-2" />Data Sources</Nav.Link>
          </Nav.Item>
          <Nav.Item>
            <Nav.Link eventKey="datasets"><FaFileUpload className="me-2" />Datasets</Nav.Link>
          </Nav.Item>
        </Nav>

        {/* Tab Content */}
        {activeTab === 'connected-apps' && renderSkillsTab()}
        {activeTab === 'connectors' && renderConnectorsTab()}
        {activeTab === 'data-sources' && renderDataSourcesTab()}
        {activeTab === 'datasets' && renderDatasetsTab()}

        {/* ── Connector Modal ── */}
        <Modal show={showConnectorModal} onHide={() => setShowConnectorModal(false)} size="lg">
          <Modal.Header closeButton>
            <Modal.Title>{editingConnector ? 'Edit Connector' : 'Add New Connector'}</Modal.Title>
          </Modal.Header>
          <Modal.Body>
            <Form>
              <Row>
                <Col md={6}>
                  <Form.Group className="mb-3">
                    <Form.Label>Name<span className="text-danger">*</span></Form.Label>
                    <Form.Control type="text" placeholder="e.g., Production Snowflake" value={connectorForm.name} onChange={(e) => setConnectorForm({ ...connectorForm, name: e.target.value })} required />
                  </Form.Group>
                </Col>
                <Col md={6}>
                  <Form.Group className="mb-3">
                    <Form.Label>Type<span className="text-danger">*</span></Form.Label>
                    <Form.Select value={connectorForm.type} onChange={(e) => setConnectorForm({ ...connectorForm, type: e.target.value, config: {} })} disabled={!!editingConnector}>
                      {Object.entries(CONNECTOR_TYPES).map(([key, cfg]) => (
                        <option key={key} value={key}>{cfg.icon} {cfg.label}</option>
                      ))}
                    </Form.Select>
                  </Form.Group>
                </Col>
              </Row>
              <Form.Group className="mb-3">
                <Form.Label>Description</Form.Label>
                <Form.Control as="textarea" rows={2} placeholder="Optional description..." value={connectorForm.description} onChange={(e) => setConnectorForm({ ...connectorForm, description: e.target.value })} />
              </Form.Group>
              <hr />
              <h6 className="mb-3">Connection Settings</h6>
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
              {testing ? <Spinner size="sm" className="me-2" /> : <FaPlay className="me-2" />}Test Connection
            </Button>
            <Button variant="secondary" onClick={() => setShowConnectorModal(false)}>Cancel</Button>
            <Button variant="primary" onClick={handleSaveConnector} disabled={saving || !connectorForm.name}>
              {saving ? <Spinner size="sm" /> : (editingConnector ? 'Update' : 'Create')}
            </Button>
          </Modal.Footer>
        </Modal>

        {/* ── Sync Modal ── */}
        <Modal show={showSyncModal} onHide={() => setShowSyncModal(false)}>
          <Modal.Header closeButton><Modal.Title>Schedule Data Sync</Modal.Title></Modal.Header>
          <Modal.Body>
            <Form>
              <Form.Group className="mb-3">
                <Form.Label>Connector<span className="text-danger">*</span></Form.Label>
                <Form.Select value={syncForm.connector_id} onChange={(e) => setSyncForm({ ...syncForm, connector_id: e.target.value })} required>
                  <option value="">Select a connector...</option>
                  {connectors.filter(c => c.status === 'active').map(c => (
                    <option key={c.id} value={c.id}>{CONNECTOR_TYPES[c.type]?.icon} {c.name}</option>
                  ))}
                </Form.Select>
              </Form.Group>
              <Form.Group className="mb-3">
                <Form.Label>Table/Query Name<span className="text-danger">*</span></Form.Label>
                <Form.Control type="text" placeholder="e.g., customers, orders" value={syncForm.table_name} onChange={(e) => setSyncForm({ ...syncForm, table_name: e.target.value })} required />
              </Form.Group>
              <Row>
                <Col>
                  <Form.Group className="mb-3">
                    <Form.Label>Frequency</Form.Label>
                    <Form.Select value={syncForm.frequency} onChange={(e) => setSyncForm({ ...syncForm, frequency: e.target.value })}>
                      <option value="hourly">Hourly</option>
                      <option value="daily">Daily</option>
                      <option value="weekly">Weekly</option>
                    </Form.Select>
                  </Form.Group>
                </Col>
                <Col>
                  <Form.Group className="mb-3">
                    <Form.Label>Sync Mode</Form.Label>
                    <Form.Select value={syncForm.mode} onChange={(e) => setSyncForm({ ...syncForm, mode: e.target.value })}>
                      <option value="full">Full Refresh</option>
                      <option value="incremental">Incremental</option>
                    </Form.Select>
                  </Form.Group>
                </Col>
              </Row>
            </Form>
          </Modal.Body>
          <Modal.Footer>
            <Button variant="secondary" onClick={() => setShowSyncModal(false)}>Cancel</Button>
            <Button variant="primary" onClick={handleCreateSync} disabled={saving || !syncForm.connector_id || !syncForm.table_name}>
              {saving ? <Spinner size="sm" /> : 'Schedule Sync'}
            </Button>
          </Modal.Footer>
        </Modal>

        {/* ── Data Source Modal ── */}
        <Modal show={showDsModal} onHide={() => { setShowDsModal(false); setEditingDsId(null); }} size="lg">
          <Modal.Header closeButton>
            <Modal.Title>{editingDsId ? 'Edit' : 'Add'} Data Source</Modal.Title>
          </Modal.Header>
          <Form onSubmit={handleDsSubmit}>
            <Modal.Body>
              <Row>
                <Col md={6}>
                  <Form.Group className="mb-3">
                    <Form.Label>Name</Form.Label>
                    <Form.Control type="text" placeholder="e.g., Production DB" value={dsForm.name} onChange={(e) => setDsForm({ ...dsForm, name: e.target.value })} required />
                  </Form.Group>
                </Col>
                <Col md={6}>
                  <Form.Group className="mb-3">
                    <Form.Label>Type</Form.Label>
                    <Form.Select value={dsForm.type} onChange={(e) => setDsForm({ ...dsForm, type: e.target.value, config: {} })}>
                      <option value="databricks">Databricks</option>
                      <option value="postgres">PostgreSQL</option>
                      <option value="s3">Amazon S3</option>
                      <option value="api">REST API</option>
                    </Form.Select>
                  </Form.Group>
                </Col>
              </Row>
              <div className="config-section p-3 rounded mb-3" style={{ background: 'var(--surface-contrast)', border: '1px solid var(--color-border)' }}>
                <h6 className="mb-3 text-muted">Connection Details</h6>
                {renderDsConfigFields()}
              </div>
            </Modal.Body>
            <Modal.Footer>
              <Button variant="secondary" onClick={() => { setShowDsModal(false); setEditingDsId(null); }}>Cancel</Button>
              <Button variant="primary" type="submit" disabled={dsSubmitting}>
                {dsSubmitting ? <Spinner size="sm" animation="border" /> : (editingDsId ? 'Update' : 'Connect')}
              </Button>
            </Modal.Footer>
          </Form>
        </Modal>

        {/* ── Upload Dataset Modal ── */}
        <Modal show={showUpload} onHide={() => { setShowUpload(false); setUploadState({ name: '', description: '', file: null }); }} centered>
          <Form onSubmit={handleUploadSubmit}>
            <Modal.Header closeButton><Modal.Title>Upload Dataset</Modal.Title></Modal.Header>
            <Modal.Body>
              <Form.Group className="mb-3">
                <Form.Label>Name</Form.Label>
                <Form.Control type="text" name="name" placeholder="Dataset name" value={uploadState.name} onChange={(e) => setUploadState(prev => ({ ...prev, name: e.target.value }))} />
              </Form.Group>
              <Form.Group className="mb-3">
                <Form.Label>Description</Form.Label>
                <Form.Control as="textarea" rows={2} name="description" placeholder="Optional description" value={uploadState.description} onChange={(e) => setUploadState(prev => ({ ...prev, description: e.target.value }))} />
              </Form.Group>
              <Form.Group className="mb-3">
                <Form.Label>File</Form.Label>
                <Form.Control type="file" accept=".xlsx,.xls,.csv" onChange={(e) => setUploadState(prev => ({ ...prev, file: e.target.files[0] || null }))} required />
                <Form.Text className="text-muted">Supported: CSV, XLS, XLSX</Form.Text>
              </Form.Group>
            </Modal.Body>
            <Modal.Footer>
              <Button variant="secondary" onClick={() => { setShowUpload(false); setUploadState({ name: '', description: '', file: null }); }}>Cancel</Button>
              <Button variant="primary" type="submit" disabled={uploadLoading}>
                {uploadLoading ? 'Uploading...' : 'Upload'}
              </Button>
            </Modal.Footer>
          </Form>
        </Modal>

        {/* ── Create Group Modal ── */}
        <Modal show={showGroupModal} onHide={() => { setShowGroupModal(false); setGroupState({ name: '', description: '', dataset_ids: [] }); }} centered size="lg">
          <Form onSubmit={handleGroupSubmit}>
            <Modal.Header closeButton><Modal.Title>Create Dataset Group</Modal.Title></Modal.Header>
            <Modal.Body>
              <Form.Group className="mb-3">
                <Form.Label>Group Name</Form.Label>
                <Form.Control type="text" placeholder="e.g., Q1 Financials" value={groupState.name} onChange={(e) => setGroupState(prev => ({ ...prev, name: e.target.value }))} required />
              </Form.Group>
              <Form.Group className="mb-3">
                <Form.Label>Description</Form.Label>
                <Form.Control as="textarea" rows={2} placeholder="Optional" value={groupState.description} onChange={(e) => setGroupState(prev => ({ ...prev, description: e.target.value }))} />
              </Form.Group>
              <Form.Group className="mb-3">
                <Form.Label>Select Datasets (min 2)</Form.Label>
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
                  {datasets.length === 0 && <p className="text-muted">No datasets available.</p>}
                </div>
              </Form.Group>
            </Modal.Body>
            <Modal.Footer>
              <Button variant="secondary" onClick={() => { setShowGroupModal(false); setGroupState({ name: '', description: '', dataset_ids: [] }); }}>Cancel</Button>
              <Button variant="primary" type="submit" disabled={groupSubmitLoading}>
                {groupSubmitLoading ? 'Creating...' : 'Create Group'}
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
              <div className="text-center py-3"><Spinner animation="border" size="sm" /> Loading preview...</div>
            ) : renderPreviewTable}
            <hr />
            <h6 className="text-uppercase text-muted">Summary Statistics</h6>
            {summaryLoading ? (
              <div className="text-center py-3"><Spinner animation="border" size="sm" /> Loading summary...</div>
            ) : renderSummaryCards}
          </Modal.Body>
          <Modal.Footer>
            <Button variant="primary" onClick={() => { setPreviewDataset(null); setPreviewData(null); setSummaryData(null); }}>Close</Button>
          </Modal.Footer>
        </Modal>
      </div>
    </Layout>
  );
};

export default IntegrationsPage;
