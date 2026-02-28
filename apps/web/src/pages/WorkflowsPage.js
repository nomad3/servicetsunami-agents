import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Form,
  Modal,
  Nav,
  Spinner,
} from 'react-bootstrap';
import {
  FaBolt,
  FaBrain,
  FaCheck,
  FaCheckCircle,
  FaChevronDown,
  FaChevronRight,
  FaClipboardList,
  FaClock,
  FaCog,
  FaCoins,
  FaComments,
  FaCopy,
  FaDatabase,
  FaDollarSign,
  FaDraftingCompass,
  FaFileInvoiceDollar,
  FaExclamationTriangle,
  FaHeartbeat,
  FaLayerGroup,
  FaPlay,
  FaProjectDiagram,
  FaRedo,
  FaRobot,
  FaSearch,
  FaServer,
  FaStream,
  FaSyncAlt,
  FaTimes,
  FaTimesCircle,
} from 'react-icons/fa';
import { useSearchParams } from 'react-router-dom';
import Layout from '../components/Layout';
import TaskTimeline from '../components/TaskTimeline';
import taskService from '../services/taskService';
import './WorkflowsPage.css';

// ===========================================================================
// WORKFLOW DEFINITIONS — Static structure of all Temporal workflows
// ===========================================================================
const WORKFLOW_DEFINITIONS = [
  {
    id: 'task-execution',
    name: 'TaskExecutionWorkflow',
    description: 'End-to-end agent task execution: dispatch, memory recall, execute via ADK, persist entities, evaluate results',
    queue: 'orchestration',
    icon: FaClipboardList,
    color: '#f472b6',
    steps: [
      { name: 'dispatch_task', timeout: '2m', retry: '3x / 30s', type: 'start', description: 'Find best agent for the task' },
      { name: 'recall_memory', timeout: '1m', retry: '3x / 30s', description: 'Load agent memories into context' },
      { name: 'execute_task', timeout: '10m', retry: '3x / 30s', description: 'Run task via ADK (fallback to static)' },
      { name: 'persist_entities', timeout: '5m', retry: '3x / 30s', description: 'LLM entity extraction from output' },
      { name: 'evaluate_task', timeout: '2m', retry: '3x / 30s', type: 'end', description: 'Score results, update skill proficiency' },
    ],
  },
  {
    id: 'dataset-sync',
    name: 'DatasetSyncWorkflow',
    description: 'Sync datasets through Bronze/Silver/Gold data layers via MCP or direct Databricks SQL',
    queue: 'databricks',
    icon: FaDatabase,
    color: '#34d399',
    steps: [
      { name: 'sync_to_bronze', timeout: '5m', retry: '3x / 5m', type: 'start', description: 'Upload raw data to bronze layer (MCP → Databricks fallback)' },
      { name: 'transform_to_silver', timeout: '10m', retry: '3x / 2m', description: 'Clean and transform to silver layer' },
      { name: 'update_dataset_metadata', timeout: '1m', retry: '5x', type: 'end', description: 'Update sync status and metadata' },
    ],
  },
  {
    id: 'data-source-sync',
    name: 'DataSourceSyncWorkflow',
    description: 'Extract data from connectors (Postgres, Snowflake, MySQL, S3, GCS, API) and load through data layers',
    queue: 'databricks',
    icon: FaSyncAlt,
    color: '#fbbf24',
    steps: [
      { name: 'extract_from_connector', timeout: '30m', retry: '3x / 30s', type: 'start', description: 'Extract via connector (6 types, full/incremental)' },
      { name: 'load_to_bronze', timeout: '15m', retry: '3x / 1m', description: 'Load extracted data to bronze' },
      { name: 'load_to_silver', timeout: '15m', retry: '3x / 1m', description: 'Transform and load to silver' },
      { name: 'update_sync_metadata', timeout: '2m', retry: '5x', type: 'end', description: 'Update watermark and sync status' },
    ],
  },
  {
    id: 'scheduled-sync',
    name: 'ScheduledSyncWorkflow',
    description: 'Parent workflow that runs DataSourceSyncWorkflow for each table in a scheduled sync job',
    queue: 'databricks',
    icon: FaLayerGroup,
    color: '#a78bfa',
    steps: [
      { name: 'for each table', type: 'loop', description: 'Iterate over configured tables' },
      { name: 'DataSourceSyncWorkflow', type: 'child', description: 'Spawn child workflow per table (errors caught per-iteration)' },
    ],
    note: 'Status: "completed" if all succeed, "partial" if some fail',
  },
  {
    id: 'knowledge-extraction',
    name: 'KnowledgeExtractionWorkflow',
    description: 'Extract knowledge entities from chat sessions using LLM analysis',
    queue: 'databricks',
    icon: FaBrain,
    color: '#38bdf8',
    steps: [
      { name: 'extract_knowledge_from_session', timeout: '5m', type: 'start', description: 'LLM entity extraction from chat transcript' },
    ],
    note: 'Skips if: session not found, empty transcript, or LLM not configured',
  },
  {
    id: 'agent-kit-execution',
    name: 'AgentKitExecutionWorkflow',
    description: 'Execute an agent kit task bundle as a durable workflow',
    queue: 'databricks',
    icon: FaRobot,
    color: '#60a5fa',
    steps: [
      { name: 'execute_agent_kit_activity', timeout: '10m', type: 'start', description: 'Run agent kit execution' },
    ],
  },
  {
    id: 'channel-health',
    name: 'ChannelHealthMonitorWorkflow',
    description: 'Long-running monitor: checks channel health, reconnects disconnected accounts, loops via continue_as_new',
    queue: 'orchestration',
    icon: FaHeartbeat,
    color: '#f87171',
    steps: [
      { name: 'check_channel_health', timeout: '1m', retry: '3x / 10s', type: 'start', description: 'Check all channel account statuses' },
      { name: 'reconnect_channel', timeout: '2m/each', retry: '3x / 10s', type: 'loop', description: 'Reconnect each disconnected account' },
      { name: 'update_channel_health_status', timeout: '1m', retry: '3x / 10s', description: 'Update DB with health results' },
      { name: 'sleep(interval)', type: 'timer', description: 'Wait before next check (default 60s)' },
      { name: 'continue_as_new', type: 'loop', description: 'Restart workflow to prevent history growth' },
    ],
  },
  {
    id: 'follow-up',
    name: 'FollowUpWorkflow',
    description: 'Schedule a delayed follow-up action: send WhatsApp, update pipeline stage, or create reminder',
    queue: 'orchestration',
    icon: FaClock,
    color: '#fbbf24',
    steps: [
      { name: 'sleep(delay_hours)', type: 'timer', description: 'Durable timer — waits configured hours' },
      { name: 'execute_followup_action', timeout: '5m', retry: '3x / 30s', type: 'branch', description: 'Routes to: send_whatsapp | update_stage | remind' },
    ],
  },
  {
    id: 'monthly-billing',
    name: 'MonthlyBillingWorkflow',
    description: 'Monthly veterinary billing settlement: aggregate visits, generate invoices, send to clinics, schedule payment follow-ups',
    queue: 'orchestration',
    icon: FaFileInvoiceDollar,
    color: '#34d399',
    steps: [
      { name: 'aggregate_visits', timeout: '5m', retry: '3x / 30s', type: 'start', description: 'Query completed visits per clinic for the billing period' },
      { name: 'generate_invoices', timeout: '10m', retry: '3x / 30s', description: 'Calculate totals from fee schedules and create invoices' },
      { name: 'send_invoices', timeout: '5m', retry: '3x / 30s', description: 'Deliver invoice PDFs via email and WhatsApp' },
      { name: 'schedule_followups', timeout: '1m', retry: '3x / 30s', type: 'end', description: 'Create 7-day reminder workflows for unpaid invoices' },
    ],
  },
];

// ===========================================================================
// Flowchart Step Component
// ===========================================================================
const StepIcon = ({ type }) => {
  const icons = {
    start: { icon: FaPlay, color: '#60a5fa' },
    end: { icon: FaCheckCircle, color: '#34d399' },
    timer: { icon: FaClock, color: '#fbbf24' },
    branch: { icon: FaProjectDiagram, color: '#a78bfa' },
    loop: { icon: FaRedo, color: '#f472b6' },
    child: { icon: FaLayerGroup, color: '#fbbf24' },
  };
  const config = icons[type] || { icon: FaBolt, color: '#60a5fa' };
  const Icon = config.icon;
  return (
    <div className="wf-step-icon" style={{ background: `${config.color}18` }}>
      <Icon size={10} color={config.color} />
    </div>
  );
};

const FlowchartStep = ({ step, index, isLast }) => (
  <div className="wf-step-wrapper">
    <div className={`wf-step ${step.type || ''}`}>
      <div className="wf-step-num">{index + 1}</div>
      <StepIcon type={step.type} />
      <div className="wf-step-content">
        <div className="wf-step-name">{step.name}</div>
        <div className="wf-step-meta">
          {step.timeout && <span><FaClock size={8} /> {step.timeout}</span>}
          {step.retry && <span className="wf-retry-badge"><FaRedo size={7} /> {step.retry}</span>}
        </div>
        {step.description && (
          <div style={{ fontSize: '0.65rem', color: 'var(--color-muted)', marginTop: '0.15rem', lineHeight: 1.3 }}>
            {step.description}
          </div>
        )}
      </div>
    </div>
    {!isLast && (
      <div className="wf-arrow">
        <div className="wf-arrow-line" />
        <div className="wf-arrow-head" />
      </div>
    )}
  </div>
);

// ===========================================================================
// Workflow Card Component
// ===========================================================================
const WorkflowCard = ({ workflow }) => {
  const [expanded, setExpanded] = useState(false);
  const Icon = workflow.icon;

  return (
    <div className="wf-card">
      <div className="wf-card-header" onClick={() => setExpanded(!expanded)}>
        <div className="wf-card-icon" style={{ background: `${workflow.color}18` }}>
          <Icon size={16} color={workflow.color} />
        </div>
        <div className="wf-card-info">
          <div className="wf-card-name">{workflow.name}</div>
          <div className="wf-card-desc">{workflow.description}</div>
        </div>
        <div className="wf-card-badges">
          <span className={`wf-queue-badge ${workflow.queue}`}>
            {workflow.queue}
          </span>
          <Badge bg="dark" style={{ fontSize: '0.62rem', fontWeight: 500, padding: '0.15rem 0.4rem' }}>
            {workflow.steps.length} step{workflow.steps.length !== 1 ? 's' : ''}
          </Badge>
        </div>
        <FaChevronRight size={12} className={`wf-card-chevron ${expanded ? 'open' : ''}`} />
      </div>

      {expanded && (
        <div className="wf-flowchart">
          <div className="wf-steps">
            {workflow.steps.map((step, i) => (
              <FlowchartStep
                key={step.name}
                step={step}
                index={i}
                isLast={i === workflow.steps.length - 1}
              />
            ))}
          </div>
          {workflow.note && (
            <div style={{
              marginTop: '0.75rem',
              padding: '0.4rem 0.6rem',
              borderRadius: '6px',
              background: 'rgba(96, 165, 250, 0.06)',
              border: '1px solid rgba(96, 165, 250, 0.15)',
              fontSize: '0.68rem',
              color: 'var(--color-muted)',
              fontStyle: 'italic',
            }}>
              {workflow.note}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

// ===========================================================================
// Designs Tab Content
// ===========================================================================
const DesignsTab = () => (
  <div>
    <div style={{ marginBottom: '1rem' }}>
      <p style={{ color: 'var(--color-muted)', fontSize: '0.82rem', margin: 0 }}>
        Visual structure of all Temporal workflows. Click a workflow to expand its step-by-step flowchart.
      </p>
    </div>

    {/* Queue summary */}
    <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
      <div style={{
        padding: '0.5rem 0.85rem',
        borderRadius: '8px',
        background: 'rgba(96, 165, 250, 0.06)',
        border: '1px solid rgba(96, 165, 250, 0.15)',
        fontSize: '0.72rem',
        color: '#60a5fa',
        fontWeight: 600,
      }}>
        <FaServer size={10} style={{ marginRight: '0.3rem' }} />
        orchestration queue — {WORKFLOW_DEFINITIONS.filter(w => w.queue === 'orchestration').length} workflows
      </div>
      <div style={{
        padding: '0.5rem 0.85rem',
        borderRadius: '8px',
        background: 'rgba(52, 211, 153, 0.06)',
        border: '1px solid rgba(52, 211, 153, 0.15)',
        fontSize: '0.72rem',
        color: '#34d399',
        fontWeight: 600,
      }}>
        <FaServer size={10} style={{ marginRight: '0.3rem' }} />
        databricks queue — {WORKFLOW_DEFINITIONS.filter(w => w.queue === 'databricks').length} workflows
      </div>
    </div>

    <div className="wf-designs-grid">
      {WORKFLOW_DEFINITIONS.map((wf) => (
        <WorkflowCard key={wf.id} workflow={wf} />
      ))}
    </div>
  </div>
);

// ===========================================================================
// EXECUTIONS TAB — Workflow Audit (from TaskConsolePage)
// ===========================================================================

const STATUS_COLORS = {
  queued: 'secondary',
  thinking: 'info',
  executing: 'warning',
  waiting_input: 'danger',
  completed: 'success',
  failed: 'danger',
  RUNNING: 'warning',
  COMPLETED: 'success',
  FAILED: 'danger',
  CANCELED: 'secondary',
  TERMINATED: 'secondary',
  TIMED_OUT: 'danger',
};

const REFRESH_OPTIONS = [
  { label: '5s', value: 5000 },
  { label: '10s', value: 10000 },
  { label: '30s', value: 30000 },
  { label: 'Off', value: 0 },
];

const EXEC_TABS = [
  { key: 'all', label: 'All' },
  { key: 'agent_task', label: 'Agent Tasks' },
  { key: 'chat', label: 'Chat' },
  { key: 'provision', label: 'Provisioning' },
  { key: 'pipeline', label: 'Data Pipelines' },
  { key: 'sync', label: 'Data Sync' },
];

const TYPE_ICONS = {
  agent_task: FaRobot,
  chat: FaComments,
  research: FaRobot,
  analyze: FaRobot,
  generate: FaRobot,
  decide: FaRobot,
  execute: FaRobot,
  DatasetSyncWorkflow: FaDatabase,
  DataSourceSyncWorkflow: FaSyncAlt,
  ScheduledSyncWorkflow: FaSyncAlt,
  TaskExecutionWorkflow: FaClipboardList,
  KnowledgeExtractionWorkflow: FaDatabase,
  AgentKitExecutionWorkflow: FaRobot,
};

const TYPE_COLORS = {
  agent_task: '#60a5fa',
  chat: '#38bdf8',
  DatasetSyncWorkflow: '#34d399',
  DataSourceSyncWorkflow: '#fbbf24',
  TaskExecutionWorkflow: '#f472b6',
};

const formatStatus = (status) => {
  if (!status) return 'Unknown';
  return status.replace(/_/g, ' ');
};

const timeAgo = (dateStr) => {
  if (!dateStr) return '';
  const diff = Date.now() - new Date(dateStr).getTime();
  if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return `${Math.floor(diff / 86400000)}d ago`;
};

const formatDuration = (startStr, endStr) => {
  if (!startStr) return '-';
  const start = new Date(startStr).getTime();
  const end = endStr ? new Date(endStr).getTime() : Date.now();
  const ms = end - start;
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3600000) return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
  return `${Math.floor(ms / 3600000)}h ${Math.floor((ms % 3600000) / 60000)}m`;
};

const truncateId = (id) => {
  if (!id) return '';
  return id.length > 20 ? id.slice(0, 8) + '...' + id.slice(-6) : id;
};

const copyToClipboard = (text) => {
  navigator.clipboard.writeText(text).catch(() => {});
};

const getTypeIcon = (type) => TYPE_ICONS[type] || FaCog;

const matchesExecTab = (item, tab) => {
  if (tab === 'all') return true;
  if (tab === 'agent_task') return item.source === 'agent_task' && item.type !== 'chat';
  if (tab === 'chat') return item.type === 'chat';
  if (tab === 'provision') return (item.type || '').includes('Provision');
  if (tab === 'pipeline') return (item.type || '').includes('DatasetSync') || (item.type || '').includes('KnowledgeExtraction');
  if (tab === 'sync') return (item.type || '').includes('DataSourceSync') || (item.type || '').includes('ScheduledSync');
  return true;
};

const StatCard = ({ icon: Icon, label, value, color }) => (
  <Card style={{
    background: 'var(--surface-elevated)',
    border: '1px solid var(--color-border)',
    borderRadius: '10px',
    padding: '1rem',
    flex: 1,
    minWidth: '140px',
  }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
      <div style={{
        width: '2.2rem',
        height: '2.2rem',
        borderRadius: '8px',
        background: `${color}18`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}>
        <Icon size={14} color={color} />
      </div>
      <div>
        <div style={{ fontSize: '1.3rem', fontWeight: 700, color: 'var(--color-foreground)', lineHeight: 1.1 }}>
          {typeof value === 'number' ? value.toLocaleString() : value}
        </div>
        <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)', textTransform: 'uppercase', letterSpacing: '0.04em', fontWeight: 600 }}>
          {label}
        </div>
      </div>
    </div>
  </Card>
);

const MetadataGrid = ({ item }) => {
  const cells = [
    { label: 'Started', value: item.start_time ? new Date(item.start_time).toLocaleString() : '-' },
    { label: 'Completed', value: item.close_time ? new Date(item.close_time).toLocaleString() : '-' },
    { label: 'Duration', value: formatDuration(item.start_time, item.close_time) },
    { label: 'Type', value: item.type || '-' },
    { label: 'Priority', value: item.priority || '-' },
    item.confidence != null
      ? { label: 'Confidence', value: `${(item.confidence * 100).toFixed(0)}%` }
      : { label: 'Trace Steps', value: item.trace_count ?? item.history_length ?? '-' },
    { label: 'Tokens', value: item.tokens_used ? item.tokens_used.toLocaleString() : '-' },
    { label: 'Cost', value: item.cost ? `$${item.cost.toFixed(4)}` : '-' },
    item.error
      ? { label: 'Error', value: item.error, isError: true }
      : { label: 'Source', value: item.source === 'temporal' ? 'Temporal Workflow' : 'Agent Task' },
  ];

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(3, 1fr)',
      gap: '0.6rem',
      marginBottom: '1.25rem',
    }}>
      {cells.map((cell) => (
        <div key={cell.label} style={{
          background: 'var(--surface-contrast)',
          borderRadius: '6px',
          padding: '0.5rem 0.7rem',
          border: cell.isError ? '1px solid rgba(220,38,38,0.3)' : '1px solid var(--color-border)',
        }}>
          <div style={{ fontSize: '0.68rem', color: 'var(--color-muted)', textTransform: 'uppercase', letterSpacing: '0.04em', fontWeight: 600, marginBottom: '0.15rem' }}>
            {cell.label}
          </div>
          <div style={{
            fontSize: '0.82rem',
            fontWeight: 500,
            color: cell.isError ? '#f87171' : 'var(--color-foreground)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}>
            {cell.value}
          </div>
        </div>
      ))}
    </div>
  );
};

const WorkflowDetailModal = ({
  show,
  onHide,
  item,
  combinedTraces,
  showRawOutput,
  setShowRawOutput,
  onApprove,
  onReject,
}) => {
  if (!item) return null;

  const IconComp = getTypeIcon(item.type);
  const iconColor = TYPE_COLORS[item.type] || TYPE_COLORS.agent_task;

  return (
    <Modal
      show={show}
      onHide={onHide}
      size="lg"
      centered
      scrollable
      contentClassName="workflow-detail-modal"
    >
      <Modal.Header
        closeButton
        style={{
          background: 'var(--surface-contrast)',
          borderBottom: '1px solid var(--color-border)',
          padding: '1rem 1.25rem',
        }}
      >
        <Modal.Title style={{ fontSize: '1rem', fontWeight: 600, color: 'var(--color-foreground)', display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <div style={{
            width: '2rem',
            height: '2rem',
            borderRadius: '6px',
            background: `${iconColor}18`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}>
            <IconComp size={12} color={iconColor} />
          </div>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {item.objective || item.type || 'Workflow Detail'}
          </span>
        </Modal.Title>
      </Modal.Header>

      <Modal.Body style={{ background: 'var(--surface-elevated)', padding: '1.25rem' }}>
        <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap', alignItems: 'center', marginBottom: '1rem' }}>
          <Badge bg={STATUS_COLORS[item.status] || 'secondary'} style={{ fontSize: '0.75rem', fontWeight: 500, textTransform: 'capitalize', padding: '0.3rem 0.6rem' }}>
            {formatStatus(item.status)}
          </Badge>
          <Badge bg="dark" style={{ fontSize: '0.72rem', fontWeight: 400, padding: '0.25rem 0.5rem' }}>
            {item.type || 'agent_task'}
          </Badge>
          {item.workflow_id && (
            <Badge
              bg="dark"
              style={{ fontSize: '0.7rem', fontWeight: 400, padding: '0.25rem 0.5rem', cursor: 'pointer' }}
              onClick={() => copyToClipboard(item.workflow_id)}
              title="Click to copy workflow ID"
            >
              <FaCopy size={9} style={{ marginRight: '0.25rem' }} />
              {truncateId(item.workflow_id)}
            </Badge>
          )}
          {item.task_id && (
            <Badge
              bg="dark"
              style={{ fontSize: '0.7rem', fontWeight: 400, padding: '0.25rem 0.5rem', cursor: 'pointer' }}
              onClick={() => copyToClipboard(item.task_id)}
              title="Click to copy task ID"
            >
              <FaCopy size={9} style={{ marginRight: '0.25rem' }} />
              Task: {truncateId(item.task_id)}
            </Badge>
          )}
          {item.status === 'waiting_input' && item.task_id && (
            <div style={{ marginLeft: 'auto', display: 'flex', gap: '0.3rem' }}>
              <Button variant="outline-success" size="sm" onClick={(e) => onApprove(item.task_id, e)} style={{ padding: '0.15rem 0.5rem', fontSize: '0.72rem' }}>
                <FaCheck size={10} style={{ marginRight: '0.2rem' }} /> Approve
              </Button>
              <Button variant="outline-danger" size="sm" onClick={(e) => onReject(item.task_id, e)} style={{ padding: '0.15rem 0.5rem', fontSize: '0.72rem' }}>
                <FaTimes size={10} style={{ marginRight: '0.2rem' }} /> Reject
              </Button>
            </div>
          )}
        </div>

        <MetadataGrid item={item} />

        {item.pipeline_run && (
          <div style={{
            background: 'var(--surface-contrast)',
            borderRadius: '6px',
            padding: '0.6rem 0.75rem',
            marginBottom: '1rem',
            border: '1px solid var(--color-border)',
          }}>
            <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)', textTransform: 'uppercase', letterSpacing: '0.04em', fontWeight: 600, marginBottom: '0.3rem' }}>
              <FaDatabase size={10} style={{ marginRight: '0.3rem' }} />
              Pipeline Run
            </div>
            <div style={{ fontSize: '0.8rem', color: 'var(--color-foreground)' }}>
              Status: <Badge bg={STATUS_COLORS[item.pipeline_run.status] || 'secondary'} style={{ fontSize: '0.68rem' }}>{item.pipeline_run.status}</Badge>
              {item.pipeline_run.error && (
                <span style={{ color: '#f87171', marginLeft: '0.5rem', fontSize: '0.78rem' }}>{item.pipeline_run.error}</span>
              )}
            </div>
          </div>
        )}

        <div style={{ borderTop: '1px solid var(--color-border)', paddingTop: '1rem' }}>
          <h6 style={{
            color: 'var(--color-muted)',
            fontSize: '0.8rem',
            textTransform: 'uppercase',
            letterSpacing: '0.05em',
            marginBottom: '1rem',
            fontWeight: 600,
            display: 'flex',
            alignItems: 'center',
            gap: '0.4rem',
          }}>
            <FaClock size={12} />
            Execution Trace
            {combinedTraces.length > 0 && (
              <Badge bg="info" style={{ fontSize: '0.68rem', fontWeight: 500 }}>
                {combinedTraces.length} steps
              </Badge>
            )}
          </h6>
          <TaskTimeline traces={combinedTraces} />
        </div>

        {(item.source === 'agent_task') && (
          <div style={{ borderTop: '1px solid var(--color-border)', paddingTop: '0.75rem', marginTop: '0.75rem' }}>
            <div
              onClick={() => setShowRawOutput(!showRawOutput)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.4rem',
                cursor: 'pointer',
                color: 'var(--color-muted)',
                fontSize: '0.78rem',
                fontWeight: 600,
                textTransform: 'uppercase',
                letterSpacing: '0.04em',
              }}
            >
              {showRawOutput ? <FaChevronDown size={10} /> : <FaChevronRight size={10} />}
              Raw Data
            </div>
            {showRawOutput && (
              <pre style={{
                background: 'var(--surface-contrast)',
                borderRadius: '6px',
                padding: '0.75rem',
                marginTop: '0.5rem',
                fontSize: '0.72rem',
                color: 'var(--color-soft)',
                fontFamily: '"SF Mono", "Fira Code", monospace',
                maxHeight: '300px',
                overflowY: 'auto',
                border: '1px solid var(--color-border)',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}>
                {JSON.stringify(item, null, 2)}
              </pre>
            )}
          </div>
        )}
      </Modal.Body>
    </Modal>
  );
};

const ExecutionsTab = () => {
  const [workflows, setWorkflows] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selectedItem, setSelectedItem] = useState(null);
  const [showModal, setShowModal] = useState(false);
  const [traces, setTraces] = useState([]);
  const [workflowHistory, setWorkflowHistory] = useState([]);
  const [showRawOutput, setShowRawOutput] = useState(false);
  const [activeTab, setActiveTab] = useState('all');
  const [statusFilter, setStatusFilter] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [sortBy, setSortBy] = useState('newest');
  const [refreshInterval, setRefreshInterval] = useState(10000);
  const intervalRef = useRef(null);

  const fetchWorkflows = useCallback(async () => {
    try {
      const params = {};
      if (statusFilter) params.status = statusFilter;
      const res = await taskService.listWorkflows(params);
      setWorkflows(res.data?.workflows || []);
    } catch (err) {
      console.error('Failed to fetch workflows:', err);
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  const fetchStats = useCallback(async () => {
    try {
      const res = await taskService.getWorkflowStats();
      setStats(res.data);
    } catch (err) {
      console.error('Failed to fetch stats:', err);
    }
  }, []);

  const fetchTrace = useCallback(async (taskId) => {
    try {
      const res = await taskService.getTrace(taskId);
      setTraces(res.data?.steps || res.data || []);
    } catch {
      setTraces([]);
    }
  }, []);

  const fetchWorkflowHistory = useCallback(async (workflowId) => {
    try {
      const res = await taskService.getWorkflowHistory(workflowId);
      setWorkflowHistory(res.data?.events || []);
    } catch {
      setWorkflowHistory([]);
    }
  }, []);

  const selectItem = useCallback((item) => {
    setSelectedItem(item);
    setShowModal(true);
    setShowRawOutput(false);
    if (item?.task_id) fetchTrace(item.task_id);
    else setTraces([]);
    if (item?.workflow_id) fetchWorkflowHistory(item.workflow_id);
    else setWorkflowHistory([]);
  }, [fetchTrace, fetchWorkflowHistory]);

  const handleApprove = async (taskId, e) => {
    e.stopPropagation();
    try {
      await taskService.approve(taskId);
      fetchWorkflows();
    } catch (err) {
      console.error('Failed to approve task:', err);
    }
  };

  const handleReject = async (taskId, e) => {
    e.stopPropagation();
    try {
      await taskService.reject(taskId);
      fetchWorkflows();
    } catch (err) {
      console.error('Failed to reject task:', err);
    }
  };

  const handleRefresh = () => {
    setLoading(true);
    fetchWorkflows();
    fetchStats();
  };

  useEffect(() => {
    fetchWorkflows();
    fetchStats();
  }, [fetchWorkflows, fetchStats]);

  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    if (refreshInterval > 0) {
      intervalRef.current = setInterval(() => {
        fetchWorkflows();
        fetchStats();
      }, refreshInterval);
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [refreshInterval, fetchWorkflows, fetchStats]);

  const filteredWorkflows = useMemo(() => {
    let items = workflows.filter((w) => matchesExecTab(w, activeTab));
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      items = items.filter((w) =>
        (w.objective || '').toLowerCase().includes(q) ||
        (w.workflow_id || '').toLowerCase().includes(q) ||
        (w.task_id || '').toLowerCase().includes(q) ||
        (w.type || '').toLowerCase().includes(q)
      );
    }
    if (sortBy === 'oldest') items = [...items].reverse();
    if (sortBy === 'duration') {
      items = [...items].sort((a, b) => {
        const dA = a.start_time && a.close_time ? new Date(a.close_time) - new Date(a.start_time) : 0;
        const dB = b.start_time && b.close_time ? new Date(b.close_time) - new Date(b.start_time) : 0;
        return dB - dA;
      });
    }
    if (sortBy === 'cost') items = [...items].sort((a, b) => (b.cost || 0) - (a.cost || 0));
    return items;
  }, [workflows, activeTab, searchQuery, sortBy]);

  const combinedTraces = useMemo(() => {
    if (traces.length > 0) return traces;
    return workflowHistory.map((evt) => ({
      step_type: evt.activity_name || evt.event_type,
      details: evt.details,
      duration_ms: evt.duration_ms,
      created_at: evt.timestamp,
    }));
  }, [traces, workflowHistory]);

  return (
    <>
      <style>{`
        .workflow-detail-modal {
          background: var(--surface-elevated) !important;
          border: 1px solid var(--color-border) !important;
          border-radius: 12px !important;
        }
        .workflow-detail-modal .btn-close {
          filter: invert(1) grayscale(100%) brightness(200%);
        }
        .workflow-table-row:hover {
          background: var(--surface-contrast) !important;
        }
      `}</style>

      {/* Header row with refresh */}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', alignItems: 'center', marginBottom: '1rem' }}>
        <Form.Select
          size="sm"
          value={refreshInterval}
          onChange={(e) => setRefreshInterval(Number(e.target.value))}
          style={{
            width: '80px',
            background: 'var(--surface-elevated)',
            color: 'var(--color-soft)',
            border: '1px solid var(--color-border)',
            fontSize: '0.75rem',
          }}
        >
          {REFRESH_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </Form.Select>
        <Button variant="outline-secondary" size="sm" onClick={handleRefresh} disabled={loading}>
          <FaSyncAlt className={loading ? 'fa-spin' : ''} style={{ marginRight: '0.3rem' }} />
          Refresh
        </Button>
      </div>

      {/* Stats */}
      <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1.25rem', overflowX: 'auto' }}>
        <StatCard icon={FaStream} label="Total Workflows" value={stats?.total_workflows ?? '-'} color="#60a5fa" />
        <StatCard icon={FaPlay} label="Running" value={stats?.running_count ?? '-'} color="#fbbf24" />
        <StatCard icon={FaCheckCircle} label="Completed" value={stats?.completed_count ?? '-'} color="#34d399" />
        <StatCard icon={FaTimesCircle} label="Failed" value={stats?.failed_count ?? '-'} color="#f87171" />
        <StatCard icon={FaCoins} label="Total Tokens" value={stats?.total_tokens ?? '-'} color="#a78bfa" />
        <StatCard icon={FaDollarSign} label="Total Cost" value={stats?.total_cost != null ? `$${stats.total_cost.toFixed(2)}` : '-'} color="#f472b6" />
      </div>

      {/* Workflow Table */}
      <Card style={{
        background: 'var(--surface-elevated)',
        border: '1px solid var(--color-border)',
        borderRadius: '10px',
      }}>
        {/* Sub-tabs */}
        <div style={{
          borderBottom: '1px solid var(--color-border)',
          padding: '0 0.75rem',
          background: 'var(--surface-contrast)',
          borderRadius: '10px 10px 0 0',
        }}>
          <Nav variant="tabs" style={{ borderBottom: 'none' }}>
            {EXEC_TABS.map((tab) => (
              <Nav.Item key={tab.key}>
                <Nav.Link
                  active={activeTab === tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  style={{
                    fontSize: '0.75rem',
                    fontWeight: 600,
                    color: activeTab === tab.key ? 'var(--color-foreground)' : 'var(--color-muted)',
                    background: 'transparent',
                    border: 'none',
                    borderBottom: activeTab === tab.key ? '2px solid #60a5fa' : '2px solid transparent',
                    padding: '0.6rem 0.7rem',
                    borderRadius: 0,
                  }}
                >
                  {tab.label}
                </Nav.Link>
              </Nav.Item>
            ))}
          </Nav>
        </div>

        {/* Filters */}
        <div style={{
          display: 'flex',
          gap: '0.4rem',
          padding: '0.5rem 0.75rem',
          borderBottom: '1px solid var(--color-border)',
        }}>
          <Form.Control
            size="sm"
            placeholder="Search by objective, workflow ID, or type..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              flex: 1,
              background: 'var(--surface-contrast)',
              color: 'var(--color-soft)',
              border: '1px solid var(--color-border)',
              fontSize: '0.75rem',
            }}
          />
          <Form.Select
            size="sm"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            style={{
              width: '120px',
              background: 'var(--surface-contrast)',
              color: 'var(--color-soft)',
              border: '1px solid var(--color-border)',
              fontSize: '0.75rem',
            }}
          >
            <option value="">All Status</option>
            <option value="queued">Queued</option>
            <option value="executing">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
          </Form.Select>
          <Form.Select
            size="sm"
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            style={{
              width: '110px',
              background: 'var(--surface-contrast)',
              color: 'var(--color-soft)',
              border: '1px solid var(--color-border)',
              fontSize: '0.75rem',
            }}
          >
            <option value="newest">Newest</option>
            <option value="oldest">Oldest</option>
            <option value="duration">Duration</option>
            <option value="cost">Cost</option>
          </Form.Select>
        </div>

        {/* Table Header */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: '2.5rem 1fr 140px 100px 80px 80px 90px',
          gap: '0.5rem',
          padding: '0.5rem 0.85rem',
          borderBottom: '1px solid var(--color-border)',
          background: 'var(--surface-contrast)',
          fontSize: '0.7rem',
          fontWeight: 600,
          color: 'var(--color-muted)',
          textTransform: 'uppercase',
          letterSpacing: '0.04em',
        }}>
          <span></span>
          <span>Workflow</span>
          <span>Type</span>
          <span>Status</span>
          <span style={{ textAlign: 'right' }}>Tokens</span>
          <span style={{ textAlign: 'right' }}>Cost</span>
          <span style={{ textAlign: 'right' }}>Duration</span>
        </div>

        {/* Rows */}
        <div style={{ maxHeight: 'calc(100vh - 520px)', overflowY: 'auto' }}>
          {loading && workflows.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--color-muted)' }}>
              <Spinner animation="border" size="sm" style={{ marginRight: '0.5rem' }} />
              Loading workflows...
            </div>
          ) : filteredWorkflows.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--color-muted)' }}>
              No workflows found
            </div>
          ) : (
            filteredWorkflows.map((item, idx) => {
              const IconComp = getTypeIcon(item.type);
              const iconColor = TYPE_COLORS[item.type] || TYPE_COLORS.agent_task;
              const statusColor = STATUS_COLORS[item.status] || 'secondary';

              return (
                <div
                  key={item.workflow_id || item.task_id || idx}
                  className="workflow-table-row"
                  onClick={() => selectItem(item)}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: '2.5rem 1fr 140px 100px 80px 80px 90px',
                    gap: '0.5rem',
                    padding: '0.65rem 0.85rem',
                    cursor: 'pointer',
                    borderBottom: '1px solid var(--color-border)',
                    alignItems: 'center',
                    transition: 'background 0.15s',
                  }}
                >
                  <div style={{
                    width: '2rem',
                    height: '2rem',
                    borderRadius: '6px',
                    background: `${iconColor}18`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}>
                    <IconComp size={12} color={iconColor} />
                  </div>

                  <div style={{ minWidth: 0 }}>
                    <div style={{
                      fontSize: '0.82rem',
                      fontWeight: 500,
                      color: 'var(--color-foreground)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}>
                      {item.objective || item.type || 'Workflow'}
                    </div>
                    <div style={{ fontSize: '0.7rem', color: 'var(--color-muted)', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                      {item.workflow_id && <span title={item.workflow_id}>{truncateId(item.workflow_id)}</span>}
                      {item.task_id && <span title={item.task_id}>{truncateId(item.task_id)}</span>}
                      <span>{timeAgo(item.start_time)}</span>
                    </div>
                  </div>

                  <div style={{ fontSize: '0.72rem', color: 'var(--color-soft)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {item.type || 'agent_task'}
                  </div>

                  <div>
                    <Badge bg={statusColor} style={{ fontSize: '0.68rem', fontWeight: 500, textTransform: 'capitalize' }}>
                      {formatStatus(item.status)}
                    </Badge>
                  </div>

                  <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)', textAlign: 'right' }}>
                    {item.tokens_used > 0 ? item.tokens_used.toLocaleString() : '-'}
                  </div>

                  <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)', textAlign: 'right' }}>
                    {item.cost > 0 ? `$${item.cost.toFixed(4)}` : '-'}
                  </div>

                  <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)', textAlign: 'right' }}>
                    {formatDuration(item.start_time, item.close_time)}
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Footer */}
        <div style={{
          padding: '0.4rem 0.75rem',
          borderTop: '1px solid var(--color-border)',
          fontSize: '0.72rem',
          color: 'var(--color-muted)',
          display: 'flex',
          justifyContent: 'space-between',
        }}>
          <span>{filteredWorkflows.length} of {workflows.length} workflows</span>
          {stats?.temporal_available === false && (
            <span style={{ color: '#fbbf24' }}>
              <FaExclamationTriangle size={10} style={{ marginRight: '0.2rem' }} />
              Temporal offline — showing DB data only
            </span>
          )}
        </div>
      </Card>

      <WorkflowDetailModal
        show={showModal}
        onHide={() => setShowModal(false)}
        item={selectedItem}
        combinedTraces={combinedTraces}
        showRawOutput={showRawOutput}
        setShowRawOutput={setShowRawOutput}
        onApprove={handleApprove}
        onReject={handleReject}
      />
    </>
  );
};

// ===========================================================================
// Main WorkflowsPage
// ===========================================================================
const WorkflowsPage = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeMainTab = searchParams.get('tab') || 'designs';

  const setTab = (tab) => {
    setSearchParams({ tab });
  };

  return (
    <Layout>
      <div style={{ padding: '1.5rem' }}>
        <div style={{ marginBottom: '0.5rem' }}>
          <h4 style={{ color: 'var(--color-foreground)', marginBottom: '0.25rem', fontWeight: 600 }}>
            Workflows
          </h4>
          <p style={{ color: 'var(--color-muted)', fontSize: '0.85rem', margin: 0 }}>
            Workflow architecture and execution audit
          </p>
        </div>

        <div className="workflows-tabs">
          <button
            className={`workflows-tab-btn ${activeMainTab === 'designs' ? 'active' : ''}`}
            onClick={() => setTab('designs')}
          >
            <FaDraftingCompass size={12} style={{ marginRight: '0.4rem' }} />
            Designs
          </button>
          <button
            className={`workflows-tab-btn ${activeMainTab === 'executions' ? 'active' : ''}`}
            onClick={() => setTab('executions')}
          >
            <FaStream size={12} style={{ marginRight: '0.4rem' }} />
            Executions
          </button>
        </div>

        {activeMainTab === 'designs' && <DesignsTab />}
        {activeMainTab === 'executions' && <ExecutionsTab />}
      </div>
    </Layout>
  );
};

export default WorkflowsPage;
