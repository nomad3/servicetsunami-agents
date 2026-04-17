import { renderHook } from '@testing-library/react';
import { useCountUp } from '../useCountUp';

// jsdom does not implement IntersectionObserver (used by framer-motion useInView)
beforeAll(() => {
  global.IntersectionObserver = class IntersectionObserver {
    constructor() {}
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

test('returns [ref, display] tuple where display is a string', () => {
  const { result } = renderHook(() => useCountUp(81, 1500));
  expect(Array.isArray(result.current)).toBe(true);
  expect(typeof result.current[1]).toBe('string');
});
