import React, { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import { apiFetch, apiJson } from '../api';

const AuthContext = createContext(null);

// Decode a JWT without verifying — we only need the `exp` claim to schedule
// a proactive refresh. The server verifies on every API call, so reading the
// claim client-side is for timing only, not auth decisions.
function decodeJwtExp(token) {
  try {
    const payload = token.split('.')[1];
    if (!payload) return null;
    const decoded = JSON.parse(
      atob(payload.replace(/-/g, '+').replace(/_/g, '/')),
    );
    return typeof decoded.exp === 'number' ? decoded.exp * 1000 : null;
  } catch {
    return null;
  }
}

// Refresh 5 minutes before the token expires; clamp to [60s, 12h] so a
// malformed exp claim can't make us spin or sleep forever.
const REFRESH_LEAD_MS = 5 * 60 * 1000;
const MIN_REFRESH_DELAY_MS = 60 * 1000;
const MAX_REFRESH_DELAY_MS = 12 * 60 * 60 * 1000;

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const refreshTimerRef = useRef(null);

  const logout = useCallback(() => {
    if (refreshTimerRef.current) {
      clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
    localStorage.removeItem('luna_token');
    setUser(null);
  }, []);

  // Schedule a proactive refresh REFRESH_LEAD_MS before token expiry. On
  // success, store the new token and reschedule. On failure, log out.
  const scheduleRefresh = useCallback((token) => {
    if (refreshTimerRef.current) {
      clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
    const expMs = decodeJwtExp(token);
    if (!expMs) return;
    const delay = Math.min(
      MAX_REFRESH_DELAY_MS,
      Math.max(MIN_REFRESH_DELAY_MS, expMs - Date.now() - REFRESH_LEAD_MS),
    );
    refreshTimerRef.current = setTimeout(async () => {
      try {
        const res = await apiFetch('/api/v1/auth/refresh', { method: 'POST' });
        const data = await res.json();
        if (data?.access_token) {
          localStorage.setItem('luna_token', data.access_token);
          scheduleRefresh(data.access_token);
        } else {
          logout();
        }
      } catch {
        // apiFetch already dispatches luna:logout on 401; nothing more to do.
      }
    }, delay);
  }, [logout]);

  useEffect(() => {
    const token = localStorage.getItem('luna_token');
    if (!token) { setLoading(false); return; }
    apiJson('/api/v1/users/me')
      .then((me) => {
        setUser(me);
        scheduleRefresh(token);
      })
      .catch(() => logout())
      .finally(() => setLoading(false));
    return () => {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    };
  }, [logout, scheduleRefresh]);

  useEffect(() => {
    window.addEventListener('luna:logout', logout);
    return () => window.removeEventListener('luna:logout', logout);
  }, [logout]);

  const login = async (email, password) => {
    const body = new URLSearchParams({ username: email, password });
    console.log('[Luna] Login to:', import.meta.env.VITE_API_BASE_URL || '(relative)');
    const res = await apiFetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    });
    const data = await res.json();
    if (!data.access_token) {
      throw new Error(data.detail || JSON.stringify(data));
    }
    localStorage.setItem('luna_token', data.access_token);
    const me = await apiJson('/api/v1/users/me');
    setUser(me);
    scheduleRefresh(data.access_token);
    return me;
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be inside AuthProvider');
  return ctx;
}
