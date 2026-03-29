import { useEffect, useRef, useCallback } from 'react';
import { apiFetch } from '../api';

const SHELL_NAME = 'desktop';
const HEARTBEAT_INTERVAL = 10000; // 10s
const CAPABILITIES = {
  can_listen: true,
  can_notify: true,
  can_capture_screen: true,
  can_capture_audio: true,
  can_connect_ble: false,
  can_run_local_actions: true,
};

export function useShellPresence() {
  const registered = useRef(false);
  const intervalRef = useRef(null);

  const register = useCallback(async () => {
    try {
      await apiFetch('/api/v1/presence/shell/register', {
        method: 'POST',
        body: JSON.stringify({
          shell: SHELL_NAME,
          capabilities: CAPABILITIES,
        }),
      });
      registered.current = true;
    } catch (err) {
      console.warn('Shell register failed:', err.message);
    }
  }, []);

  const deregister = useCallback(async () => {
    if (!registered.current) return;
    try {
      await apiFetch('/api/v1/presence/shell/deregister', {
        method: 'POST',
        body: JSON.stringify({ shell: SHELL_NAME }),
      });
    } catch {
      // best-effort on teardown
    }
    registered.current = false;
  }, []);

  const heartbeat = useCallback(async () => {
    if (!registered.current) return;
    try {
      await apiFetch('/api/v1/presence/', {
        method: 'PUT',
        body: JSON.stringify({
          active_shell: SHELL_NAME,
        }),
      });
    } catch {
      // silent — heartbeat is best-effort
    }
  }, []);

  useEffect(() => {
    register();
    intervalRef.current = setInterval(heartbeat, HEARTBEAT_INTERVAL);

    const handleBeforeUnload = () => deregister();
    window.addEventListener('beforeunload', handleBeforeUnload);

    return () => {
      clearInterval(intervalRef.current);
      window.removeEventListener('beforeunload', handleBeforeUnload);
      deregister();
    };
  }, [register, deregister, heartbeat]);

  return { register, deregister };
}
