import React from 'react';
import { Handle, Position } from 'reactflow';
import { FiLayers } from 'react-icons/fi';
import './WorkflowNodes.css';

export default function ParallelNode({ data, selected }) {
  const step = data.step || {};

  return (
    <div className={`workflow-node parallel-node ${selected ? 'selected' : ''}`}>
      <Handle type="target" position={Position.Top} />
      <div className="node-header">
        <FiLayers size={14} style={{ color: '#06b6d4' }} />
        <span className="node-title">{step.id || 'Parallel'}</span>
      </div>
      <div className="node-body">
        <span>{(step.steps || []).length} parallel branches</span>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
