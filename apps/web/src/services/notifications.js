import api from './api';

export const notificationService = {
  async getNotifications({ unreadOnly = false, skip = 0, limit = 20 } = {}) {
    const params = new URLSearchParams();
    if (unreadOnly) params.append('unread_only', 'true');
    params.append('skip', skip);
    params.append('limit', limit);
    const response = await api.get(`/notifications?${params.toString()}`);
    return response.data;
  },

  async getUnreadCount() {
    const response = await api.get('/notifications/count');
    return response.data.unread;
  },

  async markRead(id) {
    await api.patch(`/notifications/${id}/read`);
  },

  async markAllRead() {
    await api.patch('/notifications/read-all');
  },

  async dismiss(id) {
    await api.delete(`/notifications/${id}`);
  },

  // Inbox monitor controls
  async startInboxMonitor(intervalMinutes = 15) {
    const response = await api.post(`/workflows/inbox-monitor/start?check_interval_minutes=${intervalMinutes}`);
    return response.data;
  },

  async stopInboxMonitor() {
    const response = await api.post('/workflows/inbox-monitor/stop');
    return response.data;
  },

  async getInboxMonitorStatus() {
    const response = await api.get('/workflows/inbox-monitor/status');
    return response.data;
  },
};
