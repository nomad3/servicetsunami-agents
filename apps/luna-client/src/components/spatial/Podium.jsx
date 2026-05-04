/**
 * Podium — the central scene composition. Arranges agent_groups as section
 * clusters in a fan around the conductor's viewpoint, draws comms beams
 * between agents in active A2A collaborations, and exposes hover/click
 * events for point-and-voice dispatch.
 *
 * The orbit / camera control sits in PodiumScene (the wrapping Canvas);
 * this component is purely scene content.
 */
import React, { useMemo, useState } from 'react';
import SectionCluster from './SectionCluster';
import CommsBeam from './CommsBeam';

const SECTION_RADIUS = 4.2;        // distance from podium center to each section
const FAN_HALF_ANGLE_DEG = 75;     // the orchestra arcs ±75° in front of the user
const SECTION_RING_RADIUS = 1.4;   // size of each section's avatar ring

function fanLayout(count, halfAngleDeg = FAN_HALF_ANGLE_DEG, radius = SECTION_RADIUS) {
  if (count === 0) return [];
  if (count === 1) return [[0, 0, -radius]];
  const span = (halfAngleDeg * 2 * Math.PI) / 180;
  const start = -span / 2;
  const step = span / (count - 1);
  return Array.from({ length: count }, (_, i) => {
    const theta = start + step * i - Math.PI / 2; // -PI/2 puts angle 0 in front
    return [Math.cos(theta) * radius, 0, Math.sin(theta) * radius];
  });
}

function findAgentPosition(agentId, sections) {
  for (const sec of sections) {
    const idx = sec.agents.findIndex((a) => a.id === agentId);
    if (idx >= 0 && sec.positions[idx]) return sec.positions[idx];
  }
  return null;
}

export default function Podium({ snapshot, armed }) {
  const [targetedAgentId, setTargetedAgentId] = useState(null);

  // Bucket agents by team_id; agents without team go in "Unassigned".
  const sections = useMemo(() => {
    const byTeam = new Map();
    for (const g of snapshot.groups || []) byTeam.set(g.id, { group: g, agents: [] });
    byTeam.set('__unassigned__', { group: { id: '__unassigned__', name: 'Unassigned' }, agents: [] });
    for (const a of snapshot.agents || []) {
      const key = a.team_id || '__unassigned__';
      if (!byTeam.has(key)) {
        // Stale team_id — bucket under unassigned rather than dropping
        byTeam.get('__unassigned__').agents.push(a);
      } else {
        byTeam.get(key).agents.push(a);
      }
    }
    // Drop empty buckets so layout doesn't reserve fan slots for nothing.
    const sectionsArr = Array.from(byTeam.values()).filter((s) => s.agents.length > 0);
    const centers = fanLayout(sectionsArr.length);
    return sectionsArr.map((s, i) => {
      const center = centers[i];
      // Compute per-agent positions inside each section ring once for beam lookup
      const n = s.agents.length || 1;
      const positions = s.agents.map((_, j) => {
        const theta = (j / n) * Math.PI * 2;
        return [
          center[0] + Math.cos(theta) * SECTION_RING_RADIUS,
          center[1],
          center[2] + Math.sin(theta) * SECTION_RING_RADIUS,
        ];
      });
      return { ...s, center, positions };
    });
  }, [snapshot.agents, snapshot.groups]);

  // Build comms beams from active collaborations
  const beams = useMemo(() => {
    const out = [];
    for (const collab of snapshot.active_collaborations || []) {
      const ids = (collab.participants || []).filter(Boolean);
      // Pair consecutive participants — a chain visualization.
      for (let i = 0; i < ids.length - 1; i++) {
        const a = findAgentPosition(ids[i], sections);
        const b = findAgentPosition(ids[i + 1], sections);
        if (a && b) out.push({ key: `${collab.id}-${i}`, from: a, to: b });
      }
    }
    return out;
  }, [snapshot.active_collaborations, sections]);

  const handleAvatarEvent = (e) => {
    if (e.hover === true) {
      setTargetedAgentId(e.agentId);
      window.dispatchEvent(
        new CustomEvent('luna-podium-target-agent', { detail: { agentId: e.agentId } }),
      );
    }
    if (e.hover === false && targetedAgentId === e.agentId) {
      setTargetedAgentId(null);
    }
  };

  return (
    <group>
      {/* Ambient + key lighting — soft so emissive avatars carry the look */}
      <ambientLight intensity={0.35} />
      <pointLight position={[0, 4, 2]} intensity={0.6} color="#aaccff" />
      <pointLight position={[0, 0, 0]} intensity={0.4} color="#ffeecc" />

      {sections.map((s) => (
        <SectionCluster
          key={s.group.id}
          group={s.group}
          agents={s.agents}
          center={s.center}
          radius={SECTION_RING_RADIUS}
          targetedAgentId={targetedAgentId}
          onAvatarEvent={handleAvatarEvent}
        />
      ))}

      {beams.map((b) => (
        <CommsBeam key={b.key} from={b.from} to={b.to} armed={armed} />
      ))}
    </group>
  );
}
