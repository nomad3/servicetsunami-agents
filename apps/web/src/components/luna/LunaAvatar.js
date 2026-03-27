import React from 'react';
import './LunaAvatar.css';

const EMOTES = {
  idle: '~',
  listening: '((*))',
  thinking: '? ...',
  responding: '> _ <',
  happy: '\u2665 \u2605',
  focused: '</>',
  alert: '!! \u25B3 !!',
  empathetic: '\u2665',
  sleep: 'z Z z',
  private_mode: '\u25A0',
  error: '#!@%&',
  playful: '~ \u2605 ^_^ \u2605 ~',
  handoff: '\u2192',
};

const LunaAvatar = ({ state = 'idle', mood = 'calm', size = 'md', animated = true, onClick }) => {
  const sizeMap = { xs: 24, sm: 32, md: 48, lg: 80, xl: 128 };
  const px = sizeMap[size] || 48;
  const showEmote = size !== 'xs' && size !== 'sm';
  const emote = EMOTES[state] || EMOTES.idle;

  return (
    <div
      className={`luna-avatar luna-state-${state} luna-mood-${mood} ${animated ? 'luna-animated' : ''}`}
      style={{ width: px, height: px + (showEmote ? 20 : 0), cursor: onClick ? 'pointer' : 'default' }}
      onClick={onClick}
      title={`Luna: ${state}`}
    >
      {showEmote && (
        <div className="luna-emote">{emote}</div>
      )}
      <div className="luna-face-wrap" style={{ width: px, height: px }}>
        <div className={`luna-glow luna-glow-${state}`} />
        <img
          src="/assets/luna/luna-base.png"
          alt="Luna"
          className="luna-face-img"
          draggable={false}
        />
      </div>
    </div>
  );
};

export default LunaAvatar;
