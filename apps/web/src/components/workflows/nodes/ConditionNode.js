import React from 'react';
import { Handle, Position } from 'reactflow';
import { FiGitBranch } from 'react-icons/fi';
import './WorkflowNodes.css';

export default function ConditionNode({ data, selected }) {
  const step = data.step || {};
  const expression = step.if || 'condition';

  return (
    <div className={`workflow-node condition-node ${selected ? 'selected' : ''}`}>
      <Handle type="target" position={Position.Top} />
      <div className="node-header">
        <FiGitBranch size={14} style={{ color: '#f59e0b' }} />
        <span className="node-title">{step.id || 'Condition'}</span>
      </div>
      <div className="node-body">
        <span>{expression}</span>
      </div>
      <div className="condition-handles">
        <Handle type="source" position={Position.Bottom} id="then" style={{ left: '30%' }} />
        <Handle type="source" position={Position.Bottom} id="else" style={{ left: '70%' }} />
      </div>
      <div className="condition-labels">
        <span className="then-label">Then</span>
        <span className="else-label">Else</span>
      </div>
    </div>
  );
}
