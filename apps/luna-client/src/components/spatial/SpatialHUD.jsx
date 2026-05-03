import React, { useEffect, useState, useRef, useMemo } from 'react';
import KnowledgeNebula from './KnowledgeNebula';
import { apiJson } from '../../api';
import { useGesture } from '../../hooks/useGesture';
import './SpatialHUD.css';

const AGENT_COLORS = {
  'triage_agent': '#ff0055',
  'investigator': '#00ffaa',
  'analyst': '#aa00ff',
  'commander': '#ffaa00'
};

// Bridges the gesture engine's wake state to the HUD's "spatial sync"
// indicator. Replaces the camera-ownership the deleted GestureController
// had — the engine is now the sole camera owner.
function SpatialHudGestureSync({ onSyncChange }) {
  const { wakeState } = useGesture();
  React.useEffect(() => {
    onSyncChange?.(wakeState === 'armed');
  }, [wakeState, onSyncChange]);
  return null;
}

export default function SpatialHUD() {
  const [stats, setStats] = useState({ tokens: 65, cost: 0.42, manaPercent: 65 });
  const [activeQuests, setActiveQuests] = useState([]);
  const [commsLog, setCommsLog] = useState([]);
  const [trackingActive, setTrackingActive] = useState(false);
  const [nodes, setNodes] = useState([]);
  const [agents, setAgents] = useState([]);
  const [beams, setBeams] = useState([]);
  const [consensus, setConsensus] = useState(0);
  const [showInventory, setShowInventory] = useState(false);
  const lastFrameRef = useRef(0);
  
  useEffect(() => {
    // 1. Fetch real embeddings and project them via Rust
    (async () => {
      try {
        const { invoke } = await import('@tauri-apps/api/core');
        const memories = await apiJson('/api/v1/memories/spatial?limit=100');
        if (memories && memories.length > 0) {
          const vectors = memories.map(r => r.embedding).filter(Boolean);
          const ids = memories.map(r => r.id);
          if (vectors.length > 2) {
            const projections = await invoke('project_embeddings', { vectors, ids });
            const projectedNodes = projections.map(p => {
              const original = memories.find(r => r.id === p.id);
              return {
                id: p.id,
                position: [p.x, p.y, p.z],
                name: original.text_content?.substring(0, 30) || 'Unknown',
                type: original.content_type || 'memory',
              };
            });
            setNodes(projectedNodes);
          }
        }
      } catch (e) {
        console.warn('Nebula population failed:', e);
      }
    })();

    // 2. Start native spatial capture (Heartbeat for Sync UI)
    let unlistenFrame;
    (async () => {
      try {
        const { invoke } = await import('@tauri-apps/api/core');
        const { listen } = await import('@tauri-apps/api/event');
        await invoke('start_spatial_capture');
        unlistenFrame = await listen('spatial-frame', (event) => {
          lastFrameRef.current = Date.now();
        });
      } catch (e) {
        console.warn('Spatial capture failed:', e);
      }
    })();

    // 3. Listen for Live Collaboration Events
    let eventUnlisten;
    (async () => {
      try {
        const { listen } = await import('@tauri-apps/api/event');
        eventUnlisten = await listen('collaboration-event', (event) => {
          const { event_type, payload } = event.payload;
          switch(event_type) {
            case 'collaboration_started':
              setConsensus(0);
              setActiveQuests(prev => [...prev, {
                id: payload.collaboration_id,
                title: `RAID: ${payload.pattern.toUpperCase()}`,
                phase: 'INITIALIZING',
                progress: 0
              }]);
              setAgents(payload.agents.map(a => ({
                id: a.agent_slug,
                name: a.agent_slug.split('_')[0].toUpperCase(),
                role: a.role,
                targetPosition: [0, 0, 0],
                color: AGENT_COLORS[a.agent_slug] || '#ffffff'
              })));
              break;
            case 'phase_started':
              setActiveQuests(prev => prev.map(q => 
                q.id === payload.collaboration_id 
                  ? { ...q, phase: payload.phase.toUpperCase(), progress: Math.min(q.progress + 20, 90) } 
                  : q
              ));
              setAgents(prev => prev.map(a => {
                if (a.role === payload.phase) {
                   const randomNode = nodes[Math.floor(Math.random() * nodes.length)];
                   return { ...a, targetPosition: randomNode ? randomNode.position : [Math.random()*50, 0, Math.random()*50] };
                }
                return a;
              }));
              break;
            case 'blackboard_entry':
              setCommsLog(prev => [{
                time: new Date().toLocaleTimeString(),
                agent: payload.author_slug,
                text: payload.content_preview,
                active: true
              }, ...prev].slice(0, 50));
              const author = agents.find(a => a.id === payload.author_slug);
              if (author) {
                const newBeam = { start: author.targetPosition, end: [0, 0, 0], active: true };
                setBeams(prev => [...prev, newBeam]);
                setTimeout(() => setBeams(prev => prev.filter(b => b !== newBeam)), 2000);
              }
              setConsensus(prev => Math.min(prev + 5, 95));
              break;
            case 'collaboration_completed':
              setConsensus(100);
              setActiveQuests(prev => prev.map(q => 
                q.id === payload.collaboration_id 
                  ? { ...q, phase: 'COMPLETED', progress: 100 } 
                  : q
              ));
              setAgents(prev => prev.map(a => ({ ...a, targetPosition: [0, 0, 0] })));
              break;
          }
        });
      } catch (e) {
        console.warn('Event listener failed:', e);
      }
    })();

    // Keyboard controller
    const handleKeyDown = (e) => {
      switch(e.code) {
        case 'Tab': e.preventDefault(); setShowInventory(prev => !prev); break;
        default: break;
      }
    };
    window.addEventListener('keydown', handleKeyDown);

    return () => {
      unlistenFrame?.();
      eventUnlisten?.();
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, []); // Only on mount

  return (
    <div className={`spatial-hud-container ${consensus >= 90 ? 'consensus-glow' : ''}`}>
      <KnowledgeNebula nodes={nodes} agents={agents} beams={beams} />
      <SpatialHudGestureSync onSyncChange={setTrackingActive} />

      <header className="hud-top">
        <div className="hud-group">
          <div className="hud-stat">
            <label>SPATIAL SYNC</label>
            <div className={`sync-indicator ${trackingActive ? 'active' : 'searching'}`}>
              {trackingActive ? 'LOCKED' : 'SEARCHING...'}
            </div>
          </div>
          <div className="hud-stat">
            <label>MANA (TOKENS)</label>
            <div className="hud-bar-container">
              <div className="hud-bar-fill" style={{width: `${stats.manaPercent}%`}}></div>
            </div>
          </div>
          <div className="hud-stat">
            <label>PARTY HEAT</label>
            <span className="hud-value">${stats.cost.toFixed(2)} <small>USD</small></span>
          </div>
        </div>

        <div className="hud-party">
          {agents.map((agent, i) => (
            <div key={agent.id} className={`party-member ${i === 0 ? 'active' : ''}`} style={{borderBottom: `3px solid ${agent.color}`}}>
              <div className="member-status">LVL 1</div>
              <div className="member-name">{agent.name}</div>
            </div>
          ))}
          {agents.length === 0 && [1,2,3,4].map(i => (
            <div key={i} className="party-member empty">
              <div className="member-status">EMPTY</div>
              <div className="member-name">SLOT {i}</div>
            </div>
          ))}
        </div>
      </header>

      <aside className="hud-left">
        <div className="hud-module-label">ACTIVE MISSIONS</div>
        {activeQuests.length === 0 && <div className="no-quests" style={{color: 'rgba(100,180,255,0.4)', padding: '10px'}}>NO ACTIVE RAIDS</div>}
        {activeQuests.map(quest => (
          <div key={quest.id} className="quest-card">
            <div className="quest-title">{quest.title}</div>
            <div className="quest-progress-info">
              <span>PHASE: {quest.phase}</span>
              <span>{quest.progress}%</span>
            </div>
            <div className="quest-progress-bar">
              <div className="quest-progress-fill" style={{width: `${quest.progress}%`}}></div>
            </div>
          </div>
        ))}
      </aside>

      {showInventory && (
        <div className="hud-inventory-overlay">
          <div className="hud-module-label">SHARED BLACKBOARD (INVENTORY)</div>
          <div className="inventory-grid">
            {commsLog.map((item, i) => (
              <div key={i} className="inventory-item">
                <div className="item-icon" style={{backgroundColor: AGENT_COLORS[item.agent] || '#fff'}}></div>
                <div className="item-text">{item.text}</div>
              </div>
            ))}
            {commsLog.length === 0 && <div className="inventory-empty">INVENTORY EMPTY</div>}
          </div>
        </div>
      )}

      <footer className="hud-bottom">
        <div className="hud-module-label">A2A COMBAT LOG</div>
        <div className="comms-terminal">
          {commsLog.length === 0 && <div className="comms-placeholder" style={{opacity: 0.3, fontSize: '10px'}}>WAITING FOR PARTY COMMS...</div>}
          {commsLog.map((log, i) => (
            <div key={i} className={`comms-line ${log.active ? 'active' : ''}`}>
              <span className="time">{log.time}</span> <span className="agent" style={{color: AGENT_COLORS[log.agent]}}>{log.agent}</span>: {log.text}
            </div>
          ))}
          <div className="cursor-blink">_</div>
        </div>
      </footer>

      <div className="consensus-meter">
        <div className="meter-label">COALITION CONSENSUS</div>
        <div className="meter-container">
          <div className="meter-fill" style={{width: `${consensus}%`}}></div>
        </div>
      </div>

      <div className="hud-crosshair">
        <div className="ch-top"></div>
        <div className="ch-bottom"></div>
        <div className="ch-left"></div>
        <div className="ch-right"></div>
      </div>
    </div>
  );
}
