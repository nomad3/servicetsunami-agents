import axios from 'axios';

const API_URL = '/api/v1/auth/';

// Decode a JWT without verifying — only needed to schedule the proactive
// refresh. The server verifies on every API call.
function decodeJwtExp(token) {
  try {
    const payload = token.split('.')[1];
    if (!payload) return null;
    const decoded = JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/')));
    return typeof decoded.exp === 'number' ? decoded.exp * 1000 : null;
  } catch {
    return null;
  }
}

const REFRESH_LEAD_MS = 5 * 60 * 1000;
const MIN_REFRESH_DELAY_MS = 60 * 1000;
const MAX_REFRESH_DELAY_MS = 12 * 60 * 60 * 1000;

let refreshTimerId = null;

function scheduleRefresh(token) {
  if (refreshTimerId) {
    clearTimeout(refreshTimerId);
    refreshTimerId = null;
  }
  const expMs = decodeJwtExp(token);
  if (!expMs) return;
  const delay = Math.min(
    MAX_REFRESH_DELAY_MS,
    Math.max(MIN_REFRESH_DELAY_MS, expMs - Date.now() - REFRESH_LEAD_MS),
  );
  refreshTimerId = setTimeout(async () => {
    try {
      const res = await axios.post(
        API_URL + 'refresh',
        {},
        { headers: { Authorization: `Bearer ${token}` } },
      );
      if (res.data?.access_token) {
        const stored = JSON.parse(localStorage.getItem('user') || '{}');
        stored.access_token = res.data.access_token;
        stored.token_type = res.data.token_type;
        localStorage.setItem('user', JSON.stringify(stored));
        scheduleRefresh(res.data.access_token);
      }
    } catch {
      // Let the next API call surface a 401; the app's existing 401 handler
      // will log the user out via authService.logout().
    }
  }, delay);
}

const login = async (email, password) => {
  const response = await axios.post(API_URL + 'login', new URLSearchParams({
    username: email,
    password,
  }), {
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded'
    }
  });
  if (response.data.access_token) {
    localStorage.setItem('user', JSON.stringify(response.data));
    scheduleRefresh(response.data.access_token);
  }
  return response.data;
};

const register = async (email, password, fullName, tenantName) => {
  const response = await axios.post(API_URL + 'register', {
    user_in: {
      email,
      password,
      full_name: fullName,
    },
    tenant_in: {
      name: tenantName,
    },
  });
  return response.data;
};

const logout = () => {
  if (refreshTimerId) {
    clearTimeout(refreshTimerId);
    refreshTimerId = null;
  }
  localStorage.removeItem('user');
};

const getCurrentUser = () => {
  const stored = JSON.parse(localStorage.getItem('user') || 'null');
  // Re-arm refresh if we hydrated from localStorage and a timer isn't pending.
  if (stored?.access_token && refreshTimerId === null) {
    scheduleRefresh(stored.access_token);
  }
  return stored;
};

const requestPasswordReset = async (email) => {
  const response = await axios.post(API_URL + 'password-reset', { email });
  return response.data;
};

const resetPassword = async (email, token, newPassword) => {
  const response = await axios.post(API_URL + 'password-reset/confirm', {
    email,
    token,
    new_password: newPassword,
  });
  return response.data;
};

const authService = {
  login,
  register,
  logout,
  getCurrentUser,
  requestPasswordReset,
  resetPassword,
};

export default authService;