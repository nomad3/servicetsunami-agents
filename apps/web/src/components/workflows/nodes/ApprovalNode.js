import React from 'react';
import { Handle, Position } from 'reactflow';
import { FiCheckSquare } from 'react-icons/fi';
import './WorkflowNodes.css';

const STATUS_COLORS = {
  pending: '#f59e0b',
  approved: '#22c55e',
  rejected: '#ef4444',
};

export default function ApprovalNode({ data, selected }) {
  const step = data.step || {};
  const execStatus = data.executionStatus?.status;
  const borderColor = STATUS_COLORS[execStatus] || '#f97316';

  return (
    <div className={`workflow-node approval-node ${selected ? 'selected' : ''}`}
         style={{ borderColor }}>
      <Handle type="target" position={Position.Top} />
      <div className="node-header">
        <FiCheckSquare size={14} style={{ color: '#f97316' }} />
        <span className="node-title">{step.id || 'Approval'}</span>
      </div>
      <div className="node-body">
        <span>{step.prompt || 'Waiting for approval'}</span>
        {execStatus && (
          <span className={`approval-status ${execStatus}`}>{execStatus}</span>
        )}
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
