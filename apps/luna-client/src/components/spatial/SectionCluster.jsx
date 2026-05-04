/**
 * One section of the orchestra — a soft ring of agent avatars representing
 * a single agent_group. Section is anchored at `center` and lays its agents
 * in a small circle around it. The section's own label floats above.
 */
import React, { useMemo } from 'react';
import { Text } from '@react-three/drei';
import AgentAvatar, { teamColor } from './AgentAvatar';

export default function SectionCluster({
  group,
  agents,
  center,
  radius = 1.4,
  targetedAgentId,
  onAvatarEvent,
}) {
  const positions = useMemo(() => {
    const n = agents.length || 1;
    return agents.map((_, i) => {
      const theta = (i / n) * Math.PI * 2;
      return [
        center[0] + Math.cos(theta) * radius,
        center[1],
        center[2] + Math.sin(theta) * radius,
      ];
    });
  }, [agents, center, radius]);

  const label = group?.name || 'Unassigned';
  const labelColor = teamColor(group?.name);

  return (
    <group>
      <Text
        position={[center[0], center[1] + 1.4, center[2]]}
        fontSize={0.28}
        color={labelColor}
        anchorX="center"
        anchorY="middle"
        outlineWidth={0.01}
        outlineColor="#000"
      >
        {label}
      </Text>
      {agents.map((a, i) => (
        <AgentAvatar
          key={a.id}
          agent={a}
          position={positions[i]}
          teamName={group?.name}
          targeted={a.id === targetedAgentId}
          onClick={(e) => onAvatarEvent && onAvatarEvent({ ...e, agentId: a.id, position: positions[i] })}
        />
      ))}
    </group>
  );
}
