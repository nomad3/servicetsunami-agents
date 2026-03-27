import { createContext, useContext, useEffect, useState } from 'react';
import api from '../services/api';

const LunaPresenceContext = createContext(null);

export const useLunaPresence = () => useContext(LunaPresenceContext);

export const LunaPresenceProvider = ({ children }) => {
  const [presence, setPresence] = useState({
    state: 'idle',
    mood: 'calm',
    privacy: 'open',
    active_shell: null,
    connected_shells: [],
    tool_status: 'idle',
    attention_target: null,
  });

  // Poll presence — adaptive: 3s when active, 10s when idle
  useEffect(() => {
    let mounted = true;
    let intervalId = null;

    const poll = async () => {
      // Skip if not authenticated
      const token = localStorage.getItem('token');
      if (!token) return;
      try {
        const res = await api.get('/presence/');
        if (mounted && res.data) {
          setPresence(prev => {
            // Adaptive interval: poll faster during active states
            const newState = res.data.state || 'idle';
            const isActive = !['idle', 'sleep'].includes(newState);
            const wasActive = !['idle', 'sleep'].includes(prev.state);
            if (isActive !== wasActive && intervalId) {
              clearInterval(intervalId);
              intervalId = setInterval(poll, isActive ? 3000 : 10000);
            }
            return res.data;
          });
        }
      } catch (e) {
        // Silent — presence is optional
      }
    };
    poll();
    intervalId = setInterval(poll, 10000); // Start slow, speed up if active
    return () => { mounted = false; if (intervalId) clearInterval(intervalId); };
  }, []);

  return (
    <LunaPresenceContext.Provider value={{ presence, setPresence }}>
      {children}
    </LunaPresenceContext.Provider>
  );
};

export default LunaPresenceContext;
