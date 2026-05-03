import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiFetch, apiJson, apiStream, API_BASE } from '../api';

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe('api.js', () => {
  it('apiFetch attaches Bearer token when present', async () => {
    localStorage.setItem('luna_token', 'tok-1');
    const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      text: () => Promise.resolve(''),
      json: () => Promise.resolve({}),
    });
    await apiFetch('/api/v1/foo');
    const headers = fetchSpy.mock.calls[0][1].headers;
    expect(headers.Authorization).toBe('Bearer tok-1');
  });

  it('apiFetch normalises a path missing its leading slash', async () => {
    const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      text: () => Promise.resolve(''),
      json: () => Promise.resolve({}),
    });
    await apiFetch('api/v1/foo');
    const url = fetchSpy.mock.calls[0][0];
    expect(url.endsWith('/api/v1/foo')).toBe(true);
    // Should not produce the //api/v1 double-slash bug.
    expect(url).not.toMatch(/\/\/api\/v1/);
  });

  it('apiFetch throws and rejects on non-ok responses', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      status: 500,
      text: () => Promise.resolve('boom'),
      json: () => Promise.resolve({}),
    });
    await expect(apiFetch('/x')).rejects.toThrow(/API 500/);
  });

  it('apiFetch fires luna:logout event on 401 with a stored token', async () => {
    localStorage.setItem('luna_token', 'tok-1');
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      status: 401,
      text: () => Promise.resolve('unauthorized'),
      json: () => Promise.resolve({}),
    });

    const handler = vi.fn();
    window.addEventListener('luna:logout', handler);
    await expect(apiFetch('/x')).rejects.toThrow();
    expect(handler).toHaveBeenCalled();
    expect(localStorage.getItem('luna_token')).toBeNull();
    window.removeEventListener('luna:logout', handler);
  });

  it('apiJson parses the response body', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      text: () => Promise.resolve(''),
      json: () => Promise.resolve({ hello: 'world' }),
    });
    const data = await apiJson('/x');
    expect(data).toEqual({ hello: 'world' });
  });

  it('apiStream POSTs the body and includes auth header when set', async () => {
    localStorage.setItem('luna_token', 'tok-2');
    const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      body: null,
    });
    await apiStream('/api/v1/stream', { msg: 'hi' });
    const [, init] = fetchSpy.mock.calls[0];
    expect(init.method).toBe('POST');
    expect(init.headers.Authorization).toBe('Bearer tok-2');
    expect(JSON.parse(init.body)).toEqual({ msg: 'hi' });
  });

  it('exports a string API_BASE', () => {
    expect(typeof API_BASE).toBe('string');
  });
});
