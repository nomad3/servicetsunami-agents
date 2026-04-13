import React, { useRef, useMemo, useState, useEffect } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Stars, PerspectiveCamera, Text, Float, Line } from '@react-three/drei';
import { EffectComposer, Bloom, Noise, Vignette } from '@react-three/postprocessing';
import * as THREE from 'three';

// --- Instanced Entities (High Performance) ---
function InstancedEntities({ nodes = [] }) {
  const meshRef = useRef();
  const count = nodes.length;
  
  // Buffers for instance properties
  const colorArray = useMemo(() => new Float32Array(count * 3), [count]);
  const tempObject = useMemo(() => new THREE.Object3D(), []);
  const tempColor = useMemo(() => new THREE.Color(), []);

  useEffect(() => {
    if (!meshRef.current) return;

    nodes.forEach((node, i) => {
      // Position
      tempObject.position.set(...node.position);
      tempObject.updateMatrix();
      meshRef.current.setMatrixAt(i, tempObject.matrix);

      // Color based on type
      let colorStr = '#ffffff';
      switch(node.type) {
        case 'person': colorStr = '#64b4ff'; break;
        case 'organization': colorStr = '#ffaa00'; break;
        case 'system': colorStr = '#00ffaa'; break;
      }
      tempColor.set(colorStr);
      meshRef.current.setColorAt(i, tempColor);
    });

    meshRef.current.instanceMatrix.needsUpdate = true;
    if (meshRef.current.instanceColor) meshRef.current.instanceColor.needsUpdate = true;
  }, [nodes]);

  return (
    <instancedMesh ref={meshRef} args={[null, null, count]}>
      <sphereGeometry args={[0.8, 12, 12]} />
      <meshStandardMaterial emissiveIntensity={2} toneMapped={false} />
    </instancedMesh>
  );
}

// --- Agent Avatar (The Party) ---
function AgentAvatar({ name, role, targetPosition, color = '#ff0055' }) {
  const meshRef = useRef();

  useFrame((state, delta) => {
    if (!targetPosition) return;
    const target = new THREE.Vector3(...targetPosition);
    meshRef.current.position.lerp(target, 0.05);
  });

  return (
    <group ref={meshRef}>
      <mesh>
        <octahedronGeometry args={[1.5, 0]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={4} toneMapped={false} />
      </mesh>
      <Text position={[0, 2.5, 0]} fontSize={0.6} color="#ffffff" anchorX="center" anchorY="middle">
        {name}
      </Text>
      <pointLight distance={15} intensity={5} color={color} />
    </group>
  );
}

// --- Data Beam (Comms) ---
function DataBeam({ start, end, active }) {
  const lineRef = useRef();
  
  useFrame((state) => {
    if (active && lineRef.current) {
      const t = state.clock.getElapsedTime();
      lineRef.current.material.dashOffset = -t * 2;
    }
  });

  if (!active) return null;

  return (
    <Line
      ref={lineRef}
      points={[start, end]}
      color="#64b4ff"
      lineWidth={3}
      dashed
      dashScale={5}
      dashSize={1}
      dashOffset={0}
    />
  );
}

// --- Keyboard & Gesture Flight Controller ---
function NebulaCamera() {
  const { camera } = useThree();
  const moveSpeed = 5.0;
  const rotateSpeed = 0.02;
  const keys = useRef({});

  useEffect(() => {
    const down = (e) => (keys.current[e.code] = true);
    const up = (e) => (keys.current[e.code] = false);
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    
    // Listen for Gesture coordinates from HUD
    const handleGestureMove = (e) => {
      const { dx, dy, dz } = e.detail;
      camera.translateX(dx * 0.1);
      camera.translateY(dy * 0.1);
      camera.translateZ(dz * 0.1);
    };
    window.addEventListener('luna-gesture-move', handleGestureMove);

    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
      window.removeEventListener('luna-gesture-move', handleGestureMove);
    };
  }, [camera]);

  useFrame((state, delta) => {
    const speed = keys.current['ShiftLeft'] ? moveSpeed * 3 : moveSpeed;
    if (keys.current['KeyW']) camera.translateZ(-speed * delta);
    if (keys.current['KeyS']) camera.translateZ(speed * delta);
    if (keys.current['KeyA']) camera.translateX(-speed * delta);
    if (keys.current['KeyD']) camera.translateX(speed * delta);
    if (keys.current['Space']) camera.translateY(speed * delta);
    if (keys.current['ControlLeft']) camera.translateY(-speed * delta);

    if (keys.current['ArrowLeft']) camera.rotation.y += rotateSpeed;
    if (keys.current['ArrowRight']) camera.rotation.y -= rotateSpeed;
    if (keys.current['ArrowUp']) camera.rotation.x += rotateSpeed;
    if (keys.current['ArrowDown']) camera.rotation.x -= rotateSpeed;
  });

  return <PerspectiveCamera makeDefault position={[0, 0, 50]} />;
}

// --- Main Nebula Scene ---
export default function KnowledgeNebula({ nodes = [], agents = [], beams = [] }) {
  const displayNodes = useMemo(() => {
    if (nodes.length > 0) return nodes;
    // Default world generation
    return Array.from({ length: 150 }).map((_, i) => ({
      id: i,
      position: [(Math.random() - 0.5) * 200, (Math.random() - 0.5) * 200, (Math.random() - 0.5) * 200],
      name: `Entity ${i}`,
      type: ['person', 'organization', 'system', 'concept'][Math.floor(Math.random() * 4)],
    }));
  }, [nodes]);

  return (
    <div style={{ width: '100%', height: '100%', position: 'absolute', top: 0, left: 0 }}>
      <Canvas gl={{ antialias: false, stencil: false, depth: true }} dpr={[1, 2]}>
        <color attach="background" args={['#00050a']} />
        
        <ambientLight intensity={0.2} />
        <pointLight position={[10, 10, 10]} intensity={1} />
        
        <Stars radius={150} depth={50} count={7000} factor={4} saturation={0} fade speed={1} />
        
        <NebulaCamera />

        <InstancedEntities nodes={displayNodes} />

        {agents.map((agent) => (
          <AgentAvatar
            key={agent.id}
            name={agent.name}
            role={agent.role}
            targetPosition={agent.targetPosition}
            color={agent.color}
          />
        ))}

        {beams.map((beam, i) => (
          <DataBeam
            key={i}
            start={beam.start}
            end={beam.end}
            active={beam.active}
          />
        ))}

        <EffectComposer disableNormalPass>
          <Bloom 
            luminanceThreshold={1} 
            mipmapBlur 
            intensity={1.5} 
            radius={0.4}
          />
          <Noise opacity={0.05} />
          <Vignette eskil={false} offset={0.1} darkness={1.1} />
        </EffectComposer>

        <fog attach="fog" args={['#00050a', 50, 250]} />
      </Canvas>
    </div>
  );
}
