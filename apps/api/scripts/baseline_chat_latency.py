"""Measure end-to-end chat latency on the current code path.

Sends N messages through POST /api/v1/chat/sessions/{id}/messages,
records p50/p95/p99 wall-clock latency. Run against the local
docker-compose stack BEFORE any Phase 1 changes — this is the
baseline that Phase 1 must not regress (anti-success criterion §11.1).

Usage:
    BASELINE_TOKEN=<jwt> \\
    BASELINE_SESSION_ID=<session_uuid> \\
    BASELINE_N=20 \\
    python apps/api/scripts/baseline_chat_latency.py

Env:
    API_BASE_URL    default http://localhost:8000
    BASELINE_TOKEN  required — JWT bearer token
    BASELINE_SESSION_ID  required — UUID of an existing chat session
    BASELINE_N      default 20 — number of probe messages
    BASELINE_LABEL  optional — short tag added to the output JSON
"""
import asyncio
import json
import os
import statistics
import sys
import time

import httpx

API = os.environ.get("API_BASE_URL", "http://localhost:8000")
TOKEN = os.environ.get("BASELINE_TOKEN")
SESSION_ID = os.environ.get("BASELINE_SESSION_ID")
N = int(os.environ.get("BASELINE_N", "20"))
LABEL = os.environ.get("BASELINE_LABEL", "")

if not TOKEN or not SESSION_ID:
    sys.stderr.write(
        "ERROR: BASELINE_TOKEN and BASELINE_SESSION_ID env vars required.\n"
    )
    sys.exit(2)

PROMPTS = [
    "hey luna",
    "what are my open commitments",
    "remind me what we discussed yesterday",
    "who is Ray Aristy",
    "what is my next meeting",
    "what's the status of integral",
    "summarize the memory-first design doc",
    "thanks",
    "ok",
    "what platforms are we tracking competitors on",
]


async def main() -> int:
    latencies: list[float] = []
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=120) as c:
        for i in range(N):
            prompt = PROMPTS[i % len(PROMPTS)]
            t0 = time.perf_counter()
            try:
                r = await c.post(
                    f"{API}/api/v1/chat/sessions/{SESSION_ID}/messages",
                    headers={"Authorization": f"Bearer {TOKEN}"},
                    json={"content": prompt},
                )
                elapsed = time.perf_counter() - t0
                if r.status_code >= 300:
                    errors.append(f"{i}: HTTP {r.status_code} — {r.text[:120]}")
                    continue
                latencies.append(elapsed)
                sys.stderr.write(f"  {i + 1}/{N} {elapsed:.2f}s '{prompt}'\n")
            except Exception as e:
                errors.append(f"{i}: {type(e).__name__}: {e}")

    if not latencies:
        sys.stderr.write(f"FATAL: no successful requests. errors={errors}\n")
        return 1

    latencies.sort()
    n = len(latencies)
    report = {
        "label": LABEL,
        "n_requested": N,
        "n_success": n,
        "n_errors": len(errors),
        "p50_seconds": round(latencies[n // 2], 3),
        "p95_seconds": round(latencies[min(n - 1, int(n * 0.95))], 3),
        "p99_seconds": round(latencies[min(n - 1, int(n * 0.99))], 3),
        "mean_seconds": round(statistics.mean(latencies), 3),
        "max_seconds": round(max(latencies), 3),
        "min_seconds": round(min(latencies), 3),
        "errors": errors[:5],
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
