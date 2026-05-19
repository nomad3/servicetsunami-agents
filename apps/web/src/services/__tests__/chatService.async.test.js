// Tests for the async chat-result client helpers — task #161.
//
// Covers:
//   * postMessageStart / getJob / cancelJob use the right axios paths
//   * subscribeJob parses `event` lines, updates lastSeq, and stops on
//     a `terminal` event.
//   * subscribeJob reconnects (with from_seq=<lastSeq>) on transient
//     fetch failure.
//   * 404 from the events endpoint stops the controller without
//     reconnect storms.

// jsdom (the CRA default test env) doesn't ship TextEncoder/TextDecoder
// — the subscriber's reader loop needs both. Polyfill from Node's
// built-in `util` module BEFORE importing the service under test so the
// reader's `new TextDecoder()` succeeds.
const { TextEncoder: _TE, TextDecoder: _TD } = require('util');
if (typeof global.TextEncoder === 'undefined') global.TextEncoder = _TE;
if (typeof global.TextDecoder === 'undefined') global.TextDecoder = _TD;

import chatService from '../chat';
import api from '../../utils/api';

jest.mock('../../utils/api');

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  localStorage.setItem('user', JSON.stringify({ access_token: 'tok-async' }));
  api.post.mockResolvedValue({ data: { job_id: 'job-1' } });
  api.get.mockResolvedValue({ data: { id: 'job-1', status: 'queued' } });
});

afterEach(() => {
  delete global.fetch;
});

describe('chatService.postMessageStart / getJob / cancelJob (axios paths)', () => {
  test('postMessageStart posts to /chat/sessions/:id/messages/start', async () => {
    await chatService.postMessageStart('sess-1', 'hello');
    expect(api.post).toHaveBeenCalledWith(
      '/chat/sessions/sess-1/messages/start',
      { content: 'hello' },
    );
  });

  test('getJob fetches /chat/jobs/:id', async () => {
    await chatService.getJob('job-1');
    expect(api.get).toHaveBeenCalledWith('/chat/jobs/job-1');
  });

  test('cancelJob posts /chat/jobs/:id/cancel', async () => {
    await chatService.cancelJob('job-1');
    expect(api.post).toHaveBeenCalledWith('/chat/jobs/job-1/cancel');
  });
});

// ─────────────────────────────────────────────────────────────────────
// subscribeJob — SSE parser + reconnect
// ─────────────────────────────────────────────────────────────────────

// jsdom (the default react-scripts test env) doesn't ship TextEncoder
// by default in CRA's jest-environment-jsdom — fall back to a tiny
// charCode encoder. The subscriber feeds bytes into a TextDecoder that
// only cares about valid UTF-8 — every char we emit in these tests is
// 7-bit ASCII so the trivial encoder is sufficient.
const _ENC = (s) => {
  if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(s);
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i) & 0xff;
  return out;
};

function _readerFromChunks(chunks) {
  // Build a ReadableStream-like reader that yields each provided chunk
  // (string) once then signals done. Matches the subset of the API the
  // subscriber consumes: `getReader()` -> `{ read }`.
  const encoded = chunks.map((c) => _ENC(c));
  let i = 0;
  return {
    body: {
      getReader: () => ({
        read: async () => {
          if (i >= encoded.length) return { done: true, value: undefined };
          const value = encoded[i++];
          return { done: false, value };
        },
      }),
    },
    ok: true,
    status: 200,
    statusText: 'OK',
  };
}

describe('chatService.subscribeJob', () => {
  test('parses event lines, tracks seq, and closes on terminal', async () => {
    const stream = _readerFromChunks([
      'data: {"type":"event","seq":1,"kind":"lifecycle","payload":{"event":"started"}}\n\n',
      'data: {"type":"event","seq":2,"kind":"chunk","payload":{"text":"hello"}}\n\n',
      'data: {"type":"terminal","status":"done","last_seq":2}\n\n',
    ]);
    const fakeFetch = jest.fn(async () => stream);
    global.fetch = fakeFetch;

    const onEvent = jest.fn();
    const onTerminal = jest.fn();
    const onError = jest.fn();

    const ctrl = chatService.subscribeJob('job-1', { onEvent, onTerminal, onError });

    // Yield the macrotask queue several times so the async fetch reader
    // can finish draining its chunks. jest+jsdom doesn't auto-flush; one
    // setTimeout(0) per await chain is the cheapest reliable yield.
    for (let i = 0; i < 8; i++) {
      // eslint-disable-next-line no-await-in-loop
      await new Promise((r) => setTimeout(r, 20));
    }

    expect(fakeFetch).toHaveBeenCalledWith(
      '/api/v1/chat/jobs/job-1/events?from_seq=0',
      expect.objectContaining({
        method: 'GET',
        headers: expect.objectContaining({
          Authorization: 'Bearer tok-async',
        }),
      }),
    );
    expect(onEvent).toHaveBeenCalledTimes(2);
    expect(onEvent.mock.calls[0][0]).toEqual(
      expect.objectContaining({ seq: 1, kind: 'lifecycle' }),
    );
    expect(onTerminal).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'terminal', status: 'done' }),
    );
    expect(onError).not.toHaveBeenCalled();
    expect(ctrl.getLastSeq()).toBe(2);
    ctrl.stop();
  });

  test('404 from events endpoint stops without reconnecting', async () => {
    const fakeFetch = jest.fn(async () => ({
      ok: false,
      status: 404,
      statusText: 'Not Found',
      json: async () => ({ detail: 'Job not found' }),
    }));
    global.fetch = fakeFetch;

    const onError = jest.fn();
    const onEvent = jest.fn();
    const ctrl = chatService.subscribeJob('missing-job', { onEvent, onError });

    await new Promise((r) => setTimeout(r, 20));
    // Wait again — if we *would* reconnect, the second fetch would fire
    // within ~500 ms (base backoff). 200 ms is enough headroom to assert
    // the controller stopped.
    await new Promise((r) => setTimeout(r, 200));

    expect(fakeFetch).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith('Job not found');
    expect(onEvent).not.toHaveBeenCalled();
    ctrl.stop();
  });

  test('reconnect on clean stream end without terminal uses last seq', async () => {
    // Two streams: first yields seq=1 then closes (no terminal), forcing
    // a reconnect; second yields seq=2 + terminal. Both built with
    // dynamic factories per fetch call so we never re-use an exhausted
    // reader between mock dispatches.
    const buildFirst = () => _readerFromChunks([
      'data: {"type":"event","seq":1,"kind":"chunk","payload":{"text":"hi"}}\n\n',
    ]);
    const buildSecond = () => _readerFromChunks([
      'data: {"type":"event","seq":2,"kind":"chunk","payload":{"text":"there"}}\n\n',
      'data: {"type":"terminal","status":"done","last_seq":2}\n\n',
    ]);

    let callIdx = 0;
    const fakeFetch = jest.fn(async () => {
      callIdx += 1;
      if (callIdx === 1) return buildFirst();
      if (callIdx === 2) return buildSecond();
      return {
        ok: false,
        status: 404,
        statusText: 'Not Found',
        json: async () => ({ detail: 'no more streams in test' }),
      };
    });
    global.fetch = fakeFetch;

    const onEvent = jest.fn();
    const onTerminal = jest.fn();
    const ctrl = chatService.subscribeJob('job-1', { onEvent, onTerminal });

    // First connect drains + closes (no terminal). After the base
    // backoff (500 ms) the second connect fires. Yield enough
    // macrotasks for both readers to drain.
    await new Promise((r) => setTimeout(r, 800));
    for (let i = 0; i < 8; i++) {
      // eslint-disable-next-line no-await-in-loop
      await new Promise((r) => setTimeout(r, 20));
    }

    // Pin the first two URLs — those carry the seq we care about.
    expect(fakeFetch.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(fakeFetch.mock.calls[0][0]).toBe('/api/v1/chat/jobs/job-1/events?from_seq=0');
    expect(fakeFetch.mock.calls[1][0]).toBe('/api/v1/chat/jobs/job-1/events?from_seq=1');
    expect(onTerminal).toHaveBeenCalled();
    expect(onEvent).toHaveBeenCalledTimes(2);
    ctrl.stop();
  });
});

describe('chatService.postMessageAsync', () => {
  test('starts a job, returns the job_id via promise, then subscribes', async () => {
    api.post.mockResolvedValueOnce({ data: { job_id: 'job-async-1' } });
    const stream = _readerFromChunks([
      'data: {"type":"terminal","status":"done","last_seq":0}\n\n',
    ]);
    global.fetch = jest.fn(async () => stream);

    const onTerminal = jest.fn();
    const ctrl = chatService.postMessageAsync('sess-x', 'go', { onTerminal });
    const jobId = await ctrl.jobIdPromise;
    // Two macrotasks: one for the fetch microtask to settle, another for
    // the reader loop to drain the single terminal chunk.
    await new Promise((r) => setTimeout(r, 50));
    await new Promise((r) => setTimeout(r, 50));

    expect(jobId).toBe('job-async-1');
    expect(onTerminal).toHaveBeenCalled();
    ctrl.stop();
  });
});
