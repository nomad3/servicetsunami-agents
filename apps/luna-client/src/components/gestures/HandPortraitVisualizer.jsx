/**
 * Live portrait of the user's hand with detected MediaPipe landmarks
 * overlaid in real time.
 *
 * Architecture (Option B — chosen 2026-05-06):
 *   - <video> opens the same webcam via getUserMedia({video:{facingMode:'user'}})
 *     so the WebView renders at native GPU speed (no IPC frame transport)
 *   - <canvas> stretches over the video and draws 21 landmarks + bone
 *     skeleton sourced from Rust's `hand-landmarks` Tauri event
 *   - Pose label + confidence badge in the corner
 *
 * Why this is useful: lets the user SEE whether Vision is detecting
 * their hand, what pose it thinks they're showing, and where the
 * landmarks are. Was added 2026-05-07 after multiple "gestures aren't
 * working" reports where the engine was firing but the user couldn't
 * tell if it was a hand-detection issue, a classifier issue, or a
 * wake-state issue.
 *
 * Mount it in the spatial HUD as a small floating panel (we render
 * portrait-aspect by default, ~240×320, top-right corner).
 */
import { useEffect, useRef, useState } from 'react';

// MediaPipe hand landmark connections — pairs of landmark indices that
// form the bone skeleton overlay.
const HAND_BONES = [
  // Thumb
  [0, 1], [1, 2], [2, 3], [3, 4],
  // Index
  [0, 5], [5, 6], [6, 7], [7, 8],
  // Middle
  [5, 9], [9, 10], [10, 11], [11, 12],
  // Ring
  [9, 13], [13, 14], [14, 15], [15, 16],
  // Pinky
  [13, 17], [17, 18], [18, 19], [19, 20],
  // Palm
  [0, 17],
];

const POSE_COLOR = {
  OpenPalm: '#39d98a',  // green — wake-eligible
  Three:    '#39d98a',  // green — wake-eligible
  Four:     '#39d98a',  // green — wake-eligible
  Fist:     '#e6584d',  // red — dismiss
  Point:    '#f4a83a',  // amber — cursor
  Peace:    '#7d6cf2',  // purple
  ThumbUp:  '#7d6cf2',  // purple
  Custom:   '#9ca3af',  // gray — unknown
};

export default function HandPortraitVisualizer({ width = 240, height = 320 }) {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const [streamError, setStreamError] = useState(null);
  const [latest, setLatest] = useState(null);

  // Open webcam.
  useEffect(() => {
    let cancelled = false;
    let stream = null;
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: 'user', width: 640, height: 480 },
          audio: false,
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          await videoRef.current.play().catch(() => {});
        }
      } catch (e) {
        setStreamError(e?.message || String(e));
      }
    })();
    return () => {
      cancelled = true;
      if (stream) stream.getTracks().forEach((t) => t.stop());
    };
  }, []);

  // Subscribe to `hand-landmarks` from Rust.
  useEffect(() => {
    let unsub;
    (async () => {
      try {
        const { listen } = await import('@tauri-apps/api/event');
        unsub = await listen('hand-landmarks', (e) => setLatest(e.payload));
      } catch {
        // Not in Tauri (e.g. PWA build) — visualizer just shows the
        // raw camera feed without overlays.
      }
    })();
    return () => { try { unsub && unsub(); } catch {} };
  }, []);

  // Draw landmark overlay every time `latest` updates.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!latest || !latest.hands || latest.hands.length === 0) return;

    const hand = latest.hands[0];
    const lms = hand.landmarks; // [[x, y, z], ...]
    const pose = (hand.pose || '').replace(/^Some\("(.+)"\)$/, '$1');
    const color = POSE_COLOR[pose] || '#9ca3af';

    // Vision returns coordinates in [0, 1] image-space with origin
    // top-left after y-flip (per HandLandmarker.swift). We render to
    // the canvas which spans the same logical area.
    const w = canvas.width;
    const h = canvas.height;

    // The webcam preview is mirrored (selfie view) — so we mirror the
    // x-coordinate too.
    const px = (x) => (1 - x) * w;
    const py = (y) => y * h;

    // Bone skeleton.
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    for (const [a, b] of HAND_BONES) {
      const la = lms[a];
      const lb = lms[b];
      if (!la || !lb) continue;
      ctx.moveTo(px(la[0]), py(la[1]));
      ctx.lineTo(px(lb[0]), py(lb[1]));
    }
    ctx.stroke();

    // Joint dots.
    ctx.fillStyle = color;
    for (const lm of lms) {
      ctx.beginPath();
      ctx.arc(px(lm[0]), py(lm[1]), 3, 0, Math.PI * 2);
      ctx.fill();
    }
  }, [latest]);

  // Resize canvas to its CSS size on mount.
  useEffect(() => {
    if (canvasRef.current) {
      canvasRef.current.width = width;
      canvasRef.current.height = height;
    }
  }, [width, height]);

  const hand = latest?.hands?.[0];
  const pose = (hand?.pose || '').replace(/^Some\("(.+)"\)$/, '$1') || '—';
  const confidence = hand?.confidence ?? 0;
  const wakeState = latest?.wake_state || 'Sleeping';

  return (
    <div
      style={{
        position: 'relative',
        width,
        height,
        borderRadius: 12,
        overflow: 'hidden',
        background: '#000',
        boxShadow: '0 4px 24px rgba(0,0,0,0.4)',
      }}
    >
      <video
        ref={videoRef}
        muted
        playsInline
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          // Mirror so left↔right matches user's intuition.
          transform: 'scaleX(-1)',
        }}
      />
      <canvas
        ref={canvasRef}
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          pointerEvents: 'none',
        }}
      />
      {/* Pose + wake-state badge */}
      <div
        style={{
          position: 'absolute',
          left: 8,
          top: 8,
          padding: '4px 8px',
          fontSize: 11,
          fontFamily: 'ui-monospace, SFMono-Regular, monospace',
          color: '#fff',
          background: 'rgba(0,0,0,0.55)',
          borderRadius: 6,
          backdropFilter: 'blur(6px)',
        }}
      >
        <div>
          pose: <span style={{ color: POSE_COLOR[pose] || '#fff' }}>{pose}</span>
        </div>
        <div>conf: {confidence.toFixed(2)}</div>
        <div>wake: {wakeState}</div>
      </div>
      {streamError && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#e6584d',
            fontSize: 11,
            padding: 12,
            textAlign: 'center',
          }}
        >
          camera unavailable: {streamError}
        </div>
      )}
    </div>
  );
}
