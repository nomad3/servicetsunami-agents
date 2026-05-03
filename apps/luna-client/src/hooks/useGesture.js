import { useContext } from 'react';
import { GestureContext } from '../context/GestureContext';

export function useGesture() {
  const ctx = useContext(GestureContext);
  if (!ctx) {
    return {
      wakeState: 'sleeping',
      lastEvent: null,
      status: { state: 'stopped', fps: 0, last_error: null },
    };
  }
  return ctx;
}
