import React from 'react';
import { useGesture } from '../../hooks/useGesture';

/**
 * Small heads-up display in the bottom-right corner showing wake state and
 * the most recent classified gesture. Hides itself while the engine is
 * sleeping so it doesn't clutter the UI when no one is gesturing.
 *
 * Replaces the deleted spatial/GestureController.jsx — this version is a
 * pure consumer of the gesture engine via useGesture(), no MediaPipe in
 * the WebView.
 */
export default function GestureOverlay() {
  const { wakeState, lastEvent, status } = useGesture();

  if (status.state === 'stopped' || status.state === 'paused') return null;
  if (wakeState === 'sleeping') return null;

  const stateColor = wakeState === 'armed' ? '#4cf' : '#fa6';

  return (
    <div
      style={{
        position: 'fixed',
        bottom: 20,
        right: 20,
        width: 180,
        padding: 10,
        background: 'rgba(10, 18, 36, 0.78)',
        backdropFilter: 'blur(8px)',
        WebkitBackdropFilter: 'blur(8px)',
        color: '#cce',
        border: `1px solid ${stateColor}`,
        borderRadius: 8,
        fontFamily: 'ui-monospace, Menlo, monospace',
        fontSize: 11,
        pointerEvents: 'none',
        zIndex: 1000,
        userSelect: 'none',
      }}
      aria-hidden="true"
    >
      <div style={{ color: stateColor, fontWeight: 600 }}>
        {wakeState.toUpperCase()}
      </div>
      {lastEvent && (
        <>
          <div>pose: {lastEvent.pose}</div>
          {lastEvent.motion && lastEvent.motion.kind !== 'none' && (
            <div>
              {lastEvent.motion.kind} {lastEvent.motion.direction || ''}
            </div>
          )}
          {typeof lastEvent.confidence === 'number' && (
            <div style={{ color: '#7af', opacity: 0.7 }}>
              conf: {lastEvent.confidence.toFixed(2)}
            </div>
          )}
        </>
      )}
    </div>
  );
}
