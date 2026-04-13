import React, { useEffect, useRef, useState } from 'react';
import { Hands } from '@mediapipe/hands';

export default function GestureController({ onSyncChange }) {
  const videoRef = useRef(null);
  const handsRef = useRef(null);
  const requestRef = useRef(null);
  const [streamActive, setStreamActive] = useState(false);

  useEffect(() => {
    const hands = new Hands({
      locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/hands/${file}`,
    });

    hands.setOptions({
      maxNumHands: 1,
      modelComplexity: 1,
      minDetectionConfidence: 0.5,
      minTrackingConfidence: 0.5,
    });

    hands.onResults((results) => {
      if (results.multiHandLandmarks && results.multiHandLandmarks.length > 0) {
        const landmarks = results.multiHandLandmarks[0];
        // Use index finger tip (8) for 3D navigation
        const indexTip = landmarks[8];
        
        // Convert normalized landmarks to relative movement
        // We calculate delta from the center of the screen
        const dx = (indexTip.x - 0.5) * 20; // Sensitivity
        const dy = -(indexTip.y - 0.5) * 20;
        const dz = (indexTip.z) * 50; // Depth

        window.dispatchEvent(new CustomEvent('luna-gesture-move', { 
          detail: { dx, dy, dz } 
        }));
        
        if (onSyncChange) onSyncChange(true);
      }
    });

    handsRef.current = hands;

    const startCamera = async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ 
          video: { width: 640, height: 480, frameRate: 30 } 
        });
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          videoRef.current.play();
          setStreamActive(true);
        }
      } catch (err) {
        console.warn('Camera access denied for gestures:', err);
      }
    };

    startCamera();

    const processVideo = async () => {
      if (videoRef.current && videoRef.current.readyState === 4) {
        await handsRef.current.send({ image: videoRef.current });
      }
      requestRef.current = requestAnimationFrame(processVideo);
    };

    requestRef.current = requestAnimationFrame(processVideo);

    return () => {
      cancelAnimationFrame(requestRef.current);
      if (videoRef.current?.srcObject) {
        videoRef.current.srcObject.getTracks().forEach(track => track.stop());
      }
      hands.close();
    };
  }, [onSyncChange]);

  return (
    <video 
      ref={videoRef} 
      style={{ 
        position: 'absolute', 
        bottom: 20, 
        right: 20, 
        width: 160, 
        height: 120, 
        transform: 'scaleX(-1)', // Mirror
        border: '1px solid #64b4ff',
        opacity: streamActive ? 0.3 : 0, // Ghost overlay
        borderRadius: '8px',
        pointerEvents: 'none',
        zIndex: 10
      }} 
    />
  );
}
