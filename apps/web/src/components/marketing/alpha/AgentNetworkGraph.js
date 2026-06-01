/**
 * AgentNetworkGraph — the signature hero animation for alpha.agentprovision.com.
 *
 * This is the ONE bespoke motion piece that makes the page feel hand-built, not
 * AI-templated (Codex + Luna review, 2026-05-31). It is NOT decorative: it IS
 * the product story rendered as motion — a stateless command becoming teammate
 * work. A pulse travels the real pipeline:
 *
 *     command  →  orchestrator (alpha)  →  specialists (claude/codex/gemini)
 *                      ↓                          ↓
 *                   memory  ⇄  recall        approval gate (human-in-the-loop)
 *
 * Engines surface as node *state*, not as floating cards: a node lights teal
 * when memory recalls into it, amber when its mood shifts to "careful", and the
 * approval gate visibly PAUSES (the honesty beat) before the run completes.
 *
 * Craft constraints (so it reads bespoke + ships safe):
 *  - Deterministic, GPU-light: SVG + CSS transforms/opacity only; no canvas,
 *    no per-frame React state, no particle system. The pulse is a single
 *    <circle> animated along each edge via SMIL-free CSS keyframes keyed off a
 *    CSS custom property, so the browser compositor owns it.
 *  - Reduced motion: the graph renders fully formed and static (no traveling
 *    pulse, no shimmer) — the diagram still tells the story.
 *  - Self-contained: one component, its own CSS in AlphaLandingPage.css under
 *    `.ang-*`. No new deps.
 */
import { useReducedMotion } from 'framer-motion';

// Node layout on a 0–100 viewBox grid. Hand-placed (not auto-laid-out) so the
// pipeline reads left→right like a real run, with memory below and the human
// approval gate as the deliberate terminal beat.
const NODES = [
  { id: 'command', x: 8, y: 50, r: 4.5, label: 'command', kind: 'in' },
  { id: 'alpha', x: 30, y: 50, r: 6.5, label: 'alpha', kind: 'orchestrator' },
  { id: 'claude', x: 56, y: 24, r: 5, label: 'claude', kind: 'specialist' },
  { id: 'codex', x: 60, y: 50, r: 5, label: 'codex', kind: 'specialist' },
  { id: 'gemini', x: 56, y: 76, r: 5, label: 'gemini', kind: 'specialist' },
  { id: 'memory', x: 34, y: 82, r: 5.5, label: 'memory', kind: 'memory' },
  { id: 'gate', x: 88, y: 50, r: 6, label: 'approval', kind: 'gate' },
];

// Edges carry the pulse in causal order. `delay` staggers each pulse so a single
// signal appears to flow through the whole network, not all at once.
const EDGES = [
  { from: 'command', to: 'alpha', delay: 0 },
  { from: 'alpha', to: 'memory', delay: 0.6, recall: true }, // alpha pulls memory
  { from: 'memory', to: 'alpha', delay: 1.1, recall: true }, // recall returns
  { from: 'alpha', to: 'claude', delay: 1.6 },
  { from: 'alpha', to: 'codex', delay: 1.8 },
  { from: 'alpha', to: 'gemini', delay: 2.0 },
  { from: 'claude', to: 'gate', delay: 2.8 },
  { from: 'codex', to: 'gate', delay: 2.9 },
  { from: 'gemini', to: 'gate', delay: 3.0 },
];

const byId = Object.fromEntries(NODES.map((n) => [n.id, n]));

export default function AgentNetworkGraph() {
  const reduced = useReducedMotion();

  return (
    <div className="ang" aria-hidden="true">
      <svg
        className="ang__svg"
        viewBox="0 0 100 100"
        preserveAspectRatio="xMidYMid meet"
        role="img"
      >
        <defs>
          <radialGradient id="ang-glow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="var(--land-teal)" stopOpacity="0.55" />
            <stop offset="100%" stopColor="var(--land-teal)" stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* Edges — drawn first so nodes sit on top. */}
        <g className="ang__edges">
          {EDGES.map((e, i) => {
            const a = byId[e.from];
            const b = byId[e.to];
            return (
              <line
                key={`edge-${i}`}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                className={`ang__edge${e.recall ? ' ang__edge--recall' : ''}`}
              />
            );
          })}
        </g>

        {/* Traveling pulses — one per edge. Uses SVG native <animateMotion>
            (deterministic + GPU-accelerated + works in Safari, unlike CSS-
            animating cx/cy via calc()). Each pulse rides its edge's path and is
            staggered by `begin` so a single signal flows through the pipeline.
            Suppressed entirely under reduced motion (the static graph remains). */}
        {!reduced && (
          <g className="ang__pulses">
            {EDGES.map((e, i) => {
              const a = byId[e.from];
              const b = byId[e.to];
              const dur = e.recall ? 1.3 : 1.1;
              return (
                <circle
                  key={`pulse-${i}`}
                  r={e.recall ? 1.3 : 1.7}
                  className={`ang__pulse${e.recall ? ' ang__pulse--recall' : ''}`}
                >
                  <animateMotion
                    dur={`${dur}s`}
                    begin={`${e.delay}s`}
                    repeatCount="indefinite"
                    path={`M ${a.x} ${a.y} L ${b.x} ${b.y}`}
                    keyPoints="0;1"
                    keyTimes="0;1"
                    calcMode="spline"
                    keySplines="0.4 0 0.2 1"
                  />
                  <animate
                    attributeName="opacity"
                    dur={`${dur}s`}
                    begin={`${e.delay}s`}
                    repeatCount="indefinite"
                    values="0;1;1;0"
                    keyTimes="0;0.12;0.88;1"
                  />
                </circle>
              );
            })}
          </g>
        )}

        {/* Nodes. State (recall glow, careful/amber, gate pause) is expressed via
            class + animation-delay so the lighting tracks the pulse arrival. */}
        <g className="ang__nodes">
          {NODES.map((n) => (
            <g key={n.id} className={`ang__node ang__node--${n.kind}`}>
              {/* soft glow halo (under the node) */}
              <circle cx={n.x} cy={n.y} r={n.r * 2.1} fill="url(#ang-glow)" className="ang__halo" />
              <circle
                cx={n.x}
                cy={n.y}
                r={n.r}
                className="ang__dot"
                style={!reduced ? { animationDelay: `${nodeDelay(n.id)}s` } : undefined}
              />
              <text x={n.x} y={n.y + n.r + 4.2} className="ang__label" textAnchor="middle">
                {n.label}
              </text>
            </g>
          ))}
        </g>

        {/* The honesty beat: a small "⏸ human" tag pulsing at the gate. */}
        <text x={byId.gate.x} y={byId.gate.y - 9} className="ang__gate-tag" textAnchor="middle">
          ⏸ human
        </text>
      </svg>
    </div>
  );
}

// When each node should "light up" — roughly when the first pulse reaches it.
function nodeDelay(id) {
  switch (id) {
    case 'command': return 0;
    case 'alpha': return 0.5;
    case 'memory': return 1.1;
    case 'claude': return 2.2;
    case 'codex': return 2.4;
    case 'gemini': return 2.6;
    case 'gate': return 3.4;
    default: return 0;
  }
}
