import api from '../utils/api';

// Polling parameters for the async transcription fallback. The api may
// return `{ status: "pending", job_id }` when the code-worker workflow
// (apps/code-worker/transcription.py) takes longer than the server-side
// sync window. We poll `GET /media/transcription/{job_id}` until the
// status flips or we exceed `MAX_POLL_MS`. Backoff is exponential up to
// POLL_INTERVAL_CAP_MS so a slow whisper run doesn't flood the api with
// hundreds of polls; a 30s clip typically resolves on the 2nd-4th poll.
const POLL_INTERVAL_BASE_MS = 1000;
const POLL_INTERVAL_CAP_MS = 5000;
const MAX_POLL_MS = 90_000; // matches the original axios timeout

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const pollTranscription = async (jobId) => {
  const start = Date.now();
  let attempt = 0;
  while (Date.now() - start < MAX_POLL_MS) {
    const res = await api.get(`/media/transcription/${jobId}`);
    if (res.data && res.data.status === 'completed') {
      return res;
    }
    const delay = Math.min(POLL_INTERVAL_CAP_MS, POLL_INTERVAL_BASE_MS * 2 ** attempt);
    attempt += 1;
    await sleep(delay);
  }
  // Timed out — surface the most recent response shape so callers
  // render the "could not understand" branch instead of a hard error.
  return {
    data: {
      status: 'timeout',
      transcript: null,
      engine: 'unavailable',
      duration_ms: 0,
      job_id: jobId,
    },
  };
};

const transcribeAudio = async (file) => {
  const formData = new FormData();
  formData.append('file', file);
  const res = await api.post('/media/transcribe', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 90000,
  });
  // Phase A of the api image diet pushed whisper to the code-worker;
  // short clips still return inline (`status: completed`), longer clips
  // come back as `{ status: "pending", job_id }` and we poll until done.
  if (res?.data?.status === 'pending' && res?.data?.job_id) {
    return pollTranscription(res.data.job_id);
  }
  return res;
};

const mediaService = {
  transcribeAudio,
};

export default mediaService;
