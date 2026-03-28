import api from '../utils/api';

const listSessions = () => api.get('/chat/sessions');

const createSession = (payload) => api.post('/chat/sessions', payload);

const listMessages = (sessionId) => api.get(`/chat/sessions/${sessionId}/messages`);

const postMessage = (sessionId, content) =>
  api.post(`/chat/sessions/${sessionId}/messages`, {
    content,
  });

const STREAM_INACTIVITY_TIMEOUT_MS = 30000;

const postMessageStream = (sessionId, content, onToken, onUserSaved, onDone, onError) => {
  const user = JSON.parse(localStorage.getItem('user'));
  const token = user?.access_token || '';
  const ctrl = new AbortController();
  let inactivityTimer = null;

  const resetInactivityTimer = () => {
    if (inactivityTimer) clearTimeout(inactivityTimer);
    inactivityTimer = setTimeout(() => {
      ctrl.abort();
      onError('Stream timed out — no response received');
    }, STREAM_INACTIVITY_TIMEOUT_MS);
  };

  const clearInactivityTimer = () => {
    if (inactivityTimer) {
      clearTimeout(inactivityTimer);
      inactivityTimer = null;
    }
  };

  fetch(`/api/v1/chat/sessions/${sessionId}/messages/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ content }),
    signal: ctrl.signal,
  }).then(async (res) => {
    if (!res.ok) {
      clearInactivityTimer();
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      onError(err.detail || 'Stream failed');
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    resetInactivityTimer();

    const processLines = () => {
      const lines = buf.split('\n');
      buf = lines.pop(); // keep incomplete line
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const evt = JSON.parse(line.slice(6));
          if (evt.type === 'user_saved') onUserSaved(evt.message);
          else if (evt.type === 'token') onToken(evt.text);
          else if (evt.type === 'done') onDone(evt.message);
        } catch (e) {
          console.warn('[Luna] SSE parse error:', line, e);
        }
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        clearInactivityTimer();
        // Flush any remaining buffered data
        if (buf.trim()) {
          buf += '\n';
          processLines();
        }
        break;
      }
      resetInactivityTimer();
      buf += decoder.decode(value, { stream: true });
      processLines();
    }
  }).catch((err) => {
    clearInactivityTimer();
    if (err.name !== 'AbortError') onError(err.message || 'Stream failed');
  });

  return ctrl; // caller can abort
};

const getSessionEntities = (sessionId) => api.get(`/chat/sessions/${sessionId}/entities`);

const postMessageWithFile = (sessionId, content, file) => {
  const formData = new FormData();
  formData.append('content', content || '');
  formData.append('file', file);
  return api.post(`/chat/sessions/${sessionId}/messages/upload`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  });
};

const chatService = {
  listSessions,
  createSession,
  listMessages,
  postMessage,
  postMessageStream,
  postMessageWithFile,
  getSessionEntities,
};

export default chatService;
