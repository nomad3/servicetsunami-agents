/**
 * Subscribes to live fleet deltas: collaboration events from the existing
 * /collaborations/stream SSE feed. Updates the active_collaborations array
 * in the caller-supplied setSnapshot.
 *
 * Phase A only wires collaboration events — agent status snapshots are
 * refreshed on a slower interval via re-fetching /fleet/snapshot every
 * 60s. A dedicated agent-status SSE feed is a Phase B+ item.
 */
import { useEffect } from 'react';
import { API_BASE } from '../api';

export function useFleetStream(setSnapshot) {
  useEffect(() => {
    let cancelled = false;
    let evtSource = null;

    const token = localStorage.getItem('luna_token');
    if (!token) return;

    try {
      // EventSource doesn't support Authorization header — pass token in
      // the URL only if the API allows it; otherwise skip and rely on
      // periodic re-snapshot (every 60s, set up below).
      evtSource = new EventSource(
        `${API_BASE}/api/v1/collaborations/stream?token=${encodeURIComponent(token)}`,
      );
      evtSource.onmessage = (e) => {
        if (cancelled) return;
        try {
          const evt = JSON.parse(e.data);
          if (!evt || !evt.collaboration_id) return;
          setSnapshot((prev) => {
            const existing = prev.active_collaborations.find(
              (c) => c.id === evt.collaboration_id,
            );
            if (existing) {
              return {
                ...prev,
                active_collaborations: prev.active_collaborations.map((c) =>
                  c.id === evt.collaboration_id
                    ? { ...c, phase: evt.phase || c.phase, last_event: evt }
                    : c,
                ),
              };
            }
            return {
              ...prev,
              active_collaborations: [
                ...prev.active_collaborations,
                {
                  id: evt.collaboration_id,
                  pattern: evt.pattern || null,
                  phase: evt.phase || null,
                  participants: evt.participants || [],
                  started_at: evt.timestamp || null,
                  last_event: evt,
                },
              ],
            };
          });
        } catch {}
      };
      evtSource.onerror = () => {
        // SSE auth or transient failure — silently rely on /fleet/snapshot polling
      };
    } catch {}

    // Periodic re-snapshot every 60s for agent status drift.
    const poll = setInterval(async () => {
      if (cancelled) return;
      try {
        const body = await fetch(`${API_BASE}/api/v1/fleet/snapshot`, {
          headers: { Authorization: `Bearer ${token}` },
        }).then((r) => (r.ok ? r.json() : null));
        if (!body || cancelled) return;
        setSnapshot((prev) => ({
          ...prev,
          agents: body.agents || prev.agents,
          groups: body.groups || prev.groups,
          notifications: body.notifications || prev.notifications,
          commitments: body.commitments || prev.commitments,
          captured_at: body.captured_at,
        }));
      } catch {}
    }, 60_000);

    return () => {
      cancelled = true;
      try { evtSource?.close(); } catch {}
      clearInterval(poll);
    };
  }, [setSnapshot]);
}
