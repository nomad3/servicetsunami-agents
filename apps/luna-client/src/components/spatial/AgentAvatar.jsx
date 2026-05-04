/**
 * Single agent avatar — a sphere with a halo whose pulse intensity reflects
 * recent activity (invocations, success rate, quality score). Color comes
 * from the agent's team. Hovered + targeted state lights up brighter.
 */
import React, { useRef, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

const TEAM_COLORS = {
  // Best-effort canonical colors for known team names; falls back to a
  // hash-derived hue for unknown teams.
  default: '#64b4ff',
  Sales: '#5ec5b0',
  Code: '#ffaa00',
  HealthPets: '#ff5577',
  Memory: '#aa66ff',
  Marketing: '#ff9933',
  Personal: '#88ddff',
};

function teamColor(teamName) {
  if (!teamName) return TEAM_COLORS.default;
  if (TEAM_COLORS[teamName]) return TEAM_COLORS[teamName];
  let h = 0;
  for (let i = 0; i < teamName.length; i++) h = (h * 31 + teamName.charCodeAt(i)) >>> 0;
  const hue = h % 360;
  return `hsl(${hue}, 65%, 60%)`;
}

function activityIntensity(activity) {
  if (!activity) return 0.2;
  const inv = activity.invocations || 0;
  // Soft saturation around 50 invocations / day
  const invFactor = Math.min(1, inv / 50);
  const qualityFactor =
    typeof activity.avg_quality_score === 'number'
      ? Math.max(0, Math.min(1, activity.avg_quality_score / 100))
      : 0.5;
  return 0.2 + 0.6 * invFactor + 0.2 * qualityFactor;
}

export default function AgentAvatar({ agent, position, teamName, targeted, onClick }) {
  const meshRef = useRef();
  const haloRef = useRef();
  const color = useMemo(() => new THREE.Color(teamColor(teamName)), [teamName]);
  const intensity = useMemo(() => activityIntensity(agent.activity), [agent.activity]);

  useFrame((state) => {
    if (!meshRef.current) return;
    const t = state.clock.elapsedTime;
    // Subtle bob
    meshRef.current.position.y = position[1] + Math.sin(t * 1.2 + position[0]) * 0.05;
    // Halo pulse: faster when activity is higher
    if (haloRef.current) {
      const pulse = 0.85 + Math.sin(t * (1.0 + intensity * 3)) * 0.15;
      haloRef.current.scale.set(pulse * 1.6, pulse * 1.6, pulse * 1.6);
      haloRef.current.material.opacity = (0.18 + intensity * 0.4) * (targeted ? 1.4 : 1);
    }
  });

  return (
    <group position={position}>
      {/* Halo */}
      <mesh ref={haloRef}>
        <sphereGeometry args={[0.5, 24, 24]} />
        <meshBasicMaterial color={color} transparent opacity={0.25} depthWrite={false} />
      </mesh>
      {/* Body */}
      <mesh
        ref={meshRef}
        onPointerOver={(e) => { e.stopPropagation(); onClick && onClick({ hover: true, agent }); }}
        onPointerOut={(e) => { e.stopPropagation(); onClick && onClick({ hover: false, agent }); }}
        onClick={(e) => { e.stopPropagation(); onClick && onClick({ click: true, agent }); }}
      >
        <sphereGeometry args={[0.32, 24, 24]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={targeted ? 1.2 : 0.4 + intensity * 0.6}
          metalness={0.3}
          roughness={0.4}
        />
      </mesh>
    </group>
  );
}

export { teamColor, activityIntensity };
