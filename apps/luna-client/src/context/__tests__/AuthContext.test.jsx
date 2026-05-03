import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, act, waitFor } from '@testing-library/react';
import React from 'react';

import { AuthProvider, useAuth } from '../AuthContext';

let lastCtx;
function Probe() {
  lastCtx = useAuth();
  return null;
}

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
  lastCtx = undefined;
});

const okJson = (body) => ({
  ok: true,
  status: 200,
  text: () => Promise.resolve(JSON.stringify(body)),
  json: () => Promise.resolve(body),
});

describe('AuthContext', () => {
  it('starts unauthenticated when no token is in localStorage', async () => {
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() => expect(lastCtx.loading).toBe(false));
    expect(lastCtx.user).toBeNull();
  });

  it('loads the current user when a token is present', async () => {
    localStorage.setItem('luna_token', 'abc123');
    const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue(
      okJson({ id: 'u1', email: 'u@example.com' })
    );

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() => expect(lastCtx.loading).toBe(false));
    expect(lastCtx.user).toEqual({ id: 'u1', email: 'u@example.com' });
    expect(fetchSpy).toHaveBeenCalled();
  });

  it('logs out when the /me call fails', async () => {
    localStorage.setItem('luna_token', 'badtoken');
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      status: 401,
      text: () => Promise.resolve('unauthorized'),
      json: () => Promise.resolve({}),
    });

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() => expect(lastCtx.loading).toBe(false));
    expect(localStorage.getItem('luna_token')).toBeNull();
    expect(lastCtx.user).toBeNull();
  });

  it('login() stores the token and loads the user', async () => {
    const fetchSpy = vi.spyOn(global, 'fetch')
      .mockResolvedValueOnce(okJson({ access_token: 'tok-xyz' }))
      .mockResolvedValueOnce(okJson({ id: 'u2', email: 'a@b.com' }));

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() => expect(lastCtx.loading).toBe(false));

    await act(async () => {
      await lastCtx.login('a@b.com', 'pw');
    });

    expect(localStorage.getItem('luna_token')).toBe('tok-xyz');
    expect(lastCtx.user).toEqual({ id: 'u2', email: 'a@b.com' });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it('login() throws when access_token is missing in the response', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue(
      okJson({ detail: 'bad creds' })
    );

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() => expect(lastCtx.loading).toBe(false));

    await expect(lastCtx.login('a', 'b')).rejects.toThrow(/bad creds/);
  });

  it('logout() clears token and user', async () => {
    localStorage.setItem('luna_token', 'abc');
    vi.spyOn(global, 'fetch').mockResolvedValue(okJson({ id: 'u1' }));

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() => expect(lastCtx.user).toBeTruthy());

    act(() => {
      lastCtx.logout();
    });

    expect(localStorage.getItem('luna_token')).toBeNull();
    expect(lastCtx.user).toBeNull();
  });

  it('useAuth throws outside provider', () => {
    const Bare = () => {
      useAuth();
      return null;
    };
    // Suppress the React error logging for this expected throw.
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => render(<Bare />)).toThrow(/useAuth must be inside AuthProvider/);
    errSpy.mockRestore();
  });
});
