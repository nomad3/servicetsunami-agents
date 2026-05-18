# api-image-diet — docker image shrink + cold-start latency

Started: 2026-05-18
Status: Phase A shipped (PR #566) — whisper + sentence-transformers moved
out of the api image. Subsequent phases scoped here for context.

## Goal

The api container image was ~2 GB heavier than it needed to be. The two
biggest contributors are heavy ML wheels that are only used on narrow
request paths and can run in another service. The cold-start cost of
pulling that image on every node restart is real; so is the memory
footprint inside the running container.

## Phase A — whisper to code-worker (shipped)

`openai-whisper` + `torch` (the CUDA wheels) dragged ~2 GB of binary
into the api image for three callers (POST /media/transcribe, the
robot audio endpoint, and inbound WhatsApp voice notes).

- `apps/code-worker/transcription.py` now owns whisper + soundfile +
  librosa.
- `apps/api/app/services/transcription_client.py` is the api-side
  client: writes audio to the shared `workspaces` volume, dispatches
  `TranscribeAudioWorkflow` on the existing `agentprovision-code`
  Temporal queue, awaits or 202-falls-back.
- POST `/api/v1/media/transcribe` is tenant-scoped at the job-id
  ledger level — Redis binds `{job_id: tenant_id}` with 1h TTL so a
  later poll from a different tenant returns 404, never a foreign
  transcript.

## Phase B — sentence-transformers fallback drop (shipped)

The Python `sentence-transformers` fallback in `embedding_service.py`
existed for the case where the Rust embedding-service (port 50051) is
unreachable. Per the embedding_system memory note the fallback was
never observed firing in production. Removing it saved ~600 MB of
torch wheels. `embed_text` now raises `EmbeddingServiceUnavailable`;
`try_embed_text` is the resilient wrapper backfill jobs use.

## Phase C — TBD

(Out of scope for this PR.)

## Phase D — npm CLIs in api (CORRECTED)

**Earlier framing was wrong.** The "1.01 GB removable in Phase D" line
from an earlier draft of this plan claimed that the gemini_cli_auth +
claude_auth npm packages bundled into the api image were dead weight
that could simply be deleted.

The PR #566 superpowers review confirmed they are **not** removable in
their current form:

- They power **per-request OAuth flows** — the api shells out to them
  to complete an authorisation handshake each time a tenant connects
  Gemini or Claude.
- The path from request → CLI runs **inside the api container**;
  there is no transport that hands the OAuth state to the code-worker
  today.

So "Phase D = delete the CLIs" was never the right framing. The real
work to recover that disk space is a refactor:

1. Move OAuth handshake handling to `apps/code-worker` (alongside
   the rest of the CLI execution surface).
2. Have the api dispatch an `OAuthHandshakeWorkflow` (or a sync
   activity over Temporal) instead of `subprocess.run`-ing a local
   binary.
3. Drop the npm CLIs + Node toolchain from the api Dockerfile.

That is a multi-PR project, not a "shrink the image" change. Leave
the CLIs in place for now; revisit when the api-side OAuth surface
gets its own redesign.
