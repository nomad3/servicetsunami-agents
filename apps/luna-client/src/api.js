const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

export async function apiFetch(path, options = {}) {
  const token = localStorage.getItem('luna_token');
  const headers = { 'Content-Type': 'application/json', ...options.headers };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, { ...options, headers });
  
  if (res.status === 401) {
    // Only logout if we actually have a token that failed
    if (token) {
      console.warn('Unauthorized request to:', url);
      localStorage.removeItem('luna_token');
      window.dispatchEvent(new Event('luna:logout'));
    }
  }
  
  if (!res.ok) {
    const errorText = await res.text();
    console.error(`API Error [${res.status}] at ${url}:`, errorText);
    throw new Error(`API ${res.status}: ${errorText}`);
  }
  return res;
}

export async function apiJson(path, options = {}) {
  const res = await apiFetch(path, options);
  return res.json();
}

export function apiStream(path, body, signal) {
  const token = localStorage.getItem('luna_token');
  return fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });
}
