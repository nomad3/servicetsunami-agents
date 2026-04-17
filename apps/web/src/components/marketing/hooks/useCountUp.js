import { useEffect, useRef, useState } from 'react';
import { useInView, useReducedMotion, animate } from 'framer-motion';

export function useCountUp(target, duration = 1500) {
  const [display, setDisplay] = useState('0');
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true });
  const prefersReducedMotion = useReducedMotion();

  useEffect(() => {
    if (!isInView) return;
    if (prefersReducedMotion) {
      setDisplay(String(target));
      return;
    }
    const controls = animate(0, target, {
      duration: duration / 1000,
      ease: 'easeOut',
      onUpdate: v => setDisplay(Math.round(v).toString()),
    });
    return () => controls.stop();
  }, [isInView, target, duration, prefersReducedMotion]);

  return [ref, display];
}
