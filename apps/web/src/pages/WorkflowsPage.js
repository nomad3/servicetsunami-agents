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
import { useTranslation } from 'react-i18next';
import {
  FaBinoculars,
  FaBolt,
  FaBrain,
  FaBullseye,
  FaCheck,
  FaCheckCircle,
  FaChevronDown,
  FaChevronRight,
  FaClipboardList,
  FaClock,
  FaCode,
  FaCog,
  FaCoins,
  FaComments,
  FaCopy,
  FaDatabase,
  FaDollarSign,
  FaDraftingCompass,
  FaFileInvoiceDollar,
  FaEnvelope,
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
import DynamicWorkflowsTab from '../components/workflows/DynamicWorkflowsTab';
import TemplatesTab from '../components/workflows/TemplatesTab';
import RunsTab from '../components/workflows/RunsTab';
import dynamicWorkflowService from '../services/dynamicWorkflowService';
import './WorkflowsPage.css';

// ===========================================================================
// WORKFLOW DEFINITIONS — Static structure of all Temporal workflows
// ===========================================================================
// Convention: "Category · Display Name" — makes domain obvious at a glance
// Categories: Platform (core infra), Data (lakehouse), Sales (pipeline/deals),
//             Marketing (ads/competitors), Industry (HealthPets/Remedia)
const WORKFLOW_DEFINITIONS = [
  // ── Platform ──────────────────────────────────────────────────────────
  {
    id: 'task-execution',
    name: 'Platform · Task Execution',
    temporalName: 'TaskExecutionWorkflow',
    description: 'End-to-end agent task execution: dispatch, memory recall, execute via ADK, persist entities, evaluate results',
    category: 'platform',
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
    id: 'inbox-monitor',
    name: 'Platform · Inbox Monitor',
    temporalName: 'InboxMonitorWorkflow',
    description: 'Long-running per-tenant monitor: fetches Gmail + Calendar, triages with LLM, creates notifications, extracts entities',
    category: 'platform',
    queue: 'orchestration',
    icon: FaStream,
    color: '#818cf8',
    steps: [
      { name: 'fetch_new_emails', timeout: '2m', retry: '3x / 15s', type: 'start', description: 'Fetch new emails via Gmail API using history ID' },
      { name: 'fetch_upcoming_events', timeout: '2m', retry: '3x / 15s', description: 'Fetch calendar events for next 24 hours' },
      { name: 'triage_items', timeout: '3m', retry: '3x / 15s', description: 'LLM triage with memory context enrichment (priority, category, summary)' },
      { name: 'create_notifications', timeout: '2m', retry: '3x / 15s', description: 'Create notifications (deduplicates by reference_id)' },
      { name: 'extract_from_emails', timeout: '5m', retry: '3x / 15s', description: 'Extract entities/relations/memories from important emails' },
      { name: 'log_monitor_cycle', timeout: '2m', retry: '3x / 15s', description: 'Log scan cycle to memory activity' },
      { name: 'sleep(15min)', type: 'timer', description: 'Wait before next scan cycle' },
      { name: 'continue_as_new', type: 'loop', description: 'Restart to prevent history growth' },
    ],
    note: 'One instance per tenant. Auto-starts when Google OAuth is connected.',
  },
  {
    id: 'channel-health',
    name: 'Platform · Channel Health Monitor',
    temporalName: 'ChannelHealthMonitorWorkflow',
    description: 'Long-running monitor: checks channel health, reconnects disconnected accounts, loops via continue_as_new',
    category: 'platform',
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
    name: 'Platform · Follow-Up Action',
    temporalName: 'FollowUpWorkflow',
    description: 'Schedule a delayed follow-up action: send WhatsApp, update pipeline stage, or create reminder',
    category: 'platform',
    queue: 'orchestration',
    icon: FaClock,
    color: '#fbbf24',
    steps: [
      { name: 'sleep(delay_hours)', type: 'timer', description: 'Durable timer — waits configured hours' },
      { name: 'execute_followup_action', timeout: '5m', retry: '3x / 30s', type: 'branch', description: 'Routes to: send_whatsapp | update_stage | remind' },
    ],
  },
  {
    id: 'auto-action',
    name: 'Platform · Auto Action',
    temporalName: 'AutoActionWorkflow',
    description: 'Memory-triggered automated actions: routes through Luna to appropriate sub-agent team for execution',
    category: 'platform',
    queue: 'orchestration',
    icon: FaBolt,
    color: '#ec4899',
    steps: [
      { name: 'sleep(delay)', type: 'timer', description: 'Optional delay before execution (0 = immediate)' },
      { name: 'execute_auto_action', timeout: '10m', retry: '3x / 30s', type: 'start', description: 'Execute via ADK: reply_email, send_whatsapp, research, analyze, create_task' },
    ],
    note: 'Triggered by memory extraction action_triggers',
  },
  {
    id: 'code-task',
    name: 'Platform · Code Task (Claude Code)',
    temporalName: 'CodeTaskWorkflow',
    description: 'Autonomous coding via Claude Code CLI: clones repo, implements feature, creates branch and PR',
    category: 'platform',
    queue: 'code',
    icon: FaCode,
    color: '#22d3ee',
    steps: [
      { name: 'execute_code_task', timeout: '30m', retry: '1x', type: 'start', description: 'Run Claude Code CLI in isolated code-worker pod — creates branch, implements changes, opens PR' },
    ],
    note: 'Runs on dedicated code-worker pod with Node.js 20 + Python. Triggered via Code Agent in chat.',
  },
  // ── Data ──────────────────────────────────────────────────────────────
  {
    id: 'dataset-sync',
    name: 'Data · Dataset Sync',
    temporalName: 'DatasetSyncWorkflow',
    description: 'Sync datasets through Bronze/Silver/Gold data layers via MCP or direct PostgreSQL SQL',
    category: 'data',
    queue: 'postgres',
    icon: FaDatabase,
    color: '#34d399',
    steps: [
      { name: 'sync_to_bronze', timeout: '5m', retry: '3x / 5m', type: 'start', description: 'Upload raw data to bronze layer (MCP → PostgreSQL fallback)' },
      { name: 'transform_to_silver', timeout: '10m', retry: '3x / 2m', description: 'Clean and transform to silver layer' },
      { name: 'update_dataset_metadata', timeout: '1m', retry: '5x', type: 'end', description: 'Update sync status and metadata' },
    ],
  },
  {
    id: 'data-source-sync',
    name: 'Data · Data Source Sync',
    temporalName: 'DataSourceSyncWorkflow',
    description: 'Extract data from connectors (Postgres, Snowflake, MySQL, S3, GCS, API) and load through data layers',
    category: 'data',
    queue: 'postgres',
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
    name: 'Data · Scheduled Sync',
    temporalName: 'ScheduledSyncWorkflow',
    description: 'Parent workflow that runs Data Source Sync for each table in a scheduled sync job',
    category: 'data',
    queue: 'postgres',
    icon: FaLayerGroup,
    color: '#a78bfa',
    steps: [
      { name: 'for each table', type: 'loop', description: 'Iterate over configured tables' },
      { name: 'DataSourceSyncWorkflow', type: 'child', description: 'Spawn child workflow per table (errors caught per-iteration)' },
    ],
    note: 'Status: "completed" if all succeed, "partial" if some fail',
  },
  // ── Sales ─────────────────────────────────────────────────────────────
  {
    id: 'prospecting-pipeline',
    name: 'Sales · Prospecting Pipeline',
    temporalName: 'ProspectingPipelineWorkflow',
    description: 'Durable 5-step prospecting pipeline: research, score, qualify, outreach, notify',
    category: 'sales',
    queue: 'orchestration',
    icon: FaBullseye,
    color: '#34d399',
    steps: [
      { name: 'prospect_research', timeout: '5m', retry: '3x / 10s', type: 'start', description: 'Enrich prospect entities with external data' },
      { name: 'prospect_score', timeout: '5m', retry: '3x / 10s', description: 'Score each prospect via lead-scoring rubric' },
      { name: 'prospect_qualify', timeout: '3m', retry: '3x / 10s', description: 'Apply BANT qualification filter (threshold-based)' },
      { name: 'prospect_outreach', timeout: '5m', retry: '3x / 10s', description: 'Draft personalised outreach messages' },
      { name: 'prospect_notify', timeout: '2m', retry: '3x / 10s', type: 'end', description: 'Create notification summary for tenant' },
    ],
  },
  {
    id: 'deal-pipeline',
    name: 'Sales · Deal Pipeline (M&A)',
    temporalName: 'DealPipelineWorkflow',
    description: 'Full M&A deal pipeline: discover prospects, score for sell-likelihood, research briefs, outreach generation, pipeline advancement, KG sync',
    category: 'sales',
    queue: 'orchestration',
    icon: FaDollarSign,
    color: '#f59e0b',
    steps: [
      { name: 'hca_discover_prospects', timeout: '5m', retry: '3x / 10s', type: 'start', description: 'AI prospect discovery by industry and criteria' },
      { name: 'hca_score_prospects', timeout: '5m', retry: '3x / 10s', description: 'Score each prospect for sell-likelihood (threshold filtering)' },
      { name: 'hca_generate_research', timeout: '10m', retry: '3x / 10s', description: 'Generate research briefs for high-scorers' },
      { name: 'hca_generate_outreach', timeout: '5m', retry: '3x / 10s', description: 'Create outreach drafts (cold email, LinkedIn, one-pager)' },
      { name: 'hca_advance_pipeline', timeout: '2m', retry: '3x / 10s', description: 'Move prospects to "contacted" stage' },
      { name: 'hca_sync_knowledge_graph', timeout: '5m', retry: '3x / 10s', type: 'end', description: 'Sync prospects to knowledge graph' },
    ],
    note: 'Can skip discovery when triggered via webhook with existing prospect IDs',
  },
  // ── Marketing ─────────────────────────────────────────────────────────
  {
    id: 'competitor-monitor',
    name: 'Marketing · Competitor Monitor',
    temporalName: 'CompetitorMonitorWorkflow',
    description: 'Long-running per-tenant monitor: scrapes competitor websites, checks ad libraries, analyzes changes, stores observations, creates alerts',
    category: 'marketing',
    queue: 'orchestration',
    icon: FaBinoculars,
    color: '#f97316',
    steps: [
      { name: 'fetch_competitors', timeout: '3m', retry: '3x / 15s', type: 'start', description: 'Query knowledge graph for competitor entities' },
      { name: 'scrape_competitor_activity', timeout: '5m', retry: '3x / 15s', description: 'Scrape competitor websites and news via MCP scraper' },
      { name: 'check_ad_libraries', timeout: '5m', retry: '3x / 15s', description: 'Check Meta Ad Library and public ad transparency sources' },
      { name: 'analyze_competitor_changes', timeout: '3m', retry: '3x / 15s', description: 'LLM analysis of changes vs previous observations' },
      { name: 'store_competitor_observations', timeout: '3m', retry: '3x / 15s', description: 'Store observations on competitor knowledge entities' },
      { name: 'create_competitor_notifications', timeout: '3m', retry: '3x / 15s', description: 'Create alerts for notable competitor changes' },
      { name: 'sleep(24h)', type: 'timer', description: 'Wait before next monitoring cycle (default 24h)' },
      { name: 'continue_as_new', type: 'loop', description: 'Restart workflow to prevent history growth' },
    ],
    note: 'One instance per tenant. Started via Luna competitor management tools.',
  },
  // ── Industry · HealthPets ─────────────────────────────────────────────
  {
    id: 'monthly-billing',
    name: 'HealthPets · Monthly Billing',
    temporalName: 'MonthlyBillingWorkflow',
    description: 'Monthly veterinary billing: aggregate clinic visits, generate invoices, send via email/WhatsApp, schedule payment follow-ups',
    category: 'healthpets',
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
  // ── Industry · Remedia ────────────────────────────────────────────────
  {
    id: 'remedia-order',
    name: 'Remedia · Pharmacy Order',
    temporalName: 'RemediaOrderWorkflow',
    description: 'Pharmacy order lifecycle: create order, WhatsApp confirmation, payment monitoring, delivery tracking',
    category: 'remedia',
    queue: 'orchestration',
    icon: FaCoins,
    color: '#10b981',
    steps: [
      { name: 'create_remedia_order', timeout: '2m', retry: '3x / 10s', type: 'start', description: 'POST to Remedia API, get order_id + payment URL' },
      { name: 'send_confirmation', timeout: '1m', retry: '3x / 10s', description: 'WhatsApp message with order summary + payment link' },
      { name: 'monitor_payment', timeout: '35m', retry: '1x', type: 'timer', description: 'Poll order status every 30s until paid or 30min timeout' },
      { name: 'send_payment_confirmed', timeout: '1m', retry: '3x / 10s', description: 'WhatsApp notification on payment success' },
      { name: 'track_delivery', timeout: '25h', retry: '1x', type: 'end', description: 'Poll delivery status every 5min for up to 24h' },
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
          {workflow.temporalName && (
            <div style={{ fontSize: '0.62rem', color: 'var(--color-muted)', fontFamily: '"SF Mono", "Fira Code", monospace', marginBottom: '0.15rem' }}>
              {workflow.temporalName}
            </div>
          )}
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
const DesignsTab = () => {
  const { t } = useTranslation('workflows');
  return (
  <div>
    <div style={{ marginBottom: '1rem' }}>
      <p style={{ color: 'var(--color-muted)', fontSize: '0.82rem', margin: 0 }}>
        {t('designs.description')}
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
        {t('designs.orchestrationQueue')} — {WORKFLOW_DEFINITIONS.filter(w => w.queue === 'orchestration').length} workflows
      </div>
      <div style={{
        padding: '0.5rem 0.85rem',
        borderRadius: '8px',
        background: 'rgba(34, 211, 238, 0.06)',
        border: '1px solid rgba(34, 211, 238, 0.15)',
        fontSize: '0.72rem',
        color: '#22d3ee',
        fontWeight: 600,
      }}>
        <FaServer size={10} style={{ marginRight: '0.3rem' }} />
        {t('designs.codeQueue')} — {WORKFLOW_DEFINITIONS.filter(w => w.queue === 'code').length} workflows
      </div>
    </div>

    {/* Grouped by category */}
    {[
      { key: 'platform', color: '#60a5fa' },
      { key: 'data', color: '#34d399' },
      { key: 'sales', color: '#f59e0b' },
      { key: 'marketing', color: '#f97316' },
      { key: 'healthpets', color: '#f472b6' },
      { key: 'remedia', color: '#10b981' },
    ].map((cat) => {
      const catWorkflows = WORKFLOW_DEFINITIONS.filter(w => w.category === cat.key);
      if (catWorkflows.length === 0) return null;
      return (
        <div key={cat.key} style={{ marginBottom: '1.25rem' }}>
          <h6 style={{
            fontSize: '0.72rem',
            fontWeight: 700,
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
            color: cat.color,
            marginBottom: '0.5rem',
            paddingBottom: '0.3rem',
            borderBottom: `1px solid ${cat.color}25`,
          }}>
            {t(`designs.categories.${cat.key}`)}
            <span style={{ fontWeight: 500, color: 'var(--color-muted)', marginLeft: '0.5rem', fontSize: '0.65rem' }}>
              {catWorkflows.length} workflow{catWorkflows.length !== 1 ? 's' : ''}
            </span>
          </h6>
          <div className="wf-designs-grid">
            {catWorkflows.map((wf) => (
              <WorkflowCard key={wf.id} workflow={wf} />
            ))}
          </div>
        </div>
      );
    })}
  </div>
  );
};

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
  CONTINUED_AS_NEW: 'info',
  ContinuedAsNew: 'info',
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
  InboxMonitorWorkflow: FaEnvelope,
  ChannelHealthMonitorWorkflow: FaHeartbeat,
  ProspectingPipelineWorkflow: FaBullseye,
  CompetitorMonitorWorkflow: FaBinoculars,
  CodeTaskWorkflow: FaCode,
  FollowUpWorkflow: FaRedo,
};

const TYPE_COLORS = {
  agent_task: '#60a5fa',
  chat: '#38bdf8',
  DatasetSyncWorkflow: '#34d399',
  DataSourceSyncWorkflow: '#fbbf24',
  TaskExecutionWorkflow: '#f472b6',
  InboxMonitorWorkflow: '#a78bfa',
  ChannelHealthMonitorWorkflow: '#f87171',
  ProspectingPipelineWorkflow: '#fbbf24',
  CompetitorMonitorWorkflow: '#f97316',
  CodeTaskWorkflow: '#22d3ee',
  FollowUpWorkflow: '#34d399',
};

const formatStatus = (status) => {
  if (!status) return 'Unknown';
  const map = { CONTINUED_AS_NEW: 'Continued', ContinuedAsNew: 'Continued' };
  return map[status] || status.replace(/_/g, ' ');
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
  const { t } = useTranslation('workflows');
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

  const [expandedGroups, setExpandedGroups] = useState({});

  const toggleGroup = useCallback((groupKey) => {
    setExpandedGroups((prev) => ({ ...prev, [groupKey]: !prev[groupKey] }));
  }, []);

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

    // Group repeated Temporal workflow types (e.g., InboxMonitorWorkflow x100)
    const groups = {};
    const result = [];
    for (const item of items) {
      // Only group Temporal workflows (not agent_tasks)
      if (item.source === 'temporal' && item.type) {
        if (!groups[item.type]) {
          groups[item.type] = { latest: item, children: [item], index: result.length };
          result.push({ _isGroup: true, _groupKey: item.type, _items: groups[item.type] });
        } else {
          groups[item.type].children.push(item);
        }
      } else {
        result.push(item);
      }
    }
    return result;
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
          {t('executions.refresh')}
        </Button>
      </div>

      {/* Stats */}
      <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1.25rem', overflowX: 'auto' }}>
        <StatCard icon={FaStream} label={t('executions.stats.totalWorkflows')} value={stats?.total_workflows ?? '-'} color="#60a5fa" />
        <StatCard icon={FaPlay} label={t('executions.stats.running')} value={stats?.running_count ?? '-'} color="#fbbf24" />
        <StatCard icon={FaCheckCircle} label={t('executions.stats.completed')} value={stats?.completed_count ?? '-'} color="#34d399" />
        <StatCard icon={FaTimesCircle} label={t('executions.stats.failed')} value={stats?.failed_count ?? '-'} color="#f87171" />
        <StatCard icon={FaCoins} label={t('executions.stats.totalTokens')} value={stats?.total_tokens ?? '-'} color="#a78bfa" />
        <StatCard icon={FaDollarSign} label={t('executions.stats.totalCost')} value={stats?.total_cost != null ? `$${stats.total_cost.toFixed(2)}` : '-'} color="#f472b6" />
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
            placeholder={t('executions.filter.searchPlaceholder')}
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
            <option value="">{t('executions.filter.allStatus')}</option>
            <option value="queued">{t('executions.filter.queued')}</option>
            <option value="executing">{t('executions.filter.running')}</option>
            <option value="completed">{t('executions.filter.completed')}</option>
            <option value="failed">{t('executions.filter.failed')}</option>
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
            <option value="newest">{t('executions.filter.newest')}</option>
            <option value="oldest">{t('executions.filter.oldest')}</option>
            <option value="duration">{t('executions.filter.sortDuration')}</option>
            <option value="cost">{t('executions.filter.sortCost')}</option>
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
          <span>{t('executions.table.workflow')}</span>
          <span>{t('executions.table.type')}</span>
          <span>{t('executions.table.status')}</span>
          <span style={{ textAlign: 'right' }}>{t('executions.table.tokens')}</span>
          <span style={{ textAlign: 'right' }}>{t('executions.table.cost')}</span>
          <span style={{ textAlign: 'right' }}>{t('executions.table.duration')}</span>
        </div>

        {/* Rows */}
        <div style={{ maxHeight: 'calc(100vh - 520px)', overflowY: 'auto' }}>
          {loading && workflows.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--color-muted)' }}>
              <Spinner animation="border" size="sm" style={{ marginRight: '0.5rem' }} />
              {t('executions.loading')}
            </div>
          ) : filteredWorkflows.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--color-muted)' }}>
              {t('executions.noWorkflows')}
            </div>
          ) : (
            filteredWorkflows.map((entry, idx) => {
              // Grouped Temporal workflows
              if (entry._isGroup) {
                const group = entry._items;
                const item = group.latest;
                const count = group.children.length;
                const isExpanded = expandedGroups[entry._groupKey];
                const IconComp = getTypeIcon(item.type);
                const iconColor = TYPE_COLORS[item.type] || TYPE_COLORS.agent_task;
                const latestStatus = item.status;
                const statusColor = STATUS_COLORS[latestStatus] || 'secondary';
                const runningCount = group.children.filter((c) => c.status === 'RUNNING').length;
                const failedCount = group.children.filter((c) => c.status === 'FAILED').length;

                return (
                  <div key={entry._groupKey}>
                    {/* Group header row */}
                    <div
                      className="workflow-table-row"
                      onClick={() => count > 1 ? toggleGroup(entry._groupKey) : selectItem(item)}
                      style={{
                        display: 'grid',
                        gridTemplateColumns: '2.5rem 1fr 140px 100px 80px 80px 90px',
                        gap: '0.5rem',
                        padding: '0.65rem 0.85rem',
                        cursor: 'pointer',
                        borderBottom: '1px solid var(--color-border)',
                        alignItems: 'center',
                        transition: 'background 0.15s',
                        background: isExpanded ? 'var(--surface-contrast)' : undefined,
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
                        position: 'relative',
                      }}>
                        <IconComp size={12} color={iconColor} />
                      </div>

                      <div style={{ minWidth: 0 }}>
                        <div style={{
                          fontSize: '0.82rem',
                          fontWeight: 500,
                          color: 'var(--color-foreground)',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '0.5rem',
                        }}>
                          {item.type || 'Workflow'}
                          {count > 1 && (
                            <Badge bg="secondary" pill style={{ fontSize: '0.65rem', fontWeight: 600 }}>
                              {count} runs
                            </Badge>
                          )}
                          {count > 1 && (isExpanded ? <FaChevronDown size={9} color="var(--color-muted)" /> : <FaChevronRight size={9} color="var(--color-muted)" />)}
                        </div>
                        <div style={{ fontSize: '0.7rem', color: 'var(--color-muted)', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                          <span>Latest: {timeAgo(item.start_time)}</span>
                          {runningCount > 0 && <span style={{ color: '#fbbf24' }}>{runningCount} running</span>}
                          {failedCount > 0 && <span style={{ color: '#f87171' }}>{failedCount} failed</span>}
                        </div>
                      </div>

                      <div style={{ fontSize: '0.72rem', color: 'var(--color-soft)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {item.type || 'agent_task'}
                      </div>

                      <div>
                        <Badge bg={statusColor} style={{ fontSize: '0.68rem', fontWeight: 500, textTransform: 'capitalize' }}>
                          {formatStatus(latestStatus)}
                        </Badge>
                      </div>

                      <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)', textAlign: 'right' }}>-</div>
                      <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)', textAlign: 'right' }}>-</div>
                      <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)', textAlign: 'right' }}>
                        {formatDuration(item.start_time, item.close_time)}
                      </div>
                    </div>

                    {/* Expanded children */}
                    {isExpanded && group.children.map((child, ci) => {
                      const childStatusColor = STATUS_COLORS[child.status] || 'secondary';
                      return (
                        <div
                          key={child.workflow_id || ci}
                          className="workflow-table-row"
                          onClick={() => selectItem(child)}
                          style={{
                            display: 'grid',
                            gridTemplateColumns: '2.5rem 1fr 140px 100px 80px 80px 90px',
                            gap: '0.5rem',
                            padding: '0.5rem 0.85rem 0.5rem 2rem',
                            cursor: 'pointer',
                            borderBottom: '1px solid var(--color-border)',
                            alignItems: 'center',
                            transition: 'background 0.15s',
                            background: 'var(--surface-contrast)',
                            opacity: 0.85,
                          }}
                        >
                          <div style={{ width: '2rem', display: 'flex', justifyContent: 'center' }}>
                            <div style={{ width: '4px', height: '4px', borderRadius: '50%', background: iconColor }} />
                          </div>
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontSize: '0.75rem', color: 'var(--color-soft)' }}>
                              {child.workflow_id && <span title={child.workflow_id}>{truncateId(child.workflow_id)}</span>}
                            </div>
                            <div style={{ fontSize: '0.68rem', color: 'var(--color-muted)' }}>{timeAgo(child.start_time)}</div>
                          </div>
                          <div style={{ fontSize: '0.7rem', color: 'var(--color-muted)' }}>{child.type}</div>
                          <div>
                            <Badge bg={childStatusColor} style={{ fontSize: '0.65rem', fontWeight: 500, textTransform: 'capitalize' }}>
                              {formatStatus(child.status)}
                            </Badge>
                          </div>
                          <div style={{ fontSize: '0.7rem', color: 'var(--color-muted)', textAlign: 'right' }}>-</div>
                          <div style={{ fontSize: '0.7rem', color: 'var(--color-muted)', textAlign: 'right' }}>-</div>
                          <div style={{ fontSize: '0.7rem', color: 'var(--color-muted)', textAlign: 'right' }}>
                            {formatDuration(child.start_time, child.close_time)}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                );
              }

              // Regular (non-grouped) items
              const item = entry;
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
          <span>{t('executions.workflowsCount', { total: workflows.length, filtered: filteredWorkflows.length })}</span>
          {stats?.temporal_available === false && (
            <span style={{ color: '#fbbf24' }}>
              <FaExclamationTriangle size={10} style={{ marginRight: '0.2rem' }} />
              {t('executions.temporalOffline')}
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
  const { t } = useTranslation('workflows');
  const [searchParams, setSearchParams] = useSearchParams();
  const activeMainTab = searchParams.get('tab') || 'workflows';
  const [dynamicWorkflows, setDynamicWorkflows] = useState([]);

  const setTab = (tab) => {
    setSearchParams({ tab });
  };

  // Load dynamic workflows for RunsTab
  useEffect(() => {
    dynamicWorkflowService.list().then(setDynamicWorkflows).catch(() => {});
  }, []);

  return (
    <Layout>
      <div style={{ padding: '1.5rem' }}>
        <div style={{ marginBottom: '0.5rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <h4 style={{ color: 'var(--color-foreground)', marginBottom: '0.25rem', fontWeight: 600 }}>
              {t('title')}
            </h4>
            <p style={{ color: 'var(--color-muted)', fontSize: '0.85rem', margin: 0 }}>
              {t('subtitle')}
            </p>
          </div>
          <Button variant="primary" size="sm" href="/workflows/builder">
            + New Workflow
          </Button>
        </div>

        <div className="workflows-tabs">
          <button
            className={`workflows-tab-btn ${activeMainTab === 'workflows' ? 'active' : ''}`}
            onClick={() => setTab('workflows')}
          >
            <FaBolt size={12} style={{ marginRight: '0.4rem' }} />
            My Workflows
          </button>
          <button
            className={`workflows-tab-btn ${activeMainTab === 'templates' ? 'active' : ''}`}
            onClick={() => setTab('templates')}
          >
            <FaLayerGroup size={12} style={{ marginRight: '0.4rem' }} />
            Templates
          </button>
          <button
            className={`workflows-tab-btn ${activeMainTab === 'runs' ? 'active' : ''}`}
            onClick={() => setTab('runs')}
          >
            <FaStream size={12} style={{ marginRight: '0.4rem' }} />
            Runs
          </button>
          <button
            className={`workflows-tab-btn ${activeMainTab === 'executions' ? 'active' : ''}`}
            onClick={() => setTab('executions')}
          >
            <FaCog size={12} style={{ marginRight: '0.4rem' }} />
            {t('tabs.executions')}
          </button>
          <button
            className={`workflows-tab-btn ${activeMainTab === 'designs' ? 'active' : ''}`}
            onClick={() => setTab('designs')}
          >
            <FaDraftingCompass size={12} style={{ marginRight: '0.4rem' }} />
            {t('tabs.designs')}
          </button>
        </div>

        {activeMainTab === 'workflows' && <DynamicWorkflowsTab />}
        {activeMainTab === 'templates' && <TemplatesTab />}
        {activeMainTab === 'runs' && <RunsTab workflows={dynamicWorkflows} />}
        {activeMainTab === 'executions' && <ExecutionsTab />}
        {activeMainTab === 'designs' && <DesignsTab />}
      </div>
    </Layout>
  );
};

export default WorkflowsPage;
