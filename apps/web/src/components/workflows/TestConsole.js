import React from 'react';
import { Badge, Alert, ListGroup, Spinner } from 'react-bootstrap';
import { FiX, FiCheckCircle, FiAlertCircle } from 'react-icons/fi';

export default function TestConsole({ results, onClose }) {
  if (!results) {
    return (
      <div style={consoleStyle}>
        <div style={headerStyle}>
          <span style={{ fontWeight: 600 }}>Test Console</span>
          <FiX onClick={onClose} style={{ cursor: 'pointer' }} />
        </div>
        <div style={{ padding: 12, color: '#64748b' }}>
          <Spinner size="sm" /> Running validation...
        </div>
      </div>
    );
  }

  const hasErrors = results.validation_errors?.length > 0;

  return (
    <div style={consoleStyle}>
      <div style={headerStyle}>
        <span style={{ fontWeight: 600 }}>Test Console</span>
        <Badge bg={hasErrors ? 'danger' : 'success'}>
          {hasErrors ? 'Errors Found' : 'Valid'}
        </Badge>
        <FiX onClick={onClose} style={{ cursor: 'pointer', marginLeft: 'auto' }} />
      </div>
      <div style={{ padding: '8px 12px', overflowY: 'auto', maxHeight: 200 }}>
        {hasErrors && (
          <Alert variant="danger" style={{ fontSize: 12, padding: 8 }}>
            {results.validation_errors.map((err, i) => (
              <div key={i}><FiAlertCircle /> {err}</div>
            ))}
          </Alert>
        )}

        <h6 style={{ fontSize: 12, color: '#94a3b8' }}>
          Execution Plan ({results.step_count || 0} steps)
        </h6>
        <ListGroup variant="flush">
          {(results.steps_planned || []).map((step, i) => (
            <ListGroup.Item key={i} style={{
              background: 'transparent', border: 'none', padding: '4px 0',
              color: '#cbd5e1', fontSize: 12, display: 'flex', alignItems: 'center', gap: 6,
            }}>
              <Badge bg="secondary" style={{ fontSize: 10 }}>{i + 1}</Badge>
              <span>{typeof step === 'string' ? step : (step.type || JSON.stringify(step))}</span>
              <FiCheckCircle style={{ color: '#22c55e', marginLeft: 'auto' }} size={12} />
            </ListGroup.Item>
          ))}
        </ListGroup>

        {results.integrations_required?.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <h6 style={{ fontSize: 12, color: '#94a3b8' }}>Required Integrations</h6>
            {results.integrations_required.map((int, i) => (
              <Badge key={i} bg="outline-secondary"
                style={{ marginRight: 4, border: '1px solid #334155', color: '#94a3b8', fontSize: 10 }}>
                {int}
              </Badge>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const consoleStyle = {
  borderTop: '1px solid #1e293b',
  background: 'rgba(15, 23, 42, 0.8)',
  color: '#e2e8f0',
};

const headerStyle = {
  display: 'flex', alignItems: 'center', gap: 8,
  padding: '8px 12px', borderBottom: '1px solid #1e293b',
  fontSize: 13,
};
