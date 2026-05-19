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
        if (line.startsWith(':') || !line.startsWith('data: ')) continue; // skip comments/heartbeats
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

// ──────────────────────────────────────────────────────────────────────
// Async chat-result pattern — task #161
// Design: docs/plans/2026-05-17-async-chat-result-pattern-design.md
//
// Two-step turn:
//   1. postMessageStart(sessionId, content) → { job_id }       (<200 ms)
//   2. subscribeJob(jobId, onEvent, onTerminal, onError, opts) → controller
//
// The subscriber opens GET /jobs/{job_id}/events?from_seq=<last>, parses
// each `data:` event, and on a clean close (no terminal event) reopens
// with the highest seq it has rendered. Terminal events close the
// controller. Polling fallback uses GET /jobs/{job_id} for clients
// that can't stream (corporate proxies, watch-face, etc.).
// ──────────────────────────────────────────────────────────────────────

const ASYNC_JOB_RECONNECT_BASE_MS = 500;
const ASYNC_JOB_RECONNECT_CAP_MS = 8000;

const postMessageStart = (sessionId, content) =>
  api.post(`/chat/sessions/${sessionId}/messages/start`, { content });

const getJob = (jobId) => api.get(`/chat/jobs/${jobId}`);

const cancelJob = (jobId) => api.post(`/chat/jobs/${jobId}/cancel`);

const subscribeJob = (jobId, { onEvent, onTerminal, onError } = {}) => {
  const user = JSON.parse(localStorage.getItem('user') || 'null');
  const token = user?.access_token || '';

  let ctrl = new AbortController();
  let lastSeq = 0;
  let reconnectMs = ASYNC_JOB_RECONNECT_BASE_MS;
  let closed = false;
  let reconnectTimer = null;

  const cleanup = () => {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  const stop = () => {
    closed = true;
    cleanup();
    try {
      ctrl.abort();
    } catch (_) {
      // already aborted
    }
  };

  const openOnce = async () => {
    if (closed) return;
    ctrl = new AbortController();
    let reachedTerminal = false;
    try {
      const res = await fetch(
        `/api/v1/chat/jobs/${jobId}/events?from_seq=${lastSeq}`,
        {
          method: 'GET',
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: 'text/event-stream',
          },
          signal: ctrl.signal,
        },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        if (res.status === 404) {
          // Job not found / not ours — terminal, stop reconnecting.
          closed = true;
          if (onError) onError(err.detail || 'Job not found');
          return;
        }
        // Non-2xx but transient — fall through to reconnect.
        throw new Error(err.detail || `events fetch failed (${res.status})`);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      // Successful connect — reset backoff.
      reconnectMs = ASYNC_JOB_RECONNECT_BASE_MS;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (line.startsWith(':') || !line.startsWith('data: ')) continue;
          try {
            const evt = JSON.parse(line.slice(6));
            if (evt.type === 'event') {
              if (typeof evt.seq === 'number') lastSeq = Math.max(lastSeq, evt.seq);
              if (onEvent) onEvent(evt);
            } else if (evt.type === 'terminal') {
              reachedTerminal = true;
              closed = true;
              if (onTerminal) onTerminal(evt);
            }
          } catch (e) {
            console.warn('[chat-job] SSE parse error:', line, e);
          }
        }
      }
    } catch (err) {
      if (err.name === 'AbortError' || closed) return;
      // Treat as transient — reconnect with backoff.
    }

    if (!closed && !reachedTerminal) {
      const backoff = reconnectMs;
      reconnectMs = Math.min(reconnectMs * 2, ASYNC_JOB_RECONNECT_CAP_MS);
      reconnectTimer = setTimeout(openOnce, backoff);
    }
  };

  // Fire-and-forget the first connect.
  openOnce();

  return { stop, getLastSeq: () => lastSeq };
};

const postMessageAsync = (sessionId, content, handlers = {}) => {
  // Composed helper: POST start, then subscribe. Returns a controller
  // that combines the abort + a `jobIdPromise` for callers that need
  // the id (e.g. to cancel mid-turn).
  let subCtrl = null;
  let resolveJobId;
  const jobIdPromise = new Promise((r) => { resolveJobId = r; });

  postMessageStart(sessionId, content)
    .then((res) => {
      const jobId = res?.data?.job_id;
      if (!jobId) {
        if (handlers.onError) handlers.onError('Server did not return a job id');
        return;
      }
      resolveJobId(jobId);
      subCtrl = subscribeJob(jobId, handlers);
    })
    .catch((err) => {
      if (handlers.onError) handlers.onError(err?.message || 'Failed to start chat job');
    });

  return {
    jobIdPromise,
    stop: () => { if (subCtrl) subCtrl.stop(); },
  };
};

const chatService = {
  listSessions,
  createSession,
  listMessages,
  postMessage,
  postMessageStream,
  postMessageStart,
  postMessageAsync,
  subscribeJob,
  getJob,
  cancelJob,
  postMessageWithFile,
  getSessionEntities,
};

export default chatService;
