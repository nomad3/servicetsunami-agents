const getApiBase = () => {
  let base = import.meta.env.VITE_API_BASE_URL || '';
  // Clean trailing slashes
  base = base.replace(/\/$/, '');
  
  // In Tauri (native), we MUST have an absolute URL.
  // If it's empty or relative, default to the production domain.
  if (!base || !base.startsWith('http')) {
    // Check if we are in a browser or native
    if (window.__TAURI_INTERNALS__) {
      return 'https://agentprovision.com';
    }
  }
  return base;
};

const API_BASE = getApiBase();
console.log('[Luna OS] Initialization - API Base:', API_BASE);

export async function apiFetch(path, options = {}) {
  const token = localStorage.getItem('luna_token');
  const headers = { 'Content-Type': 'application/json', ...options.headers };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  
  // Normalize path to prevent double slashes or double /api
  let cleanPath = path.startsWith('/') ? path : `/${path}`;
  const url = `${API_BASE}${cleanPath}`;
  
  try {
    const res = await fetch(url, { ...options, headers });
    
    if (res.status === 401) {
      if (token) {
        console.warn('[Luna OS] Unauthorized (401) at:', url);
        localStorage.removeItem('luna_token');
        window.dispatchEvent(new Event('luna:logout'));
      }
    }
    
    if (!res.ok) {
      const errorText = await res.text();
      console.error(`[Luna OS] API Error [${res.status}] at ${url}:`, errorText);
      throw new Error(`API ${res.status}: ${errorText}`);
    }
    return res;
  } catch (err) {
    // Detailed error for "Load failed" (usually CORS or DNS)
    console.error(`[Luna OS] Connection Failed at ${url}. Check your internet or CORS settings.`, err);
    throw new Error(`Connection failed: ${err.message}`);
  }
}

export async function apiJson(path, options = {}) {
  const res = await apiFetch(path, options);
  return res.json();
}

export function apiStream(path, body, signal) {
  const token = localStorage.getItem('luna_token');
  let cleanPath = path.startsWith('/') ? path : `/${path}`;
  const url = `${API_BASE}${cleanPath}`;
  
  return fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });
}
