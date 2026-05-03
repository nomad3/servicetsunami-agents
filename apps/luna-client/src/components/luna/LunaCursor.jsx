/**
 * LunaCursor — in-app overlay that follows the index fingertip while the
 * engine is Armed and the active pose is `point`. Reads `tip_xy` from the
 * GestureEvent (Phase 4 — engine now ships normalized fingertip coords).
 *
 * The actual system cursor is moved by Rust (`cursor.rs`) on every armed
 * frame; this overlay is decorative feedback so the user can see where
 * Luna thinks their fingertip is, even when the system cursor is gated by
 * Accessibility / frontmost-app rules and not actually moving.
 */
import React from 'react';
import { useGesture } from '../../hooks/useGesture';

export default function LunaCursor() {
  const { wakeState, lastEvent } = useGesture();

  if (wakeState !== 'armed' || !lastEvent || lastEvent.pose !== 'point') return null;

  // tip_xy is [normalized_x, normalized_y] in [0, 1] image-space; map to the
  // current viewport. Apple Vision returns mirrored x relative to the user's
  // hand; the WebView is presented as a mirror to match user expectations.
  const tip = lastEvent.tip_xy;
  if (!Array.isArray(tip) || tip.length !== 2) return null;
  const x = (1 - tip[0]) * window.innerWidth;  // mirror x
  const y = tip[1] * window.innerHeight;

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
        transition: 'left 33ms linear, top 33ms linear',
      }}
      aria-hidden="true"
    />
  );
}
