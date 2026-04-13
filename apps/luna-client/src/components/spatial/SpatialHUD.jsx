import React, { useEffect, useState, useRef } from 'react';
import KnowledgeNebula from './KnowledgeNebula';
import './SpatialHUD.css';

export default function SpatialHUD() {
  const [stats, setStats] = useState({ tokens: 0, cost: 0, agents: [] });
  const [activeQuests, setActiveQuests] = useState([]);
  
  useEffect(() => {
    // Keyboard controller (WASD + Gaming shortcuts)
    const handleKeyDown = (e) => {
      // Future: connect to 3D camera controller
      switch(e.code) {
        case 'KeyW': console.log('Flight: Forward'); break;
        case 'KeyA': console.log('Flight: Left'); break;
        case 'KeyS': console.log('Flight: Backward'); break;
        case 'KeyD': console.log('Flight: Right'); break;
        case 'Space': console.log('Flight: Up'); break;
        case 'ControlLeft': console.log('Flight: Down'); break;
        case 'Tab': console.log('Toggle Minimap'); break;
        case 'Digit1': case 'Digit2': case 'Digit3': case 'Digit4':
          console.log('Switch Agent Party member:', e.code.replace('Digit', ''));
          break;
        default: break;
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  return (
    <div className="spatial-hud-container">
      {/* 3D Nebula Layer */}
      <KnowledgeNebula />

      {/* Top Resource HUD */}
      <header className="hud-top">
        <div className="hud-group">
          <div className="hud-stat">
            <label>TOKEN BANDWIDTH</label>
            <div className="hud-bar-container">
              <div className="hud-bar-fill" style={{width: '65%'}}></div>
            </div>
          </div>
          <div className="hud-stat">
            <label>COMPUTE HEAT</label>
            <span className="hud-value">$0.42 <small>USD</small></span>
          </div>
        </div>

        <div className="hud-party">
          <div className="party-member active">
            <div className="member-status">THINKING</div>
            <div className="member-name">Triage</div>
          </div>
          <div className="party-member">
            <div className="member-status">READY</div>
            <div className="member-name">Data-Inv</div>
          </div>
          <div className="party-member locked">
            <div className="member-status">LOCKED</div>
            <div className="member-name">Analyst</div>
          </div>
          <div className="party-member locked">
            <div className="member-status">LOCKED</div>
            <div className="member-name">Commander</div>
          </div>
        </div>
      </header>

      {/* Side Quest Log (Mission HUD) */}
      <aside className="hud-left">
        <div className="hud-module-label">ACTIVE MISSIONS</div>
        <div className="quest-card">
          <div className="quest-title">MDM PRICING DISCREPANCY</div>
          <div className="quest-progress-info">
            <span>PHASE: INVESTIGATE</span>
            <span>42%</span>
          </div>
          <div className="quest-progress-bar">
            <div className="quest-progress-fill" style={{width: '42%'}}></div>
          </div>
          <div className="quest-objectives">
            <div className="objective done">Classify severity</div>
            <div className="objective active">Identify breaking schema change</div>
            <div className="objective">Calculate cascade impact</div>
          </div>
        </div>
      </aside>

      {/* Bottom A2A Combat Log */}
      <footer className="hud-bottom">
        <div className="hud-module-label">A2A COMMS LOG</div>
        <div className="comms-terminal">
          <div className="comms-line"><span className="time">14:22:01</span> <span className="agent">Triage</span>: Incident confirmed. 1,200 SKUs affected in EMEA.</div>
          <div className="comms-line"><span className="time">14:22:05</span> <span className="agent">Triage</span>: Handing off to Data-Investigator.</div>
          <div className="comms-line active"><span className="time">14:22:10</span> <span className="agent">Data-Inv</span>: Analyzing Postgres migration 089...</div>
          <div className="cursor-blink">_</div>
        </div>
      </footer>

      {/* Crosshair / Center Target */}
      <div className="hud-crosshair">
        <div className="ch-top"></div>
        <div className="ch-bottom"></div>
        <div className="ch-left"></div>
        <div className="ch-right"></div>
      </div>

      {/* Consensus Meter (Boss Bar Style) */}
      <div className="consensus-meter">
        <div className="meter-label">COALITION CONSENSUS</div>
        <div className="meter-container">
          <div className="meter-fill" style={{width: '15%'}}></div>
        </div>
      </div>
    </div>
  );
}
