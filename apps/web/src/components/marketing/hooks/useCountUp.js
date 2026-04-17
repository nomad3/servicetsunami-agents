import { useEffect, useRef, useState } from 'react';
import { useInView, useReducedMotion, animate } from 'framer-motion';

export function useCountUp(target, duration = 1500, { decimals } = {}) {
  const precision = decimals ?? (Number.isInteger(target) ? 0 : 1);
  const [display, setDisplay] = useState(target.toFixed(precision));
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true });
  const prefersReducedMotion = useReducedMotion();

  useEffect(() => {
    if (!isInView) return;
    if (prefersReducedMotion) {
      setDisplay(target.toFixed(precision));
      return;
    }
    const factor = Math.pow(10, precision);
    const controls = animate(0, target, {
      duration: duration / 1000,
      ease: 'easeOut',
      onUpdate: v => setDisplay((Math.round(v * factor) / factor).toFixed(precision)),
    });
    return () => controls.stop();
  }, [isInView, target, duration, prefersReducedMotion, precision]);

  return [ref, display];
}
