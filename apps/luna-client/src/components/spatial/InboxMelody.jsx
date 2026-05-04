/**
 * Inbox melody — flowing strip of recent notifications + open commitments
 * along the top of the spatial scene. Items glow when unread; high-priority
 * items pulse slightly.
 *
 * Rendered as a 2D HTML overlay positioned with CSS rather than as Three.js
 * geometry — text legibility matters more than spatial fidelity for this
 * surface, and the conductor reads it without moving the camera.
 */
import React from 'react';

const PRIORITY_TINT = {
  high: '#ff6688',
  medium: '#7af',
  low: '#9ad',
};

export default function InboxMelody({ notifications = [], commitments = [] }) {
  const items = [
    ...notifications.map((n) => ({
      key: `n-${n.id}`,
      kind: 'notification',
      title: n.title,
      subtitle: n.source,
      priority: n.priority,
      time: n.created_at,
      read: n.read,
    })),
    ...commitments.map((c) => ({
      key: `c-${c.id}`,
      kind: 'commitment',
      title: c.title,
      subtitle: c.owner_agent_slug || 'commitment',
      priority: c.priority || 'medium',
      time: c.due_at,
      read: false,
    })),
  ];

  if (items.length === 0) return null;

  return (
    <div
      style={{
        position: 'absolute',
        top: 16,
        left: 16,
        right: 16,
        display: 'flex',
        gap: 12,
        overflowX: 'auto',
        padding: 6,
        pointerEvents: 'auto',
        zIndex: 5,
      }}
      aria-label="inbox-melody"
    >
      {items.map((item) => (
        <div
          key={item.key}
          style={{
            minWidth: 180,
            maxWidth: 260,
            padding: '6px 10px',
            borderRadius: 8,
            background: item.read
              ? 'rgba(15,20,40,0.55)'
              : 'rgba(15,20,40,0.78)',
            border: `1px solid ${PRIORITY_TINT[item.priority] || PRIORITY_TINT.medium}`,
            color: '#cce',
            fontFamily: 'ui-monospace, Menlo, monospace',
            fontSize: 11,
            backdropFilter: 'blur(6px)',
            WebkitBackdropFilter: 'blur(6px)',
            boxShadow: item.read ? 'none' : `0 0 12px ${PRIORITY_TINT[item.priority] || PRIORITY_TINT.medium}55`,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
          title={item.title}
        >
          <div style={{ fontWeight: 600, color: PRIORITY_TINT[item.priority] || PRIORITY_TINT.medium }}>
            {item.kind === 'notification' ? '✉' : '◆'} {item.title}
          </div>
          <div style={{ opacity: 0.7, fontSize: 10 }}>{item.subtitle}</div>
        </div>
      ))}
    </div>
  );
}
