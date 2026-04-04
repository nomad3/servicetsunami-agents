import React from 'react';
import { Badge, Alert, ListGroup, Spinner } from 'react-bootstrap';
import { FiX, FiCheckCircle, FiAlertCircle } from 'react-icons/fi';

export default function TestConsole({ results, onClose }) {
  if (!results) {
    return (
      <div className="test-console">
        <div className="test-console-header">
          <span style={{ fontWeight: 600 }}>Test Console</span>
          <FiX onClick={onClose} style={{ cursor: 'pointer' }} />
        </div>
        <div className="test-console-loading">
          <Spinner size="sm" /> Running validation...
        </div>
      </div>
    );
  }

  const hasErrors = results.validation_errors?.length > 0;

  return (
    <div className="test-console">
      <div className="test-console-header">
        <span style={{ fontWeight: 600 }}>Test Console</span>
        <Badge bg={hasErrors ? 'danger' : 'success'}>
          {hasErrors ? 'Errors Found' : 'Valid'}
        </Badge>
        <FiX onClick={onClose} style={{ cursor: 'pointer', marginLeft: 'auto' }} />
      </div>
      <div className="test-console-body">
        {hasErrors && (
          <Alert variant="danger" style={{ fontSize: 12, padding: 8 }}>
            {results.validation_errors.map((err, i) => (
              <div key={i}><FiAlertCircle /> {err}</div>
            ))}
          </Alert>
        )}

        <h6 className="test-console-plan-title">
          Execution Plan ({results.step_count || 0} steps)
        </h6>
        <ListGroup variant="flush">
          {(results.steps_planned || []).map((step, i) => (
            <ListGroup.Item key={i} className="test-console-step-item">
              <Badge bg="secondary" style={{ fontSize: 10 }}>{i + 1}</Badge>
              <span>{typeof step === 'string' ? step : (step.type || JSON.stringify(step))}</span>
              <FiCheckCircle style={{ color: '#22c55e', marginLeft: 'auto' }} size={12} />
            </ListGroup.Item>
          ))}
        </ListGroup>

        {results.integrations_required?.length > 0 && (
          <div className="test-console-integrations">
            <h6>Required Integrations</h6>
            {results.integrations_required.map((int, i) => (
              <Badge key={i} bg="outline-secondary" className="badge">
                {int}
              </Badge>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
