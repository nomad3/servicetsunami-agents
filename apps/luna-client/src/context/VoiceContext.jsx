import React, { createContext, useContext } from 'react';
import { useVoice } from '../hooks/useVoice';

const VoiceContext = createContext(null);

export function VoiceProvider({ children }) {
  const voice = useVoice();
  return <VoiceContext.Provider value={voice}>{children}</VoiceContext.Provider>;
}

export function useVoiceContext() {
  const ctx = useContext(VoiceContext);
  if (!ctx) {
    throw new Error('useVoiceContext must be used within VoiceProvider');
  }
  return ctx;
}
