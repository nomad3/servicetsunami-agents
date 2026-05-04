/**
 * Polls /fleet/snapshot every 60s for live agent status + collaboration
 * updates. Phase A intentionally polls rather than streaming because the
 * existing /collaborations/stream SSE is per-session, not tenant-wide, and
 * EventSource doesn't carry our Authorization header (no Bearer pre-flight).
 *
 * A tenant-wide live SSE feed lands in Phase B alongside the Score zone.
 * For now the conductor sees fleet drift on a 1-minute heartbeat, which is
 * fine for the activity-halo + comms-beam feel since coalitions typically
 * run for minutes.
 */
import { useEffect } from 'react';
import { apiJson } from '../api';

const POLL_INTERVAL_MS = 60_000;

export function useFleetStream(setSnapshot) {
  useEffect(() => {
    let cancelled = false;
    const poll = setInterval(async () => {
      if (cancelled) return;
      try {
        const body = await apiJson('/api/v1/fleet/snapshot');
        if (cancelled || !body) return;
        setSnapshot((prev) => ({
          ...prev,
          agents: body.agents || prev.agents,
          groups: body.groups || prev.groups,
          active_collaborations: body.active_collaborations || prev.active_collaborations,
          notifications: body.notifications || prev.notifications,
          commitments: body.commitments || prev.commitments,
          captured_at: body.captured_at,
        }));
      } catch {
        // apiJson already handles 401 → luna:logout; transient errors are quiet.
      }
    }, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(poll);
    };
  }, [setSnapshot]);
}
