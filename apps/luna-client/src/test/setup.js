// Vitest global setup for Luna client React tests.
// - Adds @testing-library/jest-dom matchers (toBeInTheDocument, etc.)
// - Stubs out the browser-only globals that the production code reads at
//   import time so tests don't blow up before the first assertion.

import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => {
  cleanup();
});

// Node 22+ ships a global `localStorage` shim that lacks `.clear()` and a
// proper API. Replace it with an in-memory Storage-like polyfill so test code
// can call standard methods. jsdom would normally provide one but Node's
// global shadows it depending on flags.
function makeStorage() {
  let store = {};
  return {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
    clear: () => { store = {}; },
    key: (i) => Object.keys(store)[i] || null,
    get length() { return Object.keys(store).length; },
  };
}

if (!globalThis.localStorage || typeof globalThis.localStorage.clear !== 'function') {
  Object.defineProperty(globalThis, 'localStorage', {
    value: makeStorage(),
    writable: true,
    configurable: true,
  });
}
if (!globalThis.sessionStorage || typeof globalThis.sessionStorage.clear !== 'function') {
  Object.defineProperty(globalThis, 'sessionStorage', {
    value: makeStorage(),
    writable: true,
    configurable: true,
  });
}

// `import.meta.env` reads — keep the test env stable.
if (!import.meta.env.VITE_API_BASE_URL) {
  import.meta.env.VITE_API_BASE_URL = 'http://test.local';
}

// jsdom doesn't ship matchMedia / IntersectionObserver — most components don't
// need them, but pre-stub the ones that crashed across our suites.
if (!window.matchMedia) {
  window.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  });
}

// Default fetch stub so tests that don't care about the network won't reach out.
// Individual tests can override via vi.spyOn(global, 'fetch').
if (!global.fetch || !vi.isMockFunction(global.fetch)) {
  global.fetch = vi.fn(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      text: () => Promise.resolve(''),
      json: () => Promise.resolve({}),
    })
  );
}

// Quiet down the api.js console.log on import.
const origLog = console.log;
console.log = (...args) => {
  if (typeof args[0] === 'string' && args[0].includes('[Luna OS]')) return;
  origLog(...args);
};
