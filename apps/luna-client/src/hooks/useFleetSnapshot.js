/**
 * Loads the Luna OS Podium boot snapshot from /fleet/snapshot. Single-shot
 * fetch on mount; live deltas come through useFleetStream and merge into
 * the same setSnapshot the caller provides.
 *
 * The PodiumScene owns the canonical `snapshot` state and threads its
 * setter through here so all updates land in one place — no fight between
 * "initial fetch state" and "live deltas state".
 */
import { useEffect } from 'react';
import { apiJson } from '../api';

export function useFleetSnapshot(setSnapshot) {
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const body = await apiJson('/api/v1/fleet/snapshot');
        if (cancelled) return;
        setSnapshot((prev) => ({
          ...prev,
          agents: body.agents || [],
          groups: body.groups || [],
          active_collaborations: body.active_collaborations || [],
          notifications: body.notifications || [],
          commitments: body.commitments || [],
          captured_at: body.captured_at,
          loaded: true,
          error: null,
        }));
      } catch (e) {
        if (cancelled) return;
        setSnapshot((prev) => ({ ...prev, loaded: true, error: e }));
      }
    })();
    return () => { cancelled = true; };
  }, [setSnapshot]);
}
