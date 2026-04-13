import React, { useRef, useMemo, useState, useEffect } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Stars, PerspectiveCamera, Text, Float } from '@react-three/drei';
import * as THREE from 'three';

// --- Keyboard Flight Controller ---
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
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
    };
  }, []);

  useFrame((state, delta) => {
    const speed = keys.current['ShiftLeft'] ? moveSpeed * 3 : moveSpeed;
    
    // Translation
    if (keys.current['KeyW']) camera.translateZ(-speed * delta);
    if (keys.current['KeyS']) camera.translateZ(speed * delta);
    if (keys.current['KeyA']) camera.translateX(-speed * delta);
    if (keys.current['KeyD']) camera.translateX(speed * delta);
    if (keys.current['Space']) camera.translateY(speed * delta);
    if (keys.current['ControlLeft']) camera.translateY(-speed * delta);

    // Rotation (Mouse-less fly mode placeholder - using Arrow keys for now)
    if (keys.current['ArrowLeft']) camera.rotation.y += rotateSpeed;
    if (keys.current['ArrowRight']) camera.rotation.y -= rotateSpeed;
    if (keys.current['ArrowUp']) camera.rotation.x += rotateSpeed;
    if (keys.current['ArrowDown']) camera.rotation.x -= rotateSpeed;
  });

  return <PerspectiveCamera makeDefault position={[0, 0, 50]} />;
}

// --- Individual Entity Star ---
function EntityStar({ position, name, type, similarity }) {
  const [hovered, setHover] = useState(false);
  
  const color = useMemo(() => {
    switch (type) {
      case 'person': return '#64b4ff';
      case 'organization': return '#ffaa00';
      case 'system': return '#00ffaa';
      default: return '#ffffff';
    }
  }, [type]);

  return (
    <Float speed={2} rotationIntensity={0.5} floatIntensity={0.5}>
      <mesh 
        position={position} 
        onPointerOver={() => setHover(true)} 
        onPointerOut={() => setHover(false)}
      >
        <sphereGeometry args={[hovered ? 1.2 : 0.8, 16, 16]} />
        <meshStandardMaterial 
          color={color} 
          emissive={color} 
          emissiveIntensity={hovered ? 2 : 0.5} 
          transparent 
          opacity={0.8}
        />
        {hovered && (
          <Text
            position={[0, 2, 0]}
            fontSize={0.5}
            color="#ffffff"
            anchorX="center"
            anchorY="middle"
          >
            {name}
          </Text>
        )}
      </mesh>
    </Float>
  );
}

// --- Main Nebula Scene ---
export default function KnowledgeNebula({ nodes = [] }) {
  // Generate random data if none provided
  const displayNodes = useMemo(() => {
    if (nodes.length > 0) return nodes;
    
    return Array.from({ length: 50 }).map((_, i) => ({
      id: i,
      position: [
        (Math.random() - 0.5) * 100,
        (Math.random() - 0.5) * 100,
        (Math.random() - 0.5) * 100
      ],
      name: `Entity ${i}`,
      type: ['person', 'organization', 'system', 'concept'][Math.floor(Math.random() * 4)],
    }));
  }, [nodes]);

  return (
    <div style={{ width: '100%', height: '100%', position: 'absolute', top: 0, left: 0 }}>
      <Canvas>
        <color attach="background" args={['#00050a']} />
        <ambientLight intensity={0.2} />
        <pointLight position={[10, 10, 10]} intensity={1} />
        
        <Stars radius={100} depth={50} count={5000} factor={4} saturation={0} fade speed={1} />
        
        <NebulaCamera />

        {displayNodes.map((node) => (
          <EntityStar 
            key={node.id} 
            position={node.position} 
            name={node.name} 
            type={node.type} 
          />
        ))}

        {/* Global fog for depth */}
        <fog attach="fog" args={['#00050a', 50, 200]} />
      </Canvas>
    </div>
  );
}
