import React from 'react';
import { Accordion } from 'react-bootstrap';
import {
  FiClock, FiTool, FiCpu, FiGitBranch, FiRepeat,
  FiPause, FiCheckSquare, FiLayers, FiGlobe, FiZap, FiPlay,
} from 'react-icons/fi';

const PALETTE_CATEGORIES = [
  {
    key: 'triggers',
    label: 'Triggers',
    items: [
      { type: 'trigger', subtype: 'cron', label: 'Scheduled (Cron)', icon: FiClock },
      { type: 'trigger', subtype: 'webhook', label: 'Webhook', icon: FiGlobe },
      { type: 'trigger', subtype: 'event', label: 'Event', icon: FiZap },
      { type: 'trigger', subtype: 'manual', label: 'Manual', icon: FiPlay },
    ],
  },
  {
    key: 'tools',
    label: 'MCP Tools',
    items: [],
  },
  {
    key: 'agents',
    label: 'Agents',
    items: [
      { type: 'agent', subtype: 'luna', label: 'Luna', icon: FiCpu },
      { type: 'agent', subtype: 'code', label: 'Code Agent', icon: FiCpu },
      { type: 'agent', subtype: 'data', label: 'Data Agent', icon: FiCpu },
    ],
  },
  {
    key: 'logic',
    label: 'Logic',
    items: [
      { type: 'condition', label: 'Condition (If/Else)', icon: FiGitBranch },
      { type: 'for_each', label: 'For Each Loop', icon: FiRepeat },
      { type: 'parallel', label: 'Parallel', icon: FiLayers },
    ],
  },
  {
    key: 'flow',
    label: 'Flow Control',
    items: [
      { type: 'wait', label: 'Wait / Delay', icon: FiPause },
      { type: 'human_approval', label: 'Human Approval', icon: FiCheckSquare },
    ],
  },
];

export default function StepPalette({ mcpTools = [] }) {
  const categories = PALETTE_CATEGORIES.map((cat) => {
    if (cat.key === 'tools' && mcpTools.length > 0) {
      return {
        ...cat,
        items: mcpTools.map((tool) => ({
          type: 'mcp_tool',
          subtype: tool.name || tool,
          label: (tool.name || tool).replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase()),
          icon: FiTool,
        })),
      };
    }
    return cat;
  });

  const onDragStart = (event, item) => {
    event.dataTransfer.setData('application/workflow-step', JSON.stringify(item));
    event.dataTransfer.effectAllowed = 'move';
  };

  return (
    <div className="step-palette" style={{
      width: 220, minWidth: 220, overflowY: 'auto',
      background: 'rgba(15, 23, 42, 0.6)', borderRight: '1px solid #1e293b',
      padding: '8px',
    }}>
      <h6 style={{ color: '#94a3b8', fontSize: 11, textTransform: 'uppercase', marginBottom: 8 }}>
        Steps
      </h6>
      <Accordion defaultActiveKey={['triggers', 'logic']} alwaysOpen>
        {categories.map((cat) => (
          <Accordion.Item key={cat.key} eventKey={cat.key}
            style={{ background: 'transparent', border: 'none' }}>
            <Accordion.Header style={{ fontSize: 12 }}>{cat.label}</Accordion.Header>
            <Accordion.Body style={{ padding: '4px 0' }}>
              {cat.items.map((item, i) => {
                const Icon = item.icon;
                return (
                  <div key={i}
                    className="palette-item"
                    draggable
                    onDragStart={(e) => onDragStart(e, item)}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 6,
                      padding: '4px 8px', marginBottom: 2, borderRadius: 4,
                      cursor: 'grab', fontSize: 12, color: '#cbd5e1',
                      background: 'rgba(30, 41, 59, 0.5)',
                    }}
                  >
                    <Icon size={12} />
                    <span>{item.label}</span>
                  </div>
                );
              })}
              {cat.items.length === 0 && (
                <span style={{ fontSize: 11, color: '#64748b' }}>Loading tools...</span>
              )}
            </Accordion.Body>
          </Accordion.Item>
        ))}
      </Accordion>
    </div>
  );
}
