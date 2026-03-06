import { useState, useEffect, useCallback, useRef } from 'react';
import { Badge, Dropdown } from 'react-bootstrap';
import {
  FaBell,
  FaEnvelope,
  FaCalendarAlt,
  FaExclamationTriangle,
  FaCheck,
  FaCheckDouble,
} from 'react-icons/fa';
import { notificationService } from '../services/notifications';

const SOURCE_ICONS = {
  gmail: FaEnvelope,
  calendar: FaCalendarAlt,
  system: FaBell,
};

const PRIORITY_COLORS = {
  high: '#ff4757',
  medium: '#ffa502',
  low: '#747d8c',
};

const NotificationBell = () => {
  const [notifications, setNotifications] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const intervalRef = useRef(null);

  const fetchCount = useCallback(async () => {
    try {
      const count = await notificationService.getUnreadCount();
      setUnreadCount(count);
    } catch {
      // Silent fail
    }
  }, []);

  const fetchNotifications = useCallback(async () => {
    setLoading(true);
    try {
      const data = await notificationService.getNotifications({ limit: 10 });
      setNotifications(data);
    } catch {
      // Silent fail
    } finally {
      setLoading(false);
    }
  }, []);

  // Poll unread count every 60s
  useEffect(() => {
    fetchCount();
    intervalRef.current = setInterval(fetchCount, 60000);
    return () => clearInterval(intervalRef.current);
  }, [fetchCount]);

  const handleToggle = (isOpen) => {
    if (isOpen) fetchNotifications();
  };

  const handleMarkRead = async (id, e) => {
    e.stopPropagation();
    await notificationService.markRead(id);
    setNotifications(prev => prev.map(n => n.id === id ? { ...n, read: true } : n));
    setUnreadCount(prev => Math.max(0, prev - 1));
  };

  const handleMarkAllRead = async (e) => {
    e.stopPropagation();
    await notificationService.markAllRead();
    setNotifications(prev => prev.map(n => ({ ...n, read: true })));
    setUnreadCount(0);
  };

  const handleDismiss = async (id, e) => {
    e.stopPropagation();
    await notificationService.dismiss(id);
    setNotifications(prev => prev.filter(n => n.id !== id));
    setUnreadCount(prev => Math.max(0, prev - 1));
  };

  const formatTime = (dateStr) => {
    const d = new Date(dateStr);
    const now = new Date();
    const diff = (now - d) / 1000 / 60;
    if (diff < 60) return `${Math.round(diff)}m ago`;
    if (diff < 1440) return `${Math.round(diff / 60)}h ago`;
    return d.toLocaleDateString();
  };

  return (
    <Dropdown align="end" onToggle={handleToggle}>
      <Dropdown.Toggle
        variant="link"
        className="notification-bell-toggle"
        style={{
          position: 'relative',
          color: 'var(--text-secondary)',
          padding: '4px 8px',
          border: 'none',
          background: 'none',
        }}
      >
        <FaBell size={18} />
        {unreadCount > 0 && (
          <Badge
            bg="danger"
            pill
            style={{
              position: 'absolute',
              top: 0,
              right: 0,
              fontSize: '0.65rem',
              minWidth: '16px',
            }}
          >
            {unreadCount > 99 ? '99+' : unreadCount}
          </Badge>
        )}
      </Dropdown.Toggle>

      <Dropdown.Menu
        style={{
          width: '380px',
          maxHeight: '480px',
          overflowY: 'auto',
          background: 'var(--bg-card)',
          border: '1px solid var(--border-color)',
          boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
        }}
      >
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '8px 16px', borderBottom: '1px solid var(--border-color)',
        }}>
          <strong style={{ color: 'var(--text-primary)' }}>Notifications</strong>
          {unreadCount > 0 && (
            <button onClick={handleMarkAllRead} style={{
              background: 'none', border: 'none', color: 'var(--bs-primary)',
              fontSize: '0.8rem', cursor: 'pointer',
            }}>
              <FaCheckDouble size={12} className="me-1" /> Mark all read
            </button>
          )}
        </div>

        {loading && notifications.length === 0 && (
          <div style={{ padding: '20px', textAlign: 'center', color: 'var(--text-muted)' }}>Loading...</div>
        )}

        {!loading && notifications.length === 0 && (
          <div style={{ padding: '20px', textAlign: 'center', color: 'var(--text-muted)' }}>No notifications yet</div>
        )}

        {notifications.map((n) => {
          const Icon = SOURCE_ICONS[n.source] || FaBell;
          return (
            <Dropdown.Item key={n.id} as="div" style={{
              padding: '10px 16px', borderBottom: '1px solid var(--border-color)',
              background: n.read ? 'transparent' : 'rgba(var(--bs-primary-rgb), 0.05)', cursor: 'default',
            }}>
              <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-start' }}>
                <Icon size={16} style={{ color: PRIORITY_COLORS[n.priority] || '#ffa502', marginTop: '3px' }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontWeight: n.read ? 400 : 600, fontSize: '0.85rem', color: 'var(--text-primary)',
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                  }}>
                    {n.priority === 'high' && <FaExclamationTriangle size={10} className="me-1" style={{ color: '#ff4757' }} />}
                    {n.title}
                  </div>
                  {n.body && (
                    <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginTop: '2px', lineHeight: 1.3 }}>
                      {n.body.length > 120 ? n.body.slice(0, 120) + '...' : n.body}
                    </div>
                  )}
                  <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '4px', display: 'flex', gap: '8px' }}>
                    <span>{formatTime(n.created_at)}</span>
                    {!n.read && (
                      <button onClick={(e) => handleMarkRead(n.id, e)} style={{
                        background: 'none', border: 'none', color: 'var(--bs-primary)', padding: 0, cursor: 'pointer', fontSize: '0.7rem',
                      }}><FaCheck size={10} /> Read</button>
                    )}
                    <button onClick={(e) => handleDismiss(n.id, e)} style={{
                      background: 'none', border: 'none', color: 'var(--text-muted)', padding: 0, cursor: 'pointer', fontSize: '0.7rem',
                    }}>Dismiss</button>
                  </div>
                </div>
              </div>
            </Dropdown.Item>
          );
        })}
      </Dropdown.Menu>
    </Dropdown>
  );
};

export default NotificationBell;
