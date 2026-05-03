/**
 * First-launch calibration wizard. Walks the user through:
 *   1. Camera permission (OS prompt happens automatically when the engine
 *      attempts to open the device — wizard just sets context).
 *   2. Camera selection (gesture_list_cameras Tauri command).
 *   3. Accessibility (only required if the user wants cursor/click bindings).
 *      Skipping is fine — cursor bindings then show a "permission required"
 *      badge in the bindings page.
 *   4. Pose tutorial (open palm, fist, point, peace, five) — just textual
 *      prompts in v1; baseline-recording is a follow-up.
 *   5. Wake-gesture practice — visual feedback via wakeState.
 *   6. 5-card walkthrough of default bindings.
 *
 * Persists `gesture_calibrated=1` in localStorage when complete.
 */
import React, { useEffect, useState } from 'react';
import { useGesture } from '../../hooks/useGesture';

const STEPS = [
  {
    key: 'camera',
    title: 'Camera permission',
    body: 'Luna uses your camera to recognize hand gestures. macOS will prompt you to allow access.',
  },
  {
    key: 'select',
    title: 'Choose a camera',
    body: 'Pick the camera Luna should watch. Default is the built-in FaceTime HD camera.',
  },
  {
    key: 'accessibility',
    title: 'Accessibility (optional)',
    body: 'If you want gestures to move the system cursor or click outside Luna, grant Accessibility access in System Settings → Privacy & Security → Accessibility. Otherwise, skip — gestures will still work everywhere except cursor/click bindings.',
  },
  {
    key: 'pose',
    title: 'Pose tutorial',
    body: 'Show Luna these poses one at a time: open palm, fist, point, peace, five fingers spread. Watch the wake-state indicator change as Luna recognizes each one.',
  },
  {
    key: 'wake',
    title: 'Wake-gesture practice',
    body: 'Hold an open palm in front of the camera for half a second. The wake state below should switch from sleeping → arming → armed.',
  },
  {
    key: 'tour',
    title: 'Default bindings',
    body: '3-finger swipe up = open Spatial HUD. 3-finger swipe left/right = previous/next agent. 4-finger pinch in = command palette. Fist = dismiss. You can record your own in Settings → Gestures.',
  },
];

export default function GestureCalibration({ onDone }) {
  const [step, setStep] = useState(0);
  const [cameras, setCameras] = useState([]);
  const [cameraIndex, setCameraIndex] = useState(0);
  const [accessibilityOk, setAccessibilityOk] = useState(null);
  const { wakeState, status } = useGesture();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const tauri = await import('@tauri-apps/api/core').catch(() => null);
        if (!tauri || cancelled) return;
        const cams = await tauri.invoke('gesture_list_cameras');
        if (cancelled) return;
        if (Array.isArray(cams)) setCameras(cams);
      } catch {}
    })();
    return () => { cancelled = true; };
  }, []);

  const cur = STEPS[step];

  const handleSelectCamera = async (i) => {
    setCameraIndex(i);
    try {
      const tauri = await import('@tauri-apps/api/core').catch(() => null);
      if (tauri) await tauri.invoke('gesture_set_camera_index', { index: i });
    } catch {}
  };

  const handleCheckAccessibility = async () => {
    try {
      const tauri = await import('@tauri-apps/api/core').catch(() => null);
      if (!tauri) return;
      const ok = await tauri.invoke('gesture_check_accessibility');
      setAccessibilityOk(ok);
    } catch {
      setAccessibilityOk(false);
    }
  };

  const finish = () => {
    try { localStorage.setItem('gesture_calibrated', '1'); } catch {}
    onDone?.();
  };

  return (
    <div style={overlayStyle} role="dialog" aria-modal="true">
      <div style={dialogStyle}>
        <div style={{ fontSize: 11, color: '#69a', marginBottom: 4 }}>
          Step {step + 1} of {STEPS.length}
        </div>
        <h2 style={{ marginTop: 0 }}>{cur.title}</h2>
        <p style={{ color: '#9ad' }}>{cur.body}</p>

        {cur.key === 'select' && (
          <div style={{ margin: '12px 0' }}>
            {cameras.length === 0 && <div style={{ color: '#9ad', fontSize: 12 }}>(no cameras enumerated yet)</div>}
            {cameras.map((name, i) => (
              <label key={i} style={{ display: 'block', padding: 4 }}>
                <input
                  type="radio"
                  name="camera"
                  checked={cameraIndex === i}
                  onChange={() => handleSelectCamera(i)}
                  style={{ marginRight: 8 }}
                />
                {name}
              </label>
            ))}
          </div>
        )}

        {cur.key === 'accessibility' && (
          <div style={{ margin: '12px 0' }}>
            <button onClick={handleCheckAccessibility} style={btnStyle}>Check Accessibility now</button>
            {accessibilityOk === true && <span style={{ marginLeft: 12, color: '#7d7' }}>✓ granted</span>}
            {accessibilityOk === false && <span style={{ marginLeft: 12, color: '#fa6' }}>not granted (cursor bindings disabled)</span>}
          </div>
        )}

        {(cur.key === 'pose' || cur.key === 'wake') && (
          <div style={{ margin: '12px 0', padding: 8, background: '#001020', borderRadius: 4, fontSize: 12, fontFamily: 'ui-monospace, Menlo, monospace' }}>
            engine: {status.state} · wake: <b>{wakeState}</b>
          </div>
        )}

        <div style={{ marginTop: 24, display: 'flex', justifyContent: 'space-between' }}>
          <div>
            {step > 0 && <button onClick={() => setStep(step - 1)} style={btnStyle}>Back</button>}
            <button onClick={finish} style={{ ...btnStyle, marginLeft: 8 }}>Skip all</button>
          </div>
          {step < STEPS.length - 1
            ? <button onClick={() => setStep(step + 1)} style={btnStyle}>Next</button>
            : <button onClick={finish} style={{ ...btnStyle, color: '#7af' }}>Done</button>}
        </div>
      </div>
    </div>
  );
}

const overlayStyle = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,10,0.92)',
  display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 3000,
  color: '#cce',
};
const dialogStyle = {
  background: '#0a1024', padding: 28, borderRadius: 12,
  minWidth: 440, maxWidth: 560,
  border: '1px solid #345',
};
const btnStyle = {
  background: 'transparent', border: '1px solid #345', color: '#cce',
  borderRadius: 4, padding: '6px 14px', cursor: 'pointer',
};
