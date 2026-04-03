import React from 'react';
import { Handle, Position } from 'reactflow';
import { FiClock, FiZap, FiPlay, FiGlobe } from 'react-icons/fi';
import './WorkflowNodes.css';

const TRIGGER_ICONS = {
  cron: FiClock, interval: FiClock, webhook: FiGlobe,
  event: FiZap, manual: FiPlay, agent: FiZap,
};

const TRIGGER_LABELS = {
  cron: (t) => `Cron: ${t.schedule || 'not set'}`,
  interval: (t) => `Every ${t.interval_minutes || '?'} min`,
  webhook: () => 'Webhook trigger',
  event: (t) => `On: ${t.event_type || 'event'}`,
  manual: () => 'Manual trigger',
  agent: () => 'Agent trigger',
};

export default function TriggerNode({ data }) {
  const trigger = data.trigger || { type: 'manual' };
  const Icon = TRIGGER_ICONS[trigger.type] || FiPlay;
  const label = (TRIGGER_LABELS[trigger.type] || (() => trigger.type))(trigger);

  return (
    <div className="workflow-node trigger-node">
      <div className="node-header">
        <Icon size={14} />
        <span className="node-title">{label}</span>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
