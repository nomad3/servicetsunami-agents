import React from 'react';
import { Handle, Position } from 'reactflow';
import { FiTool, FiCpu, FiCode, FiClock, FiGlobe, FiServer } from 'react-icons/fi';
import './WorkflowNodes.css';

const TYPE_CONFIG = {
  mcp_tool: { icon: FiTool, color: '#0d9488', label: (s) => s.tool || 'Tool' },
  agent: { icon: FiCpu, color: '#7c3aed', label: (s) => `${s.agent || 'Luna'}: ${(s.prompt || '').slice(0, 30)}` },
  transform: { icon: FiCode, color: '#9ca3af', label: (s) => s.operation || 'Transform' },
  wait: { icon: FiClock, color: '#6b7280', label: (s) => `Wait ${s.duration || ''}` },
  webhook_trigger: { icon: FiGlobe, color: '#6b7280', label: () => 'Webhook' },
  continue_as_new: { icon: FiServer, color: '#6b7280', label: () => 'Restart workflow' },
  cli_execute: { icon: FiCode, color: '#7c3aed', label: () => 'Code CLI' },
  internal_api: { icon: FiServer, color: '#0d9488', label: (s) => `API: ${s.path || ''}` },
};

export default function StepNode({ data, selected }) {
  const step = data.step || {};
  const config = TYPE_CONFIG[step.type] || TYPE_CONFIG.mcp_tool;
  const Icon = config.icon;

  return (
    <div className={`workflow-node step-node ${selected ? 'selected' : ''}`}
         style={{ borderColor: config.color }}>
      <Handle type="target" position={Position.Top} />
      <div className="node-header">
        <Icon size={14} style={{ color: config.color }} />
        <span className="node-title">{step.id || 'Step'}</span>
      </div>
      <div className="node-body">
        <span className="node-label">{config.label(step)}</span>
        {step.output && <span className="node-output-chip">{`{{${step.output}}}`}</span>}
      </div>
      {data.integrationStatus && (
        <span className={`integration-badge ${data.integrationStatus.connected ? 'connected' : 'disconnected'}`}>
          {data.integrationStatus.name}
        </span>
      )}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
