import React, { useState } from 'react';
import { useNotifications } from '../hooks/useNotifications';

export default function NotificationBell() {
  const { unreadCount, notifications, fetchAll, markRead, dismiss } = useNotifications();
  const [open, setOpen] = useState(false);

  const toggle = () => {
    if (!open) fetchAll();
    setOpen(!open);
  };

  const sourceIcon = (source) => {
    switch (source) {
      case 'gmail': return '\u2709';
      case 'calendar': return '\uD83D\uDCC5';
      case 'whatsapp': return '\uD83D\uDCAC';
      default: return '\uD83D\uDD14';
    }
  };

  const timeAgo = (dateStr) => {
    const diff = Date.now() - new Date(dateStr).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  };

  return (
    <div className="notif-wrapper">
      <button className="notif-bell" onClick={toggle}>
        {'\uD83D\uDD14'}
        {unreadCount > 0 && <span className="notif-badge">{unreadCount > 99 ? '99+' : unreadCount}</span>}
      </button>
      {open && (
        <div className="notif-dropdown">
          <div className="notif-header">
            <span>Notifications</span>
            <button className="notif-close" onClick={() => setOpen(false)}>x</button>
          </div>
          <div className="notif-list">
            {notifications.length === 0 && <p className="notif-empty">No notifications</p>}
            {notifications.map(n => (
              <div key={n.id} className={`notif-item ${n.read ? '' : 'unread'}`}>
                <span className="notif-icon">{sourceIcon(n.source)}</span>
                <div className="notif-content">
                  <strong>{n.title}</strong>
                  {n.body && <p>{n.body.slice(0, 100)}</p>}
                  <span className="notif-time">{timeAgo(n.created_at)}</span>
                </div>
                <div className="notif-actions">
                  {!n.read && <button onClick={() => markRead(n.id)} title="Mark read">{'\u2713'}</button>}
                  <button onClick={() => dismiss(n.id)} title="Dismiss">{'\u00D7'}</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
