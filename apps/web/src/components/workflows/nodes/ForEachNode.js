import React from 'react';
import { Handle, Position } from 'reactflow';
import { FiRepeat } from 'react-icons/fi';
import './WorkflowNodes.css';

export default function ForEachNode({ data, selected }) {
  const step = data.step || {};

  return (
    <div className={`workflow-node foreach-node ${selected ? 'selected' : ''}`}>
      <Handle type="target" position={Position.Top} />
      <div className="node-header">
        <FiRepeat size={14} style={{ color: '#22c55e' }} />
        <span className="node-title">{step.id || 'Loop'}</span>
        <span className="loop-badge">LOOP</span>
      </div>
      <div className="node-body">
        <span>for each <strong>{step.as || 'item'}</strong> in {step.collection || '...'}</span>
        <span className="substep-count">{(step.steps || []).length} sub-steps</span>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
