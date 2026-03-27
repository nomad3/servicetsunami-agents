import React from 'react';

const STATE_COLORS = {
  idle: '#8b9bb0',
  listening: '#6bb5ff',
  thinking: '#ffb347',
  responding: '#5ec5b0',
  happy: '#ffd764',
  focused: '#6b8cff',
  alert: '#ff8c32',
  empathetic: '#ffb4c8',
  sleep: '#4a6fa5',
  private_mode: '#666',
  error: '#ff5050',
  playful: '#b482ff',
  handoff: '#8b9bb0',
};

const LunaStateBadge = ({ state = 'idle', size = 'sm' }) => {
  const color = STATE_COLORS[state] || STATE_COLORS.idle;
  const fontSize = size === 'xs' ? 9 : size === 'sm' ? 11 : 13;

  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 4,
      fontSize,
      color: 'var(--color-text-secondary, #8b9bb0)',
      opacity: 0.8,
    }}>
      <span style={{
        width: 6,
        height: 6,
        borderRadius: '50%',
        backgroundColor: color,
        display: 'inline-block',
        boxShadow: `0 0 6px ${color}40`,
      }} />
      {state.replace('_', ' ')}
    </span>
  );
};

export default LunaStateBadge;
