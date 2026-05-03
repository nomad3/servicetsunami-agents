import React from 'react';

function gestureLabel(g) {
  const motion = g.motion ? ` + ${g.motion.kind}${g.motion.direction ? ' ' + g.motion.direction : ''}` : '';
  return `${g.pose}${motion}`;
}

export default function GestureBindingRow({ binding, conflict, onEdit, onToggle, onDelete }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '10px 12px',
        borderBottom: '1px solid #1f2a3a',
        opacity: binding.enabled ? 1 : 0.55,
        background: 'rgba(10, 18, 36, 0.4)',
      }}
    >
      <div style={{ flex: 1 }}>
        <div style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 13 }}>
          <b>{gestureLabel(binding.gesture)}</b>
          {binding.user_recorded && (
            <span style={{ marginLeft: 8, color: '#7af', fontSize: 10 }}>USER</span>
          )}
        </div>
        <div style={{ fontSize: 12, color: '#9ad' }}>
          {binding.action.kind} ({binding.scope})
        </div>
        {conflict && (
          <div style={{ color: '#fa6', fontSize: 11, marginTop: 4 }}>
            ⚠ shadows another active binding with the same gesture and scope
          </div>
        )}
      </div>
      <button onClick={() => onToggle(binding)} style={btnStyle}>
        {binding.enabled ? 'Disable' : 'Enable'}
      </button>
      <button onClick={() => onEdit(binding)} style={btnStyle}>Edit</button>
      <button
        onClick={() => {
          if (window.confirm(`Delete binding "${gestureLabel(binding.gesture)} → ${binding.action.kind}"?`)) {
            onDelete(binding.id);
          }
        }}
        style={{ ...btnStyle, color: '#f88' }}
      >
        Delete
      </button>
    </div>
  );
}

const btnStyle = {
  background: 'transparent',
  border: '1px solid #345',
  color: '#cce',
  borderRadius: 4,
  padding: '4px 10px',
  fontSize: 12,
  cursor: 'pointer',
};
