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
    <div className="step-palette">
      <h6 className="step-palette-title">Steps</h6>
      <Accordion defaultActiveKey={['triggers', 'logic']} alwaysOpen>
        {categories.map((cat) => (
          <Accordion.Item key={cat.key} eventKey={cat.key}>
            <Accordion.Header>{cat.label}</Accordion.Header>
            <Accordion.Body>
              {cat.items.map((item, i) => {
                const Icon = item.icon;
                return (
                  <div key={i}
                    className="palette-item"
                    draggable
                    onDragStart={(e) => onDragStart(e, item)}
                  >
                    <Icon size={12} />
                    <span>{item.label}</span>
                  </div>
                );
              })}
              {cat.items.length === 0 && (
                <span className="palette-empty">Loading tools...</span>
              )}
            </Accordion.Body>
          </Accordion.Item>
        ))}
      </Accordion>
    </div>
  );
}
