import React, { useState, useEffect, useRef, useCallback } from 'react';
import './CollaborationPanel.css';

const PHASE_LABELS = {
  triage: 'Triage',
  investigate: 'Investigate',
  analyze: 'Analyze',
  command: 'Command',
  propose: 'Propose',
  critique: 'Critique',
  revise: 'Revise',
  verify: 'Verify',
  research: 'Research',
  synthesize: 'Synthesize',
};

const replayBtnStyle = {
  padding: '3px 10px',
  borderRadius: 4,
  border: '1px solid rgba(100,180,255,0.3)',
  background: 'rgba(100,180,255,0.08)',
  color: '#64b4ff',
  fontSize: 11,
  cursor: 'pointer',
};

function EntryCard({ entry, isHighlighted }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = (entry.content_full || entry.content_preview || '').length > 300;

  return (
    <div className={`blackboard-entry${isHighlighted ? ' highlighted' : ''}`}>
      <div className="blackboard-entry__header">
        <span className="blackboard-entry__agent">{entry.author_slug}</span>
        <span className={`blackboard-entry__role-badge role-badge--${entry.author_role}`}>
          {entry.author_role}
        </span>
        <span className="blackboard-entry__type">{entry.entry_type}</span>
      </div>
      <div className={`blackboard-entry__content${isLong && !expanded ? ' collapsed' : ''}`}>
        {entry.content_full || entry.content_preview}
      </div>
      {isLong && (
        <button className="blackboard-entry__expand" onClick={() => setExpanded(!expanded)}>
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}
      <div className="confidence-bar">
        <div className="confidence-bar__fill" style={{ width: `${(entry.confidence || 0.7) * 100}%` }} />
      </div>
      <div className="blackboard-entry__meta">
        <span>v{entry.board_version}</span>
        {entry.timestamp && (
          <span>{new Date(entry.timestamp * 1000).toLocaleTimeString()}</span>
        )}
      </div>
    </div>
  );
}

/**
 * CollaborationPanel
 *
 * Props:
 *   collaborationId  — UUID of the active/completed collaboration
 *   phases           — array of phase names e.g. ['triage','investigate','analyze','command']
 *   apiBaseUrl       — e.g. '/api/v1'
 *   token            — JWT for auth headers
 *   isCompleted      — boolean, true = replay mode available
 */
export default function CollaborationPanel({ collaborationId, phases, apiBaseUrl, token, isCompleted }) {
  const [entries, setEntries] = useState([]);
  const [activePhase, setActivePhase] = useState(null);
  const [completedPhases, setCompletedPhases] = useState([]);
  const [status, setStatus] = useState(isCompleted ? 'completed' : 'active');
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [mode, setMode] = useState(isCompleted ? 'replay' : 'live');

  // Replay state
  const [replayIndex, setReplayIndex] = useState(-1);
  const [isReplaying, setIsReplaying] = useState(false);
  const [replaySpeed, setReplaySpeed] = useState(1);
  const allEntriesRef = useRef([]);

  const feedRef = useRef(null);
  const startTimeRef = useRef(Date.now());
  const streamCtrlRef = useRef(null);

  // Live mode: open SSE stream using fetch (native EventSource cannot send JWT headers)
  useEffect(() => {
    if (mode !== 'live' || !collaborationId) return;

    const ctrl = new AbortController();
    streamCtrlRef.current = ctrl;

    (async () => {
      try {
        const res = await fetch(`${apiBaseUrl}/collaborations/${collaborationId}/stream`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: ctrl.signal,
        });
        if (!res.ok) return;
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split('\n');
          buf = lines.pop();
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            try { handleEvent(JSON.parse(line.slice(6))); } catch (_) {}
          }
        }
      } catch (e) {
        if (e.name !== 'AbortError') console.warn('[CollabPanel] SSE error', e);
      }
    })();

    return () => { ctrl.abort(); };
  }, [collaborationId, mode]);

  // Load full detail for replay mode
  useEffect(() => {
    if (mode !== 'replay' || !collaborationId) return;

    fetch(`${apiBaseUrl}/collaborations/${collaborationId}/detail`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(r => r.json())
      .then(data => {
        allEntriesRef.current = data.entries || [];
        setCompletedPhases(phases || []);
        setEntries([]);
        setReplayIndex(-1);
      });
  }, [collaborationId, mode]);

  function handleEvent(data) {
    switch (data.event_type) {
      case 'phase_started':
        setActivePhase(data.payload.phase);
        break;
      case 'blackboard_entry':
        setEntries(prev => [...prev, data.payload]);
        setTimeout(() => {
          feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight, behavior: 'smooth' });
        }, 50);
        break;
      case 'phase_completed':
        setCompletedPhases(prev => [...new Set([...prev, data.payload.phase])]);
        break;
      case 'collaboration_completed':
        setStatus('completed');
        setMode('replay');
        break;
      default:
        break;
    }
  }

  // Elapsed timer for live mode
  useEffect(() => {
    if (status !== 'active') return;
    const t = setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startTimeRef.current) / 1000));
    }, 1000);
    return () => clearInterval(t);
  }, [status]);

  // Replay step-through
  const stepReplay = useCallback((dir) => {
    const all = allEntriesRef.current;
    const next = Math.max(-1, Math.min(all.length - 1, replayIndex + dir));
    setReplayIndex(next);
    setEntries(next < 0 ? [] : all.slice(0, next + 1));
  }, [replayIndex]);

  // Keyboard navigation for replay
  useEffect(() => {
    if (mode !== 'replay') return;
    const handler = (e) => {
      if (e.key === 'ArrowRight') stepReplay(1);
      if (e.key === 'ArrowLeft') stepReplay(-1);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [mode, stepReplay]);

  // Auto-replay
  useEffect(() => {
    if (!isReplaying) return;
    const all = allEntriesRef.current;
    if (replayIndex >= all.length - 1) { setIsReplaying(false); return; }
    const delay = replaySpeed === 1 ? 2000 : replaySpeed === 2 ? 1000 : 400;
    const t = setTimeout(() => stepReplay(1), delay);
    return () => clearTimeout(t);
  }, [isReplaying, replayIndex, replaySpeed, stepReplay]);

  const phasesOrder = phases || ['triage', 'investigate', 'analyze', 'command'];

  return (
    <div className="collaboration-panel">
      <div className="collaboration-panel__header">
        <h3 className="collaboration-panel__title">Agent Collaboration</h3>
        <span className="collaboration-panel__mode-badge">
          {mode === 'live' ? 'LIVE' : 'REPLAY'}
        </span>
      </div>

      {/* Phase Timeline */}
      <div className="phase-timeline">
        {phasesOrder.map((phase, i) => {
          const isActive = activePhase === phase;
          const isDone = completedPhases.includes(phase);
          return (
            <div
              key={phase}
              className={`phase-step ${isActive ? 'active' : ''} ${isDone ? 'completed' : ''}`}
            >
              <div className="phase-step__dot">
                {isDone ? '✓' : i + 1}
              </div>
              <div className="phase-step__label">{PHASE_LABELS[phase] || phase}</div>
            </div>
          );
        })}
      </div>

      {/* Blackboard Feed */}
      <div className="blackboard-feed" ref={feedRef}>
        {entries.length === 0 && (
          <div style={{ color: 'rgba(224,240,255,0.3)', fontSize: 13, textAlign: 'center', marginTop: 24 }}>
            {mode === 'live' ? 'Waiting for agents...' : 'Press → to step through'}
          </div>
        )}
        {entries.map((entry, idx) => (
          <EntryCard
            key={entry.entry_id || idx}
            entry={entry}
            isHighlighted={mode === 'replay' && idx === replayIndex}
          />
        ))}
      </div>

      {/* Status Bar */}
      <div className="collaboration-status-bar">
        <div className={`status-dot ${status === 'active' ? 'active' : 'completed'}`} />
        <span>{status === 'active' ? `${elapsedSeconds}s` : 'Completed'}</span>
        <span>{entries.length} contributions</span>
        {mode === 'live' && status === 'active' && (
          <span style={{ marginLeft: 'auto', color: '#64b4ff' }}>● Live</span>
        )}
        {status === 'completed' && mode === 'live' && (
          <button style={{ marginLeft: 'auto', ...replayBtnStyle }} onClick={() => setMode('replay')}>
            Replay
          </button>
        )}
      </div>

      {/* Replay Controls (visible in replay mode) */}
      {mode === 'replay' && (
        <div className="replay-controls">
          <button className="replay-btn" onClick={() => { setReplayIndex(-1); setEntries([]); setIsReplaying(false); }}>
            ↩ Reset
          </button>
          <button className="replay-btn" onClick={() => stepReplay(-1)}>‹</button>
          <button className="replay-btn" onClick={() => setIsReplaying(!isReplaying)}>
            {isReplaying ? '⏸' : '▶'}
          </button>
          <button className="replay-btn" onClick={() => stepReplay(1)}>›</button>
          <button
            className="replay-btn"
            onClick={() => {
              setEntries(allEntriesRef.current);
              setReplayIndex(allEntriesRef.current.length - 1);
              setIsReplaying(false);
            }}
          >
            ⏭ End
          </button>
          <select
            className="replay-speed-select"
            value={replaySpeed}
            onChange={e => setReplaySpeed(Number(e.target.value))}
          >
            <option value={1}>1×</option>
            <option value={2}>2×</option>
            <option value={5}>5×</option>
          </select>
          <span style={{ fontSize: 11, color: 'rgba(224,240,255,0.3)', marginLeft: 'auto' }}>
            {replayIndex + 1}/{allEntriesRef.current.length}
          </span>
        </div>
      )}
    </div>
  );
}
