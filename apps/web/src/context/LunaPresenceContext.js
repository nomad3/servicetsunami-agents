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

  // Poll presence every 3 seconds
  useEffect(() => {
    let mounted = true;
    const poll = async () => {
      try {
        const res = await api.get('/presence/');
        if (mounted && res.data) {
          setPresence(res.data);
        }
      } catch (e) {
        // Silent — presence is optional
      }
    };
    poll();
    const interval = setInterval(poll, 3000);
    return () => { mounted = false; clearInterval(interval); };
  }, []);

  return (
    <LunaPresenceContext.Provider value={{ presence, setPresence }}>
      {children}
    </LunaPresenceContext.Provider>
  );
};

export default LunaPresenceContext;
