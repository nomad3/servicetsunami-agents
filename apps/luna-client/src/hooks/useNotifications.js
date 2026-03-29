import { useState, useEffect, useCallback, useRef } from 'react';
import { apiJson } from '../api';

const POLL_INTERVAL = 30000; // 30s

export function useNotifications() {
  const [unreadCount, setUnreadCount] = useState(0);
  const [notifications, setNotifications] = useState([]);
  const intervalRef = useRef(null);
  const unreadRef = useRef(-1); // -1 = not yet initialized
  const initializedRef = useRef(false);

  const fetchCount = useCallback(async () => {
    try {
      const data = await apiJson('/api/v1/notifications/count');
      const newCount = data.unread || 0;

      // Show native notification when count increases (skip first poll)
      if (initializedRef.current && newCount > unreadRef.current) {
        showNativeNotification(newCount - unreadRef.current);
      }
      initializedRef.current = true;
      unreadRef.current = newCount;
      setUnreadCount(newCount);
    } catch {}
  }, []);

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
      const next = Math.max(0, unreadRef.current - 1);
      unreadRef.current = next;
      setUnreadCount(next);
    } catch {}
  }, []);

  const dismiss = useCallback(async (id) => {
    try {
      const target = notifications.find(n => n.id === id);
      await apiJson(`/api/v1/notifications/${id}`, { method: 'DELETE' });
      setNotifications(prev => prev.filter(n => n.id !== id));
      if (target && !target.read) {
        const next = Math.max(0, unreadRef.current - 1);
        unreadRef.current = next;
        setUnreadCount(next);
      }
    } catch {}
  }, [notifications]);

  useEffect(() => {
    fetchCount();
    intervalRef.current = setInterval(fetchCount, POLL_INTERVAL);
    return () => clearInterval(intervalRef.current);
  }, [fetchCount]);

  return { unreadCount, notifications, fetchAll, markRead, dismiss };
}

async function showNativeNotification(count) {
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
    if ('Notification' in window && Notification.permission === 'granted') {
      new Notification('Luna', { body: `${count} new notification${count > 1 ? 's' : ''}` });
    }
  }
}
