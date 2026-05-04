/**
 * Animated beam of light between two agents — visible when an A2A
 * collaboration is active. Pulses opacity for an "active comms" feel; dims
 * to ambient when the conductor is sleeping.
 */
import React, { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

export default function CommsBeam({ from, to, color = '#4cf', armed = true }) {
  const matRef = useRef();

  // Cylinder maths — orient default-Y cylinder along (to - from).
  const { mid, quat, len } = useMemo(() => {
    const start = new THREE.Vector3(...from);
    const end = new THREE.Vector3(...to);
    const dir = end.clone().sub(start);
    const length = dir.length();
    const center = start.clone().add(end).multiplyScalar(0.5);
    const up = new THREE.Vector3(0, 1, 0);
    const q = new THREE.Quaternion().setFromUnitVectors(
      up,
      length > 1e-6 ? dir.divideScalar(length) : up.clone(),
    );
    return { mid: center.toArray(), quat: q.toArray(), len: length };
  }, [from, to]);

  useFrame((state) => {
    if (!matRef.current) return;
    const t = state.clock.elapsedTime;
    const base = armed ? 0.55 : 0.2;
    matRef.current.opacity = base + Math.sin(t * 4) * 0.15;
  });

  return (
    <mesh position={mid} quaternion={quat}>
      <cylinderGeometry args={[0.025, 0.025, len, 8, 1, true]} />
      <meshBasicMaterial
        ref={matRef}
        color={color}
        transparent
        opacity={0.5}
        depthWrite={false}
      />
    </mesh>
  );
}
