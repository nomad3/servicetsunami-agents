import { useEffect, useRef, useCallback, useState } from 'react';
import { apiFetch } from '../api';

const SHELL_TYPE = 'desktop';
const HEARTBEAT_INTERVAL = 10000; // 10s
const CAPABILITIES = {
  can_listen: true,
  can_notify: true,
  can_capture_screen: true,
  can_capture_audio: true,
  can_connect_ble: false,
  can_run_local_actions: true,
};

// Unique per browser/app instance, persists across page reloads
function getShellId() {
  let id = sessionStorage.getItem('luna_shell_id');
  if (!id) {
    id = `${SHELL_TYPE}-${Date.now().toString(36)}`;
    sessionStorage.setItem('luna_shell_id', id);
  }
  return id;
}

export function useShellPresence() {
  const registered = useRef(false);
  const intervalRef = useRef(null);
  const shellId = useRef(getShellId());
  const [handoff, setHandoff] = useState(false);

  const register = useCallback(async () => {
    try {
      const res = await apiFetch('/api/v1/presence/shell/register', {
        method: 'POST',
        body: JSON.stringify({
          shell: shellId.current,
          capabilities: CAPABILITIES,
        }),
      });
      registered.current = true;
      const snap = await res.json();
      if (snap.state === 'handoff') {
        setHandoff(true);
        setTimeout(() => setHandoff(false), 5000);
      }
    } catch (err) {
      console.warn('Shell register failed:', err.message);
    }
  }, []);

  const deregister = useCallback(async () => {
    if (!registered.current) return;
    try {
      await apiFetch('/api/v1/presence/shell/deregister', {
        method: 'POST',
        body: JSON.stringify({ shell: shellId.current }),
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
          active_shell: shellId.current,
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

  return { register, deregister, handoff };
}
