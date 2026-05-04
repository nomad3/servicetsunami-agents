/**
 * PodiumScene — the wrapping Canvas + camera + post-processing for the Luna
 * OS conductor's podium. Composes:
 *   - Knowledge Nebula (existing) in the dim background
 *   - Podium (sections, agents, beams)
 *   - InboxMelody as an HTML overlay
 *   - Wake-state controlled scene fade (sleeping = dim/distant; armed = lit/close)
 *
 * The actual gesture engine + cursor + accessibility live in the existing
 * GestureProvider — this scene just consumes wake state and dispatches
 * point-and-voice events.
 */
import React, { useEffect, useState } from 'react';
import { Canvas } from '@react-three/fiber';
import { PerspectiveCamera, Stars } from '@react-three/drei';
import { EffectComposer, Bloom, Vignette } from '@react-three/postprocessing';

import { useFleetSnapshot } from '../../hooks/useFleetSnapshot';
import { useFleetStream } from '../../hooks/useFleetStream';
import { useDispatchOnPoint } from '../../hooks/useDispatchOnPoint';
import { useGesture } from '../../hooks/useGesture';
import Podium from './Podium';
import InboxMelody from './InboxMelody';

const EMPTY_SNAPSHOT = {
  agents: [],
  groups: [],
  active_collaborations: [],
  notifications: [],
  commitments: [],
  loaded: false,
  error: null,
};

export default function PodiumScene() {
  const [snapshot, setSnapshot] = useState(EMPTY_SNAPSHOT);
  useFleetSnapshot(setSnapshot);
  useFleetStream(setSnapshot);
  useDispatchOnPoint();

  const { wakeState } = useGesture();
  const armed = wakeState === 'armed';

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: '#040816',
        overflow: 'hidden',
        // Whole-scene fade tied to wake state — sleeping = dimmer / further;
        // armed = bright. Smooth via CSS, no per-frame Three.js work.
        opacity: armed ? 1 : 0.62,
        transition: 'opacity 600ms ease-out',
      }}
    >
      <Canvas dpr={[1, 2]} gl={{ antialias: true, alpha: false }}>
        <PerspectiveCamera makeDefault position={[0, 1.6, 0]} fov={70} near={0.1} far={200} />

        {/* Background — a quiet starfield to give a sense of place. Phase
            B+ replaces this with the embedded Knowledge Nebula scene
            (requires extracting it from its self-owning Canvas). */}
        <Stars radius={120} depth={60} count={3500} factor={3} saturation={0.4} fade speed={0.4} />
        <fog attach="fog" args={['#040816', 12, 32]} />

        <Podium snapshot={snapshot} armed={armed} />

        <EffectComposer>
          <Bloom luminanceThreshold={0.25} luminanceSmoothing={0.6} intensity={0.9} mipmapBlur />
          <Vignette eskil={false} offset={0.18} darkness={0.85} />
        </EffectComposer>
      </Canvas>

      <InboxMelody
        notifications={snapshot.notifications || []}
        commitments={snapshot.commitments || []}
      />

      {/* Wake-state badge — small, bottom-left, always visible */}
      <div
        style={{
          position: 'absolute',
          bottom: 16,
          left: 16,
          padding: '4px 10px',
          borderRadius: 4,
          background: armed ? 'rgba(76,255,255,0.18)' : 'rgba(120,120,140,0.18)',
          border: `1px solid ${armed ? '#4cf' : '#445'}`,
          color: armed ? '#4cf' : '#9ad',
          fontFamily: 'ui-monospace, Menlo, monospace',
          fontSize: 11,
          pointerEvents: 'none',
          zIndex: 10,
        }}
      >
        {wakeState.toUpperCase()}
      </div>

      {/* Loading skeleton for first-paint */}
      {!snapshot.loaded && (
        <div
          style={{
            position: 'absolute',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
            color: '#9ad',
            fontFamily: 'ui-monospace, Menlo, monospace',
            fontSize: 14,
          }}
        >
          Tuning the orchestra…
        </div>
      )}
    </div>
  );
}
