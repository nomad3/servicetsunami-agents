import React from 'react';

export default function ActionApproval({ action, onApprove, onDeny, onDismiss }) {
  if (!action) return null;

  return (
    <div className="action-approval-overlay">
      <div className="action-approval-card">
        <h3>Luna wants to take an action</h3>
        <div className="action-type">{action.type}</div>
        <p className="action-desc">{action.description}</p>
        {action.details && (
          <pre className="action-details">{JSON.stringify(action.details, null, 2)}</pre>
        )}
        <div className="action-buttons">
          <button className="luna-btn" onClick={() => onApprove(action)}>Allow</button>
          <button className="luna-btn luna-btn-secondary" onClick={() => onDeny(action)}>Deny</button>
          <button className="luna-btn luna-btn-sm" onClick={onDismiss}>Skip</button>
        </div>
      </div>
    </div>
  );
}
