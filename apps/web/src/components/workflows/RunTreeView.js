import React, { useState, useEffect } from 'react';
import { Button, Badge, ProgressBar } from 'react-bootstrap';
import { FiArrowLeft, FiClock, FiDollarSign } from 'react-icons/fi';
import { useNodesState, useEdgesState } from 'reactflow';

import WorkflowCanvas from './WorkflowCanvas';
import RunStepDetail from './RunStepDetail';
import { definitionToFlow } from './WorkflowAdapter';
import dynamicWorkflowService from '../../services/dynamicWorkflowService';

const STATUS_BORDER = {
  pending: '#64748b',
  running: '#3b82f6',
  completed: '#22c55e',
  failed: '#ef4444',
  waiting: '#eab308',
};

const STATUS_GLOW = {
  running: '0 0 12px rgba(59,130,246,0.5)',
  failed: '0 0 12px rgba(239,68,68,0.3)',
};

export default function RunTreeView({ run, onBack }) {
  const [runDetail, setRunDetail] = useState(run);
  const [nodes, setNodes] = useNodesState([]);
  const [edges, setEdges] = useEdgesState([]);
  const [selectedStep, setSelectedStep] = useState(null);

  // Normalize API response: {run: {...}, steps: [...]} -> flat object with step_logs
  const normalizeRunResp = (resp) => {
    if (resp.run) return { ...resp.run, step_logs: resp.steps || [] };
    return resp;
  };

  useEffect(() => {
    async function load() {
      try {
        const resp = await dynamicWorkflowService.getRun(run.id);
        const detail = normalizeRunResp(resp);
        setRunDetail(detail);
        // Also fetch the workflow definition for tree rendering
        if (detail.workflow_id) {
          try {
            const wf = await dynamicWorkflowService.get(detail.workflow_id);
            applyRunStatus(detail, wf.definition, wf.trigger_config);
          } catch {
            applyRunStatus(detail, { steps: [] }, null);
          }
        }
      } catch (err) {
        console.error('Failed to load run:', err);
      }
    }
    load();
  }, [run.id]);

  useEffect(() => {
    if (runDetail?.status !== 'running') return;
    const interval = setInterval(async () => {
      try {
        const resp = await dynamicWorkflowService.getRun(run.id);
        const detail = normalizeRunResp(resp);
        setRunDetail(detail);
        if (detail.workflow_id) {
          try {
            const wf = await dynamicWorkflowService.get(detail.workflow_id);
            applyRunStatus(detail, wf.definition, wf.trigger_config);
          } catch {
            applyRunStatus(detail, { steps: [] }, null);
          }
        }
        if (detail.status !== 'running') clearInterval(interval);
      } catch {}
    }, 3000);
    return () => clearInterval(interval);
  }, [runDetail?.status, run.id]);

  const applyRunStatus = (detail, wfDef, triggerConfig) => {
    if (!detail) return;
    const { nodes: baseNodes, edges: baseEdges } = definitionToFlow(wfDef || { steps: [] }, triggerConfig);

    const stepLogs = detail.step_logs || [];
    const statusMap = {};
    stepLogs.forEach((log) => { statusMap[log.step_id] = log; });

    const styledNodes = baseNodes.map((n) => {
      const stepLog = statusMap[n.id];
      const status = stepLog?.status || 'pending';
      return {
        ...n,
        style: {
          border: `2px solid ${STATUS_BORDER[status] || '#64748b'}`,
          boxShadow: STATUS_GLOW[status] || 'none',
        },
        data: { ...n.data, executionStatus: stepLog },
      };
    });

    setNodes(styledNodes);
    setEdges(baseEdges);
  };

  const stepsCompleted = (runDetail?.step_logs || []).filter((s) => s.status === 'completed').length;
  const stepsTotal = (runDetail?.step_logs || []).length || Math.max(nodes.length - 1, 0);
  const progress = stepsTotal > 0 ? (stepsCompleted / stepsTotal) * 100 : 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Summary bar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, padding: '8px 16px',
        borderBottom: '1px solid #1e293b', flexWrap: 'wrap',
      }}>
        <Button variant="link" size="sm" onClick={onBack} style={{ color: '#94a3b8', padding: 0 }}>
          <FiArrowLeft /> Back
        </Button>
        <Badge bg={run.status === 'completed' ? 'success' : run.status === 'failed' ? 'danger' : 'primary'}>
          {runDetail?.status || run.status}
        </Badge>
        <span style={{ fontSize: 12, color: '#94a3b8' }}>
          Steps: {stepsCompleted}/{stepsTotal}
        </span>
        <ProgressBar now={progress} style={{ flex: 1, minWidth: 100, height: 6 }} />
        {runDetail?.duration_ms && (
          <span style={{ fontSize: 12, color: '#94a3b8' }}>
            <FiClock size={10} /> {(runDetail.duration_ms / 1000).toFixed(1)}s
          </span>
        )}
        {runDetail?.total_cost_usd > 0 && (
          <span style={{ fontSize: 12, color: '#94a3b8' }}>
            <FiDollarSign size={10} /> ${runDetail.total_cost_usd.toFixed(4)}
          </span>
        )}
      </div>

      {/* Tree + detail */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <WorkflowCanvas
          nodes={nodes} edges={edges}
          onNodesChange={() => {}} onEdgesChange={() => {}}
          setEdges={setEdges}
          onNodeClick={(_, node) => setSelectedStep(node.data.executionStatus)}
          readOnly
        />
        {selectedStep && (
          <RunStepDetail step={selectedStep} onClose={() => setSelectedStep(null)} />
        )}
      </div>
    </div>
  );
}
