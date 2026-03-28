import { createContext, useContext, useEffect, useRef, useState } from 'react';
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
  const intervalRef = useRef(null);
  const activeRef = useRef(false); // track active state outside setPresence

  // Poll presence — adaptive: 3s when active, 10s when idle
  useEffect(() => {
    let mounted = true;

    const reschedule = (isActive) => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      intervalRef.current = setInterval(poll, isActive ? 3000 : 10000);
    };

    const poll = async () => {
      const token = localStorage.getItem('token');
      if (!token) return;
      try {
        const res = await api.get('/presence/');
        if (mounted && res.data) {
          const newState = res.data.state || 'idle';
          const isActive = !['idle', 'sleep'].includes(newState);
          if (isActive !== activeRef.current) {
            activeRef.current = isActive;
            reschedule(isActive);
          }
          setPresence(res.data);
        }
      } catch (e) {
        // Silent — presence is optional
      }
    };
    poll();
    intervalRef.current = setInterval(poll, 10000);
    return () => { mounted = false; if (intervalRef.current) clearInterval(intervalRef.current); };
  }, []);

  return (
    <LunaPresenceContext.Provider value={{ presence, setPresence }}>
      {children}
    </LunaPresenceContext.Provider>
  );
};

export default LunaPresenceContext;
