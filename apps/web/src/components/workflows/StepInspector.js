import React from 'react';
import { Form, Badge } from 'react-bootstrap';
import { FiX } from 'react-icons/fi';

export default function StepInspector({ node, integrationStatus, onUpdate, onClose }) {
  if (!node) return null;

  const step = node.data?.step || {};
  const trigger = node.data?.trigger;

  const handleStepChange = (field, value) => {
    onUpdate(node.id, { step: { ...step, [field]: value } });
  };

  if (node.type === 'triggerNode') {
    return (
      <div className="step-inspector" style={inspectorStyle}>
        <div style={headerStyle}>
          <h6 style={{ margin: 0, fontSize: 14 }}>Trigger Configuration</h6>
          <FiX style={{ cursor: 'pointer' }} onClick={onClose} />
        </div>
        <Form.Group className="mb-3">
          <Form.Label style={labelStyle}>Type</Form.Label>
          <Form.Select size="sm" value={trigger?.type || 'manual'}
            onChange={(e) => onUpdate(node.id, { trigger: { ...trigger, type: e.target.value } })}>
            <option value="manual">Manual</option>
            <option value="cron">Scheduled (Cron)</option>
            <option value="interval">Interval</option>
            <option value="webhook">Webhook</option>
            <option value="event">Event</option>
          </Form.Select>
        </Form.Group>
        {trigger?.type === 'cron' && (
          <Form.Group className="mb-3">
            <Form.Label style={labelStyle}>Cron Expression</Form.Label>
            <Form.Control size="sm" value={trigger?.schedule || ''} placeholder="0 8 * * *"
              onChange={(e) => onUpdate(node.id, { trigger: { ...trigger, schedule: e.target.value } })} />
          </Form.Group>
        )}
        {trigger?.type === 'interval' && (
          <Form.Group className="mb-3">
            <Form.Label style={labelStyle}>Interval (minutes)</Form.Label>
            <Form.Control size="sm" type="number" value={trigger?.interval_minutes || ''}
              onChange={(e) => onUpdate(node.id, { trigger: { ...trigger, interval_minutes: parseInt(e.target.value) || 0 } })} />
          </Form.Group>
        )}
        {trigger?.type === 'event' && (
          <Form.Group className="mb-3">
            <Form.Label style={labelStyle}>Event Type</Form.Label>
            <Form.Control size="sm" value={trigger?.event_type || ''} placeholder="entity_created"
              onChange={(e) => onUpdate(node.id, { trigger: { ...trigger, event_type: e.target.value } })} />
          </Form.Group>
        )}
      </div>
    );
  }

  return (
    <div className="step-inspector" style={inspectorStyle}>
      <div style={headerStyle}>
        <h6 style={{ margin: 0, fontSize: 14 }}>Step: {step.id || 'Unnamed'}</h6>
        <FiX style={{ cursor: 'pointer' }} onClick={onClose} />
      </div>

      <Form.Group className="mb-2">
        <Form.Label style={labelStyle}>Step ID</Form.Label>
        <Form.Control size="sm" value={step.id || ''} onChange={(e) => handleStepChange('id', e.target.value)} />
      </Form.Group>

      <Form.Group className="mb-2">
        <Form.Label style={labelStyle}>Type</Form.Label>
        <Form.Select size="sm" value={step.type || 'mcp_tool'} onChange={(e) => handleStepChange('type', e.target.value)}>
          <option value="mcp_tool">MCP Tool</option>
          <option value="agent">Agent</option>
          <option value="condition">Condition</option>
          <option value="for_each">For Each</option>
          <option value="parallel">Parallel</option>
          <option value="wait">Wait</option>
          <option value="human_approval">Human Approval</option>
          <option value="transform">Transform</option>
        </Form.Select>
      </Form.Group>

      {step.type === 'mcp_tool' && (
        <>
          <Form.Group className="mb-2">
            <Form.Label style={labelStyle}>Tool</Form.Label>
            <Form.Control size="sm" value={step.tool || ''} placeholder="search_emails"
              onChange={(e) => handleStepChange('tool', e.target.value)} />
          </Form.Group>
          <Form.Group className="mb-2">
            <Form.Label style={labelStyle}>Parameters (JSON)</Form.Label>
            <Form.Control as="textarea" rows={3} size="sm"
              style={{ fontFamily: 'monospace', fontSize: 11 }}
              value={JSON.stringify(step.params || {}, null, 2)}
              onChange={(e) => { try { handleStepChange('params', JSON.parse(e.target.value)); } catch {} }} />
          </Form.Group>
        </>
      )}

      {step.type === 'agent' && (
        <>
          <Form.Group className="mb-2">
            <Form.Label style={labelStyle}>Agent</Form.Label>
            <Form.Select size="sm" value={step.agent || 'luna'} onChange={(e) => handleStepChange('agent', e.target.value)}>
              <option value="luna">Luna</option>
              <option value="code">Code Agent</option>
              <option value="data">Data Agent</option>
            </Form.Select>
          </Form.Group>
          <Form.Group className="mb-2">
            <Form.Label style={labelStyle}>Prompt</Form.Label>
            <Form.Control as="textarea" rows={3} size="sm" value={step.prompt || ''}
              placeholder="Use {{variable}} to reference outputs"
              onChange={(e) => handleStepChange('prompt', e.target.value)} />
          </Form.Group>
        </>
      )}

      {step.type === 'condition' && (
        <Form.Group className="mb-2">
          <Form.Label style={labelStyle}>Expression</Form.Label>
          <Form.Control size="sm" value={step.if || ''} placeholder="{{score.score}} >= 70"
            onChange={(e) => handleStepChange('if', e.target.value)} />
        </Form.Group>
      )}

      {step.type === 'for_each' && (
        <>
          <Form.Group className="mb-2">
            <Form.Label style={labelStyle}>Collection</Form.Label>
            <Form.Control size="sm" value={step.collection || ''} placeholder="{{contacts}}"
              onChange={(e) => handleStepChange('collection', e.target.value)} />
          </Form.Group>
          <Form.Group className="mb-2">
            <Form.Label style={labelStyle}>Item Variable</Form.Label>
            <Form.Control size="sm" value={step.as || ''} placeholder="contact"
              onChange={(e) => handleStepChange('as', e.target.value)} />
          </Form.Group>
        </>
      )}

      {step.type === 'wait' && (
        <Form.Group className="mb-2">
          <Form.Label style={labelStyle}>Duration</Form.Label>
          <Form.Control size="sm" value={step.duration || ''} placeholder="5m, 1h, 30s"
            onChange={(e) => handleStepChange('duration', e.target.value)} />
        </Form.Group>
      )}

      <Form.Group className="mb-2">
        <Form.Label style={labelStyle}>Output Variable</Form.Label>
        <Form.Control size="sm" value={step.output || ''} placeholder="result"
          onChange={(e) => handleStepChange('output', e.target.value)} />
      </Form.Group>

      {integrationStatus && (
        <div style={{ marginTop: 12 }}>
          <small style={{ color: '#64748b' }}>Requires:</small>
          <Badge bg={integrationStatus.connected ? 'success' : 'danger'} style={{ marginLeft: 6 }}>
            {integrationStatus.name} {integrationStatus.connected ? '(Connected)' : '(Not connected)'}
          </Badge>
        </div>
      )}
    </div>
  );
}

const inspectorStyle = {
  width: 280, minWidth: 280, overflowY: 'auto',
  background: 'rgba(15, 23, 42, 0.6)', borderLeft: '1px solid #1e293b',
  padding: 12, color: '#e2e8f0',
};

const headerStyle = {
  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
  marginBottom: 12, paddingBottom: 8, borderBottom: '1px solid #1e293b',
};

const labelStyle = { fontSize: 11, color: '#94a3b8', marginBottom: 2 };
