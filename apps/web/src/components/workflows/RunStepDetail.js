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
    <div style={{
      width: 300, minWidth: 300, overflowY: 'auto',
      background: 'rgba(15, 23, 42, 0.8)', borderLeft: '1px solid #1e293b',
      padding: 12, color: '#e2e8f0',
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        marginBottom: 12, paddingBottom: 8, borderBottom: '1px solid #1e293b',
      }}>
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
        <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>
          <FiClock size={10} /> {step.duration_ms}ms
        </div>
      )}
      {step.tokens_used > 0 && (
        <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>
          <FiCpu size={10} /> {step.tokens_used} tokens
        </div>
      )}
      {step.cost_usd > 0 && (
        <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>
          <FiDollarSign size={10} /> ${step.cost_usd.toFixed(4)}
        </div>
      )}
      {step.retry_count > 0 && (
        <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>
          <FiRefreshCw size={10} /> Retries: {step.retry_count}
        </div>
      )}
      {step.platform && (
        <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 8 }}>
          Platform: {step.platform}
        </div>
      )}

      {step.input_data && (
        <div style={{ marginBottom: 12 }}>
          <h6 style={{ fontSize: 12, color: '#64748b' }}>Input</h6>
          <pre style={{
            fontSize: 10, color: '#94a3b8', background: '#1e293b',
            padding: 8, borderRadius: 4, maxHeight: 150, overflowY: 'auto',
            whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          }}>
            {JSON.stringify(step.input_data, null, 2)}
          </pre>
        </div>
      )}

      {step.output_data && (
        <div style={{ marginBottom: 12 }}>
          <h6 style={{ fontSize: 12, color: '#64748b' }}>Output</h6>
          <pre style={{
            fontSize: 10, color: '#94a3b8', background: '#1e293b',
            padding: 8, borderRadius: 4, maxHeight: 150, overflowY: 'auto',
            whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          }}>
            {JSON.stringify(step.output_data, null, 2)}
          </pre>
        </div>
      )}

      {step.error && (
        <div>
          <h6 style={{ fontSize: 12, color: '#ef4444' }}>Error</h6>
          <pre style={{
            fontSize: 10, color: '#fca5a5', background: '#1e293b',
            padding: 8, borderRadius: 4, maxHeight: 150, overflowY: 'auto',
            whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          }}>
            {step.error}
          </pre>
        </div>
      )}
    </div>
  );
}
