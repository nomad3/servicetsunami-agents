/**
 * Animated beam of light between two agents — visible when an A2A
 * collaboration is active. Reads the wake state to dim itself when the
 * conductor is sleeping, lights up bright when armed.
 */
import React, { useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

export default function CommsBeam({ from, to, color = '#4cf', armed = true }) {
  const ref = useRef();

  useFrame((state) => {
    if (!ref.current) return;
    const t = state.clock.elapsedTime;
    // Pulse opacity for "active comms" feel
    const base = armed ? 0.55 : 0.2;
    ref.current.material.opacity = base + Math.sin(t * 4) * 0.15;
  });

  // Thin cylinder oriented from `from` to `to`.
  const start = new THREE.Vector3(...from);
  const end = new THREE.Vector3(...to);
  const mid = start.clone().add(end).multiplyScalar(0.5);
  const dir = end.clone().sub(start);
  const len = dir.length();
  // Default cylinder is along Y; rotate so it points along `dir`.
  const up = new THREE.Vector3(0, 1, 0);
  const quat = new THREE.Quaternion().setFromUnitVectors(up, dir.normalize());

  return (
    <mesh position={mid.toArray()} quaternion={quat.toArray()}>
      <cylinderGeometry args={[0.025, 0.025, len, 8, 1, true]} />
      <meshBasicMaterial color={color} transparent opacity={0.5} depthWrite={false} />
      <primitive object={ref.current || {}} attach="material" />
    </mesh>
  );
}
