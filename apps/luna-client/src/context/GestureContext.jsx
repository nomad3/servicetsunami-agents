/**
 * GestureContext — single subscription point for the Tauri gesture engine.
 *
 * Listens to `gesture-event`, `wake-state-changed`, and `engine-status` Tauri
 * events and exposes them via `useGesture()`. When a binding matches an event,
 * the registered `onAction(binding, event)` callback is fired.
 *
 * Wraps the authenticated app shell in App.jsx, alongside AuthProvider.
 */
import React, { createContext, useEffect, useMemo, useRef, useState } from 'react';
import { DEFAULT_BINDINGS } from '../components/gestures/defaults';

export const GestureContext = createContext(null);

function bindingMatches(binding, event) {
  if (!binding.enabled) return false;
  if (binding.gesture.pose !== event.pose) return false;
  if (binding.gesture.motion) {
    if (!event.motion) return false;
    if (binding.gesture.motion.kind !== event.motion.kind) return false;
    if (
      binding.gesture.motion.direction &&
      binding.gesture.motion.direction !== event.motion.direction
    ) {
      return false;
    }
  }
  return true;
}

export function GestureProvider({ children, bindings = DEFAULT_BINDINGS, onAction }) {
  const [wakeState, setWakeState] = useState('sleeping');
  const [lastEvent, setLastEvent] = useState(null);
  const [status, setStatus] = useState({ state: 'stopped', fps: 0, last_error: null });
  const bindingsRef = useRef(bindings);
  bindingsRef.current = bindings;

  useEffect(() => {
    let unsubGesture;
    let unsubWake;
    let unsubStatus;

    (async () => {
      try {
        const { listen } = await import('@tauri-apps/api/event');
        unsubGesture = await listen('gesture-event', (e) => {
          const event = e.payload;
          setLastEvent(event);
          const match = bindingsRef.current.find((b) => bindingMatches(b, event));
          if (match && onAction) onAction(match, event);
        });
        unsubWake = await listen('wake-state-changed', (e) => setWakeState(e.payload));
        unsubStatus = await listen('engine-status', (e) => setStatus(e.payload));
      } catch {
        // Not in Tauri (e.g., PWA build) — engine simply never fires events.
      }
    })();

    return () => {
      try { unsubGesture && unsubGesture(); } catch {}
      try { unsubWake && unsubWake(); } catch {}
      try { unsubStatus && unsubStatus(); } catch {}
    };
  }, [onAction]);

  const value = useMemo(
    () => ({ wakeState, lastEvent, status }),
    [wakeState, lastEvent, status],
  );

  return <GestureContext.Provider value={value}>{children}</GestureContext.Provider>;
}
