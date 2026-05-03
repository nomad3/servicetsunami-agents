/**
 * @jest-environment jsdom
 */
import {
  initMarketingAnalytics,
  track,
  _resetForTest,
} from '../marketingAnalytics';


describe('marketingAnalytics', () => {
  const ORIGINAL_ENV = process.env;

  beforeEach(() => {
    process.env = { ...ORIGINAL_ENV };
    _resetForTest();
    delete window.plausible;
    document.head.querySelectorAll('script[data-domain-marker="plausible"]').forEach(
      (s) => s.remove(),
    );
  });

  afterAll(() => {
    process.env = ORIGINAL_ENV;
  });

  test('init is a no-op when REACT_APP_PLAUSIBLE_DOMAIN is unset', () => {
    delete process.env.REACT_APP_PLAUSIBLE_DOMAIN;
    initMarketingAnalytics();
    expect(document.querySelector('script[data-domain-marker="plausible"]')).toBeNull();
    expect(window.plausible).toBeUndefined();
  });

  test('init is a no-op when REACT_APP_PLAUSIBLE_DOMAIN is empty string', () => {
    process.env.REACT_APP_PLAUSIBLE_DOMAIN = '';
    initMarketingAnalytics();
    expect(document.querySelector('script[data-domain-marker="plausible"]')).toBeNull();
  });

  test('init injects the script when domain is set', () => {
    process.env.REACT_APP_PLAUSIBLE_DOMAIN = 'agentprovision.com';
    initMarketingAnalytics();
    const script = document.querySelector('script[data-domain-marker="plausible"]');
    expect(script).not.toBeNull();
    expect(script.getAttribute('data-domain')).toBe('agentprovision.com');
    expect(script.src).toContain('plausible.io/js/script.js');
    expect(script.defer).toBe(true);
  });

  test('init respects custom REACT_APP_PLAUSIBLE_HOST (self-hosted)', () => {
    process.env.REACT_APP_PLAUSIBLE_DOMAIN = 'agentprovision.com';
    process.env.REACT_APP_PLAUSIBLE_HOST = 'https://stats.agentprovision.com';
    initMarketingAnalytics();
    const script = document.querySelector('script[data-domain-marker="plausible"]');
    expect(script.src).toContain('https://stats.agentprovision.com/js/script.js');
  });

  test('init is idempotent (no double-injection on HMR / re-mount)', () => {
    process.env.REACT_APP_PLAUSIBLE_DOMAIN = 'agentprovision.com';
    initMarketingAnalytics();
    initMarketingAnalytics();
    expect(document.querySelectorAll('script[data-domain-marker="plausible"]')).toHaveLength(1);
  });

  test('track is a no-op when analytics disabled — no PII leaves the page', () => {
    delete process.env.REACT_APP_PLAUSIBLE_DOMAIN;
    // Even if a stub is somehow present, track must respect the env gate
    const stub = jest.fn();
    window.plausible = stub;
    track('cta_get_started_click', { location: 'hero' });
    expect(stub).not.toHaveBeenCalled();
  });

  test('track queues events when analytics enabled', () => {
    process.env.REACT_APP_PLAUSIBLE_DOMAIN = 'agentprovision.com';
    initMarketingAnalytics();
    // The injected stub queues into window.plausible.q
    track('cta_get_started_click', { location: 'hero' });
    expect(window.plausible.q).toBeDefined();
    expect(window.plausible.q.length).toBe(1);
    const [eventName, payload] = window.plausible.q[0];
    expect(eventName).toBe('cta_get_started_click');
    expect(payload).toEqual({ props: { location: 'hero' } });
  });

  test('track ignores empty / non-string event names defensively', () => {
    process.env.REACT_APP_PLAUSIBLE_DOMAIN = 'agentprovision.com';
    initMarketingAnalytics();
    track('');
    track(null);
    track(123);
    expect(window.plausible.q || []).toHaveLength(0);
  });

  test('track works without props', () => {
    process.env.REACT_APP_PLAUSIBLE_DOMAIN = 'agentprovision.com';
    initMarketingAnalytics();
    track('pageview');
    expect(window.plausible.q.length).toBe(1);
    const [eventName, payload] = window.plausible.q[0];
    expect(eventName).toBe('pageview');
    expect(payload).toBeUndefined();
  });
});
