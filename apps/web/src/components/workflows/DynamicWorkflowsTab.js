import { useCallback, useEffect, useState } from 'react';
import { Alert, Badge, Button, Col, Form, Modal, Row, Spinner } from 'react-bootstrap';
import {
  FaBolt, FaCheck, FaClock, FaCog, FaEdit, FaPause, FaPlay, FaPlus, FaRocket,
  FaStore, FaTimesCircle, FaTrash,
} from 'react-icons/fa';
import { useNavigate } from 'react-router-dom';
import dynamicWorkflowService from '../../services/dynamicWorkflowService';

const statusColors = {
  draft: 'secondary',
  active: 'success',
  paused: 'warning',
  archived: 'dark',
};

const statusIcons = {
  draft: FaCog,
  active: FaCheck,
  paused: FaPause,
  archived: FaTimesCircle,
};

const triggerLabels = {
  cron: 'Scheduled',
  interval: 'Interval',
  webhook: 'Webhook',
  event: 'Event',
  manual: 'Manual',
  agent: 'Agent',
};

// ── Workflow Card ────────────────────────────────────────────────

function WorkflowCard({ workflow, onRun, onToggle, onDelete, onSelect, onEdit }) {
  const StatusIcon = statusIcons[workflow.status] || FaCog;
  const stepCount = workflow.definition?.steps?.length || 0;
  const trigger = workflow.trigger_config?.type || 'manual';

  return (
    <article
      className="ap-card h-100"
      style={{ cursor: 'pointer' }}
      onClick={() => onSelect(workflow)}
    >
      <div className="ap-card-body">
        <div className="d-flex justify-content-between align-items-start mb-3">
          <div className="d-flex align-items-center gap-2">
            <Badge bg={statusColors[workflow.status]} className="text-uppercase" style={{ fontSize: '0.65rem' }}>
              <StatusIcon size={10} className="me-1" />
              {workflow.status}
            </Badge>
            <span className="ap-badge-outline">
              {triggerLabels[trigger] || trigger}
            </span>
          </div>
          <div className="d-flex gap-1">
            {workflow.status === 'active' ? (
              <button type="button" className="ap-btn-ghost" title="Pause"
                onClick={(e) => { e.stopPropagation(); onToggle(workflow.id, 'pause'); }}>
                <FaPause size={10} />
              </button>
            ) : workflow.status !== 'archived' ? (
              <button type="button" className="ap-btn-ghost" title="Activate"
                onClick={(e) => { e.stopPropagation(); onToggle(workflow.id, 'activate'); }}>
                <FaPlay size={10} />
              </button>
            ) : null}
            <button type="button" className="ap-btn-ghost" title="Edit in builder"
              onClick={(e) => { e.stopPropagation(); onEdit(workflow.id); }}>
              <FaEdit size={10} />
            </button>
            <button type="button" className="ap-btn-ghost" title="Run now"
              onClick={(e) => { e.stopPropagation(); onRun(workflow.id); }}>
              <FaRocket size={10} />
            </button>
          </div>
        </div>

        <h3 className="ap-card-title">{workflow.name}</h3>
        <p className="ap-card-text mb-3">
          {workflow.description || 'No description'}
        </p>

        <div className="d-flex justify-content-between align-items-center" style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>
          <span><FaBolt className="me-1" />{stepCount} steps</span>
          <span><FaPlay className="me-1" />{workflow.run_count || 0} runs</span>
          {workflow.success_rate != null && (
            <span><FaCheck className="me-1" />{(workflow.success_rate * 100).toFixed(0)}%</span>
          )}
          {workflow.last_run_at && (
            <span><FaClock className="me-1" />{new Date(workflow.last_run_at).toLocaleDateString()}</span>
          )}
        </div>

        {workflow.tags?.length > 0 && (
          <div className="mt-2 d-flex gap-1 flex-wrap">
            {workflow.tags.map(tag => (
              <span key={tag} className="ap-badge-outline">{tag}</span>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}

// ── Run History Modal ────────────────────────────────────────────

function RunHistoryModal({ workflow, show, onHide }) {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedRun, setSelectedRun] = useState(null);
  const [runDetail, setRunDetail] = useState(null);

  useEffect(() => {
    if (show && workflow) {
      setLoading(true);
      dynamicWorkflowService.listRuns(workflow.id).then(r => { setRuns(r); setLoading(false); }).catch(() => setLoading(false));
    }
  }, [show, workflow]);

  const loadDetail = async (runId) => {
    setSelectedRun(runId);
    const detail = await dynamicWorkflowService.getRun(runId);
    setRunDetail(detail);
  };

  const runStatusColor = { running: 'primary', completed: 'success', failed: 'danger', cancelled: 'secondary', waiting_approval: 'warning' };

  return (
    <Modal show={show} onHide={onHide} size="lg">
      <Modal.Header closeButton>
        <Modal.Title style={{ fontSize: '1.1rem' }}>
          {workflow?.name} — Run History
        </Modal.Title>
      </Modal.Header>
      <Modal.Body style={{ maxHeight: '70vh', overflow: 'auto' }}>
        {loading ? (
          <div className="text-center py-4"><Spinner size="sm" /></div>
        ) : runs.length === 0 ? (
          <p className="text-muted text-center py-4">No runs yet. Click "Run" to start.</p>
        ) : (
          <>
            {runs.map(run => (
              <div
                key={run.id}
                className="p-3 mb-2 rounded border"
                style={{ cursor: 'pointer', background: selectedRun === run.id ? 'var(--bs-primary-bg-subtle)' : 'transparent' }}
                onClick={() => loadDetail(run.id)}
              >
                <div className="d-flex justify-content-between align-items-center">
                  <div>
                    <Badge bg={runStatusColor[run.status] || 'secondary'} className="me-2">{run.status}</Badge>
                    <small className="text-muted">{new Date(run.started_at).toLocaleString()}</small>
                  </div>
                  <div className="text-muted" style={{ fontSize: '0.75rem' }}>
                    {run.duration_ms ? `${(run.duration_ms / 1000).toFixed(1)}s` : '—'}
                    {run.total_tokens > 0 && ` · ${run.total_tokens} tokens`}
                  </div>
                </div>
                {run.error && <small className="text-danger d-block mt-1">{run.error}</small>}
              </div>
            ))}

            {runDetail && (
              <div className="mt-3 p-3 border rounded" style={{ background: 'var(--bs-body-bg)' }}>
                <h6 className="fw-bold mb-3">Step Log</h6>
                {runDetail.steps?.map(step => (
                  <div key={step.id} className="d-flex align-items-center gap-2 mb-2 py-1" style={{ borderBottom: '1px solid var(--bs-border-color)' }}>
                    <Badge bg={step.status === 'completed' ? 'success' : step.status === 'failed' ? 'danger' : step.status === 'running' ? 'primary' : 'secondary'} style={{ fontSize: '0.6rem', minWidth: 65 }}>
                      {step.status}
                    </Badge>
                    <span style={{ fontSize: '0.82rem', fontWeight: 500 }}>{step.step_id}</span>
                    <small className="text-muted">({step.step_type})</small>
                    <span className="ms-auto text-muted" style={{ fontSize: '0.72rem' }}>
                      {step.duration_ms ? `${step.duration_ms}ms` : ''}
                      {step.tokens_used > 0 && ` · ${step.tokens_used}t`}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </Modal.Body>
    </Modal>
  );
}

// ── Create Workflow Modal ────────────────────────────────────────

function CreateWorkflowModal({ show, onHide, onCreate }) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [creating, setCreating] = useState(false);

  const handleCreate = async () => {
    if (!name.trim()) return;
    setCreating(true);
    try {
      const wf = await dynamicWorkflowService.create({
        name: name.trim(),
        description: description.trim() || null,
        definition: { steps: [] },
        tags: [],
      });
      onCreate(wf);
      setName('');
      setDescription('');
      onHide();
    } catch (err) {
      console.error('Create workflow failed:', err);
    } finally {
      setCreating(false);
    }
  };

  return (
    <Modal show={show} onHide={onHide}>
      <Modal.Header closeButton>
        <Modal.Title style={{ fontSize: '1.1rem' }}>Create Dynamic Workflow</Modal.Title>
      </Modal.Header>
      <Modal.Body>
        <Form.Group className="mb-3">
          <Form.Label>Name</Form.Label>
          <Form.Control value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Daily Inbox Scanner" />
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Label>Description</Form.Label>
          <Form.Control as="textarea" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What does this workflow do?" />
        </Form.Group>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" size="sm" onClick={onHide}>Cancel</Button>
        <Button size="sm" onClick={handleCreate} disabled={creating || !name.trim()}>
          {creating ? <Spinner size="sm" /> : 'Create'}
        </Button>
      </Modal.Footer>
    </Modal>
  );
}

// ── Main Tab ─────────────────────────────────────────────────────

export default function DynamicWorkflowsTab() {
  const navigate = useNavigate();
  const [workflows, setWorkflows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showCreate, setShowCreate] = useState(false);
  const [selectedWorkflow, setSelectedWorkflow] = useState(null);
  const [showRuns, setShowRuns] = useState(false);
  const [filter, setFilter] = useState('');

  const loadWorkflows = useCallback(async () => {
    try {
      setLoading(true);
      const data = await dynamicWorkflowService.list();
      setWorkflows(data);
      setError(null);
    } catch (err) {
      setError('Failed to load workflows');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadWorkflows(); }, [loadWorkflows]);

  const handleRun = async (id) => {
    try {
      await dynamicWorkflowService.run(id);
      loadWorkflows();
    } catch (err) {
      console.error('Run failed:', err);
    }
  };

  const handleToggle = async (id, action) => {
    try {
      if (action === 'activate') await dynamicWorkflowService.activate(id);
      else await dynamicWorkflowService.pause(id);
      loadWorkflows();
    } catch (err) {
      console.error('Toggle failed:', err);
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this workflow?')) return;
    try {
      await dynamicWorkflowService.delete(id);
      loadWorkflows();
    } catch (err) {
      console.error('Delete failed:', err);
    }
  };

  const handleSelect = (wf) => {
    setSelectedWorkflow(wf);
    setShowRuns(true);
  };

  const filtered = workflows.filter(wf =>
    !filter || wf.name.toLowerCase().includes(filter.toLowerCase()) ||
    wf.tags?.some(t => t.toLowerCase().includes(filter.toLowerCase()))
  );

  if (loading) {
    return <div className="text-center py-5"><Spinner size="sm" /><p className="mt-2 text-muted" style={{ fontSize: '0.85rem' }}>Loading workflows...</p></div>;
  }

  return (
    <>
      {error && <Alert variant="danger" style={{ fontSize: '0.85rem' }}>{error}</Alert>}

      <div className="d-flex justify-content-between align-items-center mb-4 flex-wrap gap-2">
        <Form.Control
          placeholder="Search workflows..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{ maxWidth: 300, fontSize: 'var(--ap-fs-sm)' }}
        />
        <div className="d-flex gap-2">
          <button type="button" className="ap-btn-secondary ap-btn-sm" onClick={loadWorkflows}>
            Refresh
          </button>
          <button type="button" className="ap-btn-primary ap-btn-sm" onClick={() => setShowCreate(true)}>
            <FaPlus /> New Workflow
          </button>
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="ap-empty">
          <FaBolt size={40} className="mb-3" style={{ opacity: 0.3 }} />
          <div className="ap-empty-title">No dynamic workflows yet</div>
          <div className="ap-empty-text">Create your first workflow or install a template from the marketplace.</div>
          <div className="d-flex gap-2 justify-content-center mt-3">
            <button type="button" className="ap-btn-primary ap-btn-sm" onClick={() => setShowCreate(true)}>
              <FaPlus /> Create Workflow
            </button>
            <button type="button" className="ap-btn-secondary ap-btn-sm">
              <FaStore /> Browse Templates
            </button>
          </div>
        </div>
      ) : (
        <Row className="g-3">
          {filtered.map(wf => (
            <Col md={6} lg={4} key={wf.id}>
              <WorkflowCard
                workflow={wf}
                onRun={handleRun}
                onToggle={handleToggle}
                onDelete={handleDelete}
                onSelect={handleSelect}
                onEdit={(id) => navigate(`/workflows/builder/${id}`)}
              />
            </Col>
          ))}
        </Row>
      )}

      <CreateWorkflowModal
        show={showCreate}
        onHide={() => setShowCreate(false)}
        onCreate={(wf) => setWorkflows(prev => [wf, ...prev])}
      />

      <RunHistoryModal
        workflow={selectedWorkflow}
        show={showRuns}
        onHide={() => { setShowRuns(false); setSelectedWorkflow(null); }}
      />
    </>
  );
}
