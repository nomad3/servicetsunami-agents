import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

const apiJsonMock = vi.fn();

vi.mock('../../api', () => ({
  apiJson: (...args) => apiJsonMock(...args),
}));

vi.mock('@tauri-apps/plugin-notification', () => ({
  sendNotification: vi.fn(),
  isPermissionGranted: vi.fn(() => Promise.resolve(true)),
  requestPermission: vi.fn(() => Promise.resolve('granted')),
}));

import { useNotifications } from '../useNotifications';

beforeEach(() => {
  apiJsonMock.mockReset();
});

describe('useNotifications', () => {
  it('fetches the unread count on mount', async () => {
    apiJsonMock.mockResolvedValue({ unread: 4 });
    const { result } = renderHook(() => useNotifications());
    await waitFor(() => expect(result.current.unreadCount).toBe(4));
    expect(apiJsonMock).toHaveBeenCalledWith('/api/v1/notifications/count');
  });

  it('fetchAll loads notification list', async () => {
    apiJsonMock.mockResolvedValueOnce({ unread: 0 });
    const list = [{ id: 1, body: 'hi', read: false }];
    apiJsonMock.mockResolvedValueOnce(list);
    const { result } = renderHook(() => useNotifications());
    await waitFor(() => expect(result.current.unreadCount).toBe(0));
    await act(async () => {
      await result.current.fetchAll();
    });
    expect(result.current.notifications).toEqual(list);
  });

  it('markRead patches the API and decrements unread count', async () => {
    apiJsonMock.mockResolvedValueOnce({ unread: 2 });
    apiJsonMock.mockResolvedValueOnce([
      { id: 1, body: 'a', read: false },
      { id: 2, body: 'b', read: false },
    ]);
    apiJsonMock.mockResolvedValueOnce({});

    const { result } = renderHook(() => useNotifications());
    await waitFor(() => expect(result.current.unreadCount).toBe(2));
    await act(async () => {
      await result.current.fetchAll();
    });
    await act(async () => {
      await result.current.markRead(1);
    });

    expect(apiJsonMock).toHaveBeenCalledWith(
      '/api/v1/notifications/1/read',
      { method: 'PATCH' }
    );
    expect(result.current.notifications.find((n) => n.id === 1).read).toBe(true);
    expect(result.current.unreadCount).toBe(1);
  });

  it('dismiss removes the notification and only decrements unread for unread items', async () => {
    apiJsonMock.mockResolvedValueOnce({ unread: 1 });
    apiJsonMock.mockResolvedValueOnce([
      { id: 1, body: 'unread', read: false },
      { id: 2, body: 'read', read: true },
    ]);
    apiJsonMock.mockResolvedValueOnce({});
    apiJsonMock.mockResolvedValueOnce({});

    const { result } = renderHook(() => useNotifications());
    await waitFor(() => expect(result.current.unreadCount).toBe(1));
    await act(async () => {
      await result.current.fetchAll();
    });

    // Dismissing the read item should not change the unread count.
    await act(async () => {
      await result.current.dismiss(2);
    });
    expect(result.current.unreadCount).toBe(1);
    expect(result.current.notifications.find((n) => n.id === 2)).toBeUndefined();

    await act(async () => {
      await result.current.dismiss(1);
    });
    expect(result.current.unreadCount).toBe(0);
  });

  it('survives a fetch failure without throwing', async () => {
    apiJsonMock.mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useNotifications());
    // No throw, unread stays at default 0.
    await waitFor(() => expect(apiJsonMock).toHaveBeenCalled());
    expect(result.current.unreadCount).toBe(0);
  });
});
