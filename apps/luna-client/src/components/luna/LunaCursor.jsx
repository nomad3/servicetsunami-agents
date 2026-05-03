/**
 * LunaCursor — decorative in-app overlay that follows the index fingertip
 * while the engine is Armed and the active pose is `point`. The actual
 * system cursor is moved by Rust (cursor.rs) on every armed frame to keep
 * tip-to-cursor latency under 16ms; this overlay is purely visual feedback.
 */
import React from 'react';
import { useGesture } from '../../hooks/useGesture';

export default function LunaCursor() {
  const { wakeState, lastEvent } = useGesture();

  if (wakeState !== 'armed' || !lastEvent || lastEvent.pose !== 'point') return null;

  // Approximate index-tip position via the gesture event's hand.
  // (We don't have raw landmark coords on the React side; the overlay
  // renders at a fixed center as a placeholder for visual confirmation
  // that point-tracking is active. A future iteration could ship the
  // tip xy as part of GestureEvent for a moving overlay.)
  const x = window.innerWidth / 2;
  const y = window.innerHeight / 2;

  return (
    <div
      style={{
        position: 'fixed',
        left: x - 8,
        top: y - 8,
        width: 16,
        height: 16,
        borderRadius: 8,
        background: 'rgba(120, 200, 255, 0.45)',
        boxShadow: '0 0 16px rgba(120, 200, 255, 0.8)',
        pointerEvents: 'none',
        zIndex: 1500,
      }}
      aria-hidden="true"
    />
  );
}
