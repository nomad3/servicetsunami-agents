import React from 'react';
import { Badge } from 'react-bootstrap';
import { FiX, FiClock, FiDollarSign, FiCpu, FiRefreshCw } from 'react-icons/fi';

const STATUS_COLORS = {
  pending: 'secondary', running: 'primary', completed: 'success',
  failed: 'danger', waiting: 'warning',
};

export default function RunStepDetail({ step, onClose }) {
  if (!step) return null;

  return (
    <div className="run-step-detail">
      <div className="run-step-detail-header">
        <div>
          <h6 style={{ margin: 0, fontSize: 14 }}>{step.step_id}</h6>
          <Badge bg="secondary" style={{ fontSize: 10 }}>{step.step_type}</Badge>
        </div>
        <FiX style={{ cursor: 'pointer' }} onClick={onClose} />
      </div>

      <div style={{ marginBottom: 8 }}>
        <Badge bg={STATUS_COLORS[step.status] || 'secondary'}>{step.status}</Badge>
      </div>

      {step.duration_ms != null && (
        <div className="stat-line">
          <FiClock size={10} /> {step.duration_ms}ms
        </div>
      )}
      {step.tokens_used > 0 && (
        <div className="stat-line">
          <FiCpu size={10} /> {step.tokens_used} tokens
        </div>
      )}
      {step.cost_usd > 0 && (
        <div className="stat-line">
          <FiDollarSign size={10} /> ${step.cost_usd.toFixed(4)}
        </div>
      )}
      {step.retry_count > 0 && (
        <div className="stat-line">
          <FiRefreshCw size={10} /> Retries: {step.retry_count}
        </div>
      )}
      {step.platform && (
        <div className="stat-line" style={{ marginBottom: 8 }}>
          Platform: {step.platform}
        </div>
      )}

      {step.input_data && (
        <div style={{ marginBottom: 12 }}>
          <h6 className="section-label">Input</h6>
          <pre>
            {JSON.stringify(step.input_data, null, 2)}
          </pre>
        </div>
      )}

      {step.output_data && (
        <div style={{ marginBottom: 12 }}>
          <h6 className="section-label">Output</h6>
          <pre>
            {JSON.stringify(step.output_data, null, 2)}
          </pre>
        </div>
      )}

      {step.error && (
        <div>
          <h6 className="error-title">Error</h6>
          <pre className="error">
            {step.error}
          </pre>
        </div>
      )}
    </div>
  );
}
