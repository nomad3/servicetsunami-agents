import { useState, useEffect, useCallback, useRef } from 'react';
import { apiJson } from '../api';

const POLL_INTERVAL = 30000; // 30s

export function useNotifications() {
  const [unreadCount, setUnreadCount] = useState(0);
  const [notifications, setNotifications] = useState([]);
  const intervalRef = useRef(null);

  const fetchCount = useCallback(async () => {
    try {
      const data = await apiJson('/api/v1/notifications/count');
      const newCount = data.unread || 0;

      // If count increased, show native notification for the latest
      if (newCount > unreadCount && unreadCount > 0) {
        showNativeNotification(newCount - unreadCount);
      }
      setUnreadCount(newCount);
    } catch {}
  }, [unreadCount]);

  const fetchAll = useCallback(async () => {
    try {
      const data = await apiJson('/api/v1/notifications?limit=20&unread_only=false');
      setNotifications(data);
    } catch {}
  }, []);

  const markRead = useCallback(async (id) => {
    try {
      await apiJson(`/api/v1/notifications/${id}/read`, { method: 'PATCH' });
      setNotifications(prev => prev.map(n => n.id === id ? { ...n, read: true } : n));
      setUnreadCount(prev => Math.max(0, prev - 1));
    } catch {}
  }, []);

  const dismiss = useCallback(async (id) => {
    try {
      await apiJson(`/api/v1/notifications/${id}`, { method: 'DELETE' });
      setNotifications(prev => prev.filter(n => n.id !== id));
    } catch {}
  }, []);

  useEffect(() => {
    fetchCount();
    intervalRef.current = setInterval(fetchCount, POLL_INTERVAL);
    return () => clearInterval(intervalRef.current);
  }, [fetchCount]);

  return { unreadCount, notifications, fetchAll, markRead, dismiss };
}

async function showNativeNotification(count) {
  // Try Tauri native notification first
  try {
    const { sendNotification, isPermissionGranted, requestPermission } = await import('@tauri-apps/plugin-notification');
    let permitted = await isPermissionGranted();
    if (!permitted) {
      const permission = await requestPermission();
      permitted = permission === 'granted';
    }
    if (permitted) {
      sendNotification({
        title: 'Luna',
        body: `${count} new notification${count > 1 ? 's' : ''}`,
      });
    }
  } catch {
    // Fallback to web Notification API (for PWA mode)
    if ('Notification' in window && Notification.permission === 'granted') {
      new Notification('Luna', { body: `${count} new notification${count > 1 ? 's' : ''}` });
    }
  }
}
