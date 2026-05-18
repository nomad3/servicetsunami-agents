/*
 * Alpha Control Center — the merged Dashboard + AI Chat surface.
 *
 * Wrapped in the brand `Layout` so the global sidebar / navigation /
 * theme tokens are identical to every other page in the app. The
 * IDE-shell experiment (ActivityBar + custom title/status bars) is
 * gone — it diverged from the brand UI per user feedback.
 *
 * Layout, top to bottom:
 *   - Page header (ap-page-header)
 *   - LiveActivityFeed (existing brand widget)
 *   - System Status cards (ported from legacy dashboard)
 *   - Quick Access tiles (ported)
 *   - 3-column control row:
 *       · Sessions list (left)
 *       · Active chat thread (center) — embedded ChatTab
 *       · AgentActivityPanel (right) — live v2 SSE feed
 *
 * Alpha CLI remains the kernel: chat posts hit /api/v1/chat/sessions
 * which dispatches through `cli_session_manager`. The browser makes
 * no LLM calls directly.
 */
import { useCallback, useEffect, useState } from 'react';
import { Alert, Spinner } from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { FaColumns, FaTimes } from 'react-icons/fa';
import Layout from '../components/Layout';
import { getOnboardingStatus } from '../services/onboarding';
import chatService from '../services/chat';
import agentService from '../services/agent';
import AgentActivityPanel from '../dashboard/AgentActivityPanel';
import ChatTab from '../dashboard/tabs/ChatTab';
import TerminalPanel from '../dashboard/TerminalPanel';
import CommandPalette from '../dashboard/CommandPalette';
import TriggerCoalitionModal from '../dashboard/TriggerCoalitionModal';
import ResizableSplit from '../dashboard/ResizableSplit';
import FileTreePanel from '../dashboard/FileTreePanel';
import FileViewer from '../dashboard/FileViewer';
import { SessionEventsProvider } from '../dashboard/SessionEventsContext';
import './DashboardControlCenter.css';

// Cap the number of side-by-side chat panes ("editor groups"). Past 4
// they get unusable on a single laptop screen anyway.
const MAX_EDITOR_GROUPS = 4;

// Per-group default sizes for the nested chat-groups ResizableSplit.
// Even split — user can drag from there.
const evenSizes = (n) => Array.from({ length: n }, () => 100 / n);
const evenMins = (n) => Array.from({ length: n }, () => 280);

// Centre-pane subcomponent: renders 1..N ChatTab cards in a nested
// ResizableSplit. Each card has its own header with split/close
// buttons and a focus indicator. Extracted from the main component so
// the JSX in `DashboardControlCenter` stays readable.
const ChatGroupsPane = ({
  editorGroups,
  focusedGroupId,
  setFocusedGroupId,
  sessions,
  onSplitRight,
  onCloseSplit,
  maxGroups,
  t,
  onNewSession,
  creating,
}) => {
  const n = editorGroups.length;
  const canSplit = n < maxGroups;
  const canClose = n > 1;

  const renderGroup = (group) => {
    const session = group.sessionId ? sessions.find((s) => s.id === group.sessionId) : null;
    const isFocused = group.id === focusedGroupId;
    return (
      <article
        key={group.id}
        className={`ap-card h-100 dcc-thread-card${isFocused ? ' dcc-thread-card-focused' : ''}`}
        onPointerDownCapture={() => {
          // Click/touch/pen-to-focus the group. PointerDown-capture so
          // the focus updates *before* any click-handler inside (e.g.
          // textarea focus or session-row click) runs. Replaces the
          // mouse-only `onMouseDownCapture` so touch + stylus also
          // shift focus correctly.
          if (focusedGroupId !== group.id) setFocusedGroupId(group.id);
        }}
        onFocusCapture={() => {
          // Tab/shift-tab into any focusable child (the chat textarea,
          // a button) should also refocus this group. Without this,
          // keyboard users could be typing into pane B while group A
          // is still marked focused, and a subsequent session-pick
          // from the sidebar would land in the wrong pane.
          if (focusedGroupId !== group.id) setFocusedGroupId(group.id);
        }}
      >
        <div className="dcc-thread-header">
          <span className="dcc-thread-header-title" title={session?.title || ''}>
            {session?.title || t('chat.untitled', 'Untitled')}
          </span>
          <div className="dcc-thread-header-actions">
            <button
              type="button"
              className="dcc-thread-iconbtn"
              onClick={(e) => {
                e.stopPropagation();
                setFocusedGroupId(group.id);
                onSplitRight();
              }}
              disabled={!canSplit}
              title={canSplit ? 'Split right' : `Max ${maxGroups} splits`}
              aria-label="Split right"
            >
              <FaColumns aria-hidden="true" />
            </button>
            {canClose ? (
              <button
                type="button"
                className="dcc-thread-iconbtn"
                onClick={(e) => {
                  e.stopPropagation();
                  setFocusedGroupId(group.id);
                  onCloseSplit();
                }}
                title="Close split"
                aria-label="Close split"
              >
                <FaTimes aria-hidden="true" />
              </button>
            ) : null}
          </div>
        </div>
        <div className="ap-card-body dcc-thread-body">
          {session ? (
            <ChatTab
              tab={{
                sessionId: session.id,
                title: session.title || t('chat.untitled', 'Untitled'),
              }}
            />
          ) : (
            <div className="dcc-thread-empty">
              <p>{t('chat.pickPrompt', 'Pick a session or start a new one to chat with Alpha.')}</p>
              <button
                type="button"
                className="ap-btn-primary ap-btn-sm"
                onClick={onNewSession}
                disabled={creating}
              >
                + {creating ? t('chat.creating', 'Creating…') : t('chat.new', 'New session')}
              </button>
            </div>
          )}
        </div>
      </article>
    );
  };

  // Defensive: handleCloseSplit guards against closing the last pane,
  // but if some other code path ever empties `editorGroups` we'd hit
  // `editorGroups[0] === undefined` and crash inside renderGroup. Bail
  // out cleanly instead.
  if (n === 0) return null;

  if (n <= 1) {
    return (
      <div className="dcc-thread-pane">
        {renderGroup(editorGroups[0])}
      </div>
    );
  }

  return (
    <div className="dcc-thread-pane">
      <ResizableSplit
        key={`editor-groups-${n}`}
        storageKey={`dcc.editorGroups.sizes.${n}`}
        defaultSizes={evenSizes(n)}
        minSizes={evenMins(n)}
      >
        {editorGroups.map((g) => renderGroup(g))}
      </ResizableSplit>
    </div>
  );
};

const DashboardControlCenter = () => {
  const { t } = useTranslation('dashboard');
  const navigate = useNavigate();

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Sessions for the embedded chat surface.
  const [sessions, setSessions] = useState([]);
  const [activeSession, setActiveSession] = useState(null);

  // Editor groups — VSCode-style side-by-side chat panes. Each group
  // has its own active session. Default is a single group whose
  // sessionId is null until the first session loads (kept in sync by
  // the effects below). When the user clicks a session in the sidebar
  // it updates the *focused* group, not all groups, so split chats can
  // diverge intentionally.
  //
  // Persisted to localStorage so the layout survives navigation off
  // /dashboard and back. Without this the user lost split-pane state
  // every time they popped over to Integrations / Memory / Agents.
  const [editorGroups, setEditorGroups] = useState(() => {
    try {
      const raw = localStorage.getItem('apControl.editorGroups');
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed) && parsed.length > 0
          && parsed.every((g) => g && typeof g.id === 'string')) {
          return parsed.slice(0, MAX_EDITOR_GROUPS);
        }
      }
    } catch { /* corrupt; fall through */ }
    return [{ id: 'g0', sessionId: null }];
  });
  const [focusedGroupId, setFocusedGroupId] = useState(() => {
    try {
      return localStorage.getItem('apControl.focusedGroupId') || 'g0';
    } catch { return 'g0'; }
  });
  // Mirror editorGroups + focusedGroupId into localStorage. Saving on
  // every state mutation is fine — these are tiny arrays (max 4
  // groups) and writes are async on modern browsers.
  useEffect(() => {
    try { localStorage.setItem('apControl.editorGroups', JSON.stringify(editorGroups)); } catch { /* quota */ }
  }, [editorGroups]);
  useEffect(() => {
    try { localStorage.setItem('apControl.focusedGroupId', focusedGroupId); } catch { /* quota */ }
  }, [focusedGroupId]);

  // Agents and command-palette state for ⌘K jump.
  const [agents, setAgents] = useState([]);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [coalitionOpen, setCoalitionOpen] = useState(false);

  // Pick a session for the currently-focused editor group. This is
  // what the sidebar session-row buttons call. We also mirror the
  // selection into `activeSession` so the existing SessionEventsProvider,
  // coalition modal, and command palette keep working — they assume
  // one "active" session at a time even though the UI can now show
  // several side-by-side.
  const selectSessionForFocusedGroup = useCallback(
    (s) => {
      setEditorGroups((groups) => {
        // Happy path: a group matches the current focused id.
        const matchIdx = groups.findIndex((g) => g.id === focusedGroupId);
        if (matchIdx >= 0) {
          return groups.map((g) =>
            g.id === focusedGroupId ? { ...g, sessionId: s?.id ?? null } : g,
          );
        }
        // Fallback: focusedGroupId points at a group that no longer
        // exists (e.g. handleCloseSplit fired but a stale closure
        // still held the old id). Drop the session into the LAST
        // group and re-anchor focusedGroupId so subsequent picks land
        // there too — prevents a silent no-op where the sidebar click
        // appears to do nothing.
        if (groups.length === 0) return groups;
        const lastIdx = groups.length - 1;
        const targetId = groups[lastIdx].id;
        setFocusedGroupId(targetId);
        return groups.map((g, i) => (i === lastIdx ? { ...g, sessionId: s?.id ?? null } : g));
      });
      setActiveSession(s);
    },
    [focusedGroupId],
  );

  // Inline session creation — keeps the user on the dashboard. Was
  // previously navigating to /chat which felt like a page-mode change.
  const [creating, setCreating] = useState(false);
  const handleNewSession = async () => {
    if (creating) return;
    setCreating(true);
    try {
      // Default title with timestamp so the user can see at a glance
      // that this is a fresh session, not an existing one. Without a
      // title the server's default labelling collides with whatever
      // session the user typed in last, which looked like "reuse" in
      // the sidebar.
      const stamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      const resp = await chatService.createSession({ title: `New session · ${stamp}` });
      setSessions((prev) => [resp.data, ...prev]);
      selectSessionForFocusedGroup(resp.data);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn('createSession failed:', e);
    } finally {
      setCreating(false);
    }
  };

  // Split the focused chat group to the right. The new group inherits
  // the focused group's session so the user gets a side-by-side view
  // of the same thread, then can pick another session for the new pane.
  const handleSplitRight = () => {
    setEditorGroups((groups) => {
      if (groups.length >= MAX_EDITOR_GROUPS) return groups;
      const focusedIdx = groups.findIndex((g) => g.id === focusedGroupId);
      const idx = focusedIdx < 0 ? groups.length - 1 : focusedIdx;
      const newId = `g${Date.now().toString(36)}`;
      const newGroup = { id: newId, sessionId: groups[idx]?.sessionId ?? null };
      const next = groups.slice();
      next.splice(idx + 1, 0, newGroup);
      // Focus the freshly-split pane so subsequent sidebar clicks land
      // on the new group, matching VSCode behaviour.
      setFocusedGroupId(newId);
      return next;
    });
  };

  // Close the focused split. We never close the last remaining group —
  // there has to be at least one chat surface visible at all times.
  const handleCloseSplit = () => {
    setEditorGroups((groups) => {
      if (groups.length <= 1) return groups;
      const idx = groups.findIndex((g) => g.id === focusedGroupId);
      if (idx < 0) return groups;
      const next = groups.filter((g) => g.id !== focusedGroupId);
      // Refocus the neighbour to the left (or the new first group if
      // we closed the leftmost pane).
      const newFocusIdx = Math.max(0, idx - 1);
      setFocusedGroupId(next[newFocusIdx].id);
      return next;
    });
  };

  // Binary mode toggle: 'simple' hides the terminal card and the live
  // agent activity panel; 'pro' shows everything. Persisted to
  // localStorage; default is 'simple' for first-touch users.
  const [mode, setMode] = useState(() => {
    try {
      const v = localStorage.getItem('alpha.dashboard.mode');
      return v === 'pro' ? 'pro' : 'simple';
    } catch { return 'simple'; }
  });
  const toggleMode = () => {
    setMode((prev) => {
      const next = prev === 'simple' ? 'pro' : 'simple';
      try { localStorage.setItem('alpha.dashboard.mode', next); } catch { /* quota */ }
      return next;
    });
  };

  // Left-panel content toggle: 'chats' (sessions list, default) or
  // 'files' (workspace tree navigator). Persisted to localStorage so
  // the preference survives reloads. When 'files', clicking a file in
  // the tree updates `openFile` which the right column picks up to
  // render <FileViewer>.
  const [leftMode, setLeftMode] = useState(() => {
    try {
      const v = localStorage.getItem('apControl.leftMode');
      return v === 'files' ? 'files' : 'chats';
    } catch { return 'chats'; }
  });
  // openFile survives navigation so the user lands back on the same
  // doc when they return to /dashboard. Stored object shape:
  // { path: string, scope: 'tenant'|'platform' }.
  const [openFile, setOpenFile] = useState(() => {
    try {
      const raw = localStorage.getItem('apControl.openFile');
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed.path === 'string'
        && (parsed.scope === 'tenant' || parsed.scope === 'platform')) {
        return parsed;
      }
    } catch { /* corrupt */ }
    return null;
  });
  useEffect(() => {
    try {
      if (openFile) localStorage.setItem('apControl.openFile', JSON.stringify(openFile));
      else localStorage.removeItem('apControl.openFile');
    } catch { /* quota */ }
  }, [openFile]);
  const switchLeftMode = (next) => {
    if (next === leftMode) return;
    setLeftMode(next);
    try { localStorage.setItem('apControl.leftMode', next); } catch { /* quota */ }
  };

  // Onboarding redirect — keeps the same gate the legacy dashboard had.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status = await getOnboardingStatus();
        if (cancelled) return;
        if (!status?.onboarded && !status?.deferred) {
          navigate('/onboarding', { replace: true });
        }
      } catch (e) {
        // Soft-fail; same semantics as legacy dashboard.
        // eslint-disable-next-line no-console
        console.warn('onboarding-status probe failed:', e);
      }
    })();
    return () => { cancelled = true; };
  }, [navigate]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await chatService.listSessions();
        if (cancelled) return;
        const list = resp.data || [];
        setSessions(list);
        // Use the functional setter so a session the user *just*
        // created via handleNewSession isn't clobbered by list[0] if
        // the initial list-fetch resolves after createSession.
        if (list.length) {
          setActiveSession((cur) => cur ?? list[0]);
          // Also seed the default editor group's session so the first
          // ChatTab renders the same thread as `activeSession`. Only
          // seed groups that have no session yet — don't clobber a
          // user-driven pick that arrived before this list fetch.
          setEditorGroups((groups) =>
            groups.map((g) => (g.sessionId == null ? { ...g, sessionId: list[0].id } : g)),
          );
        }
      } catch {
        // Non-fatal; the dashboard still renders the widgets.
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Mirror the focused group's session into `activeSession` so the SSE
  // provider, command palette, and coalition modal all reference the
  // pane the user is currently working in. Without this, clicking a
  // split would leave activeSession pointing at the original pane.
  //
  // INTENTIONAL: this effect mirrors the focused-group session into
  // `activeSession`. The id-only equality guard
  // (`activeSession?.id === focused.sessionId`) is what prevents an
  // infinite re-render loop when `sessions` is re-fetched and arrives
  // with NEW object references but the same ids — a deep-equality
  // guard would still fire setActiveSession on every refetch because
  // the object identity changed, which would in turn re-run any child
  // effect keyed on activeSession. Keep the comparison by id.
  useEffect(() => {
    const focused = editorGroups.find((g) => g.id === focusedGroupId);
    if (!focused) return;
    if (focused.sessionId == null) return;
    if (activeSession?.id === focused.sessionId) return;
    const match = sessions.find((s) => s.id === focused.sessionId);
    if (match) setActiveSession(match);
  }, [focusedGroupId, editorGroups, sessions, activeSession]);

  // Agents feed the command palette. Fail-soft — palette still works
  // with sessions + static nav even if the agent list 403s.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await agentService.getAll();
        if (cancelled) return;
        setAgents(Array.isArray(resp.data) ? resp.data : resp.data?.agents || []);
      } catch { /* fail-soft */ }
    })();
    return () => { cancelled = true; };
  }, []);

  // ⌘K / Ctrl+K opens the command palette. Esc handled inside the
  // palette modal itself. Ignore the shortcut if the user is editing
  // inside an input/textarea/contenteditable that's not the palette.
  useEffect(() => {
    const onKey = (e) => {
      const isPaletteShortcut = (e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K');
      if (!isPaletteShortcut) return;
      e.preventDefault();
      setPaletteOpen((v) => !v);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  if (loading) {
    return (
      <Layout>
        <div className="text-center py-5">
          <Spinner animation="border" variant="primary" />
        </div>
      </Layout>
    );
  }

  // Keys live under `system.*` and `system.deployed/sourcesPipelines/rows/vectorStores`
  // in apps/web/src/i18n/locales/{en,es}/dashboard.json. Earlier draft used
  // `cards.*` which doesn't exist in that namespace.
  return (
    <Layout>
      <div className={`dcc-container dcc-mode-${mode}`}>
        <header className="ap-page-header">
          <div>
            <h1 className="ap-page-title">{t('title')}</h1>
            <p className="ap-page-subtitle">{t('subtitle')}</p>
          </div>
          <div className="ap-page-actions">
            <button
              type="button"
              className="dcc-palette-trigger"
              onClick={() => setPaletteOpen(true)}
              title="Search and jump (⌘K)"
              aria-label="Open command palette"
            >
              <span>Search</span>
              <kbd className="dcc-palette-kbd">⌘K</kbd>
            </button>
            <button
              type="button"
              className="dcc-mode-toggle"
              onClick={toggleMode}
              aria-pressed={mode === 'pro'}
              title={mode === 'simple' ? 'Switch to Pro mode (terminal + advanced)' : 'Switch to Simple mode'}
            >
              <span className={`dcc-mode-pill ${mode === 'simple' ? 'active' : ''}`}>Simple</span>
              <span className={`dcc-mode-pill ${mode === 'pro' ? 'active' : ''}`}>Pro</span>
            </button>
          </div>
        </header>

        <CommandPalette
          open={paletteOpen}
          onClose={() => setPaletteOpen(false)}
          sessions={sessions}
          agents={agents}
          onSelectSession={(s) => setActiveSession(s)}
        />

        <TriggerCoalitionModal
          open={coalitionOpen}
          onClose={() => setCoalitionOpen(false)}
          sessionId={activeSession?.id || null}
        />

        {error && (
          <Alert variant="warning" dismissible onClose={() => setError(null)} className="mb-3" style={{ fontSize: 'var(--ap-fs-sm)' }}>
            {error}
          </Alert>
        )}

        {/* Stat chips removed per user feedback — they were dead weight
            when the numbers were 0/0/0/0 and even when populated they
            didn't earn the prime real estate at the top of the dash.
            The same data is reachable from /agents, /integrations,
            /memory; the bottom Quick-tile row links there directly. */}

        {/* Merged chat surface: sessions list + active thread + live agent activity */}
        {/* SessionEventsProvider opens ONE SSE connection per active
            session and shares events/status across ChatTab's PlanStepper,
            AgentActivityPanel, and TerminalPanel/TerminalGroup. Previously
            each subscribed independently → 3-4 concurrent SSE connections
            per session (browser caps at 6 per origin). */}
        <SessionEventsProvider sessionId={activeSession?.id || null}>
        <div className="ap-section-label">{t('chat.title', 'Chat with Alpha')}</div>
        {/* The chat row is now a `<ResizableSplit>` instead of a
            Bootstrap Row+Col. Each pane fills 100% of the row height
            (the row itself still clamps via .dcc-chat-row in CSS). The
            inner `.ap-card` rule keeps stretching to 100% as before —
            ResizableSplit's `.rs-pane` is `height: 100%` itself, so
            the height chain is unbroken. */}
        {(() => {
          // Right pane is visible when Pro mode is on OR when the user
          // is in Files mode (the pane shows either the previewed file
          // or a "pick a file" placeholder). Keeping Files mode at a
          // stable 3-pane shape avoids remounting the outer
          // ResizableSplit on every file open/close, which would
          // collapse the FileTreePanel's lazy-loaded expand state.
          const hasRightPane = mode === 'pro' || leftMode === 'files';
          // ── Chat row body. In Pro mode this becomes the top pane of
          // an outer column-direction ResizableSplit so the user can
          // drag the divider between chat surface and terminal. In
          // Simple mode the terminal isn't mounted, so we render the
          // row standalone with its existing clamped height. ──
          const chatRow = (
        <div className="dcc-chat-row">
          <ResizableSplit
            key={`chat-row-${mode}-${hasRightPane ? 'r' : 'nr'}`}
            storageKey={`dcc.chatRow.sizes.${mode}-${hasRightPane ? 'r' : 'nr'}`}
            defaultSizes={hasRightPane ? [22, 56, 22] : [25, 75]}
            minSizes={hasRightPane ? [160, 320, 200] : [160, 320]}
          >
            {/* Pane 1 — sessions list */}
            <article className="ap-card h-100">
              <div className="ap-card-body dcc-sessions">
                {/* Chats / Files mode toggle — swaps the body of this
                    card without changing layout. localStorage-backed
                    so the preference survives reloads. */}
                <div className="dcc-left-mode-toggle" role="tablist" aria-label="Left panel mode">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={leftMode === 'chats'}
                    className={`dcc-mode-pill${leftMode === 'chats' ? ' active' : ''}`}
                    onClick={() => switchLeftMode('chats')}
                  >
                    Chats
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={leftMode === 'files'}
                    className={`dcc-mode-pill${leftMode === 'files' ? ' active' : ''}`}
                    onClick={() => switchLeftMode('files')}
                  >
                    Files
                  </button>
                </div>

                {leftMode === 'chats' ? (
                  <>
                    <div className="d-flex justify-content-between align-items-center mb-2">
                      <strong style={{ fontSize: 'var(--ap-fs-sm)' }}>{t('chat.sessions', 'Sessions')}</strong>
                      <div className="d-flex" style={{ gap: 4 }}>
                        <button
                          type="button"
                          className="ap-btn-secondary ap-btn-sm"
                          onClick={() => setCoalitionOpen(true)}
                          disabled={!activeSession}
                          title="Dispatch an A2A coalition (Propose / Critique / Revise, Plan / Verify, …)"
                        >
                          ⚡ A2A
                        </button>
                        <button
                          type="button"
                          className="ap-btn-primary ap-btn-sm"
                          onClick={handleNewSession}
                          disabled={creating}
                        >
                          + {creating ? t('chat.creating', 'Creating…') : t('chat.new', 'New')}
                        </button>
                      </div>
                    </div>
                    {sessions.length === 0 ? (
                      <p className="text-muted mb-0" style={{ fontSize: 'var(--ap-fs-sm)' }}>
                        {t('chat.empty', 'No conversations yet.')}
                      </p>
                    ) : (
                      <ul className="dcc-session-list">
                        {sessions.slice(0, 12).map((s) => (
                          <li key={s.id}>
                            <button
                              type="button"
                              className={`dcc-session-row${activeSession?.id === s.id ? ' active' : ''}`}
                              onClick={() => selectSessionForFocusedGroup(s)}
                            >
                              <span className="dcc-session-title" title={s.title}>
                                {s.title || t('chat.untitled', 'Untitled')}
                              </span>
                              <span className="dcc-session-meta">
                                {s.message_count != null ? `${s.message_count} msgs` : ''}
                              </span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </>
                ) : (
                  <FileTreePanel onSelect={setOpenFile} />
                )}
              </div>
            </article>

            {/* Pane 2 — chat thread(s). When the user splits, this pane
                hosts a nested `<ResizableSplit>` so each editor group
                becomes its own resizable column inside the centre. */}
            <ChatGroupsPane
              editorGroups={editorGroups}
              focusedGroupId={focusedGroupId}
              setFocusedGroupId={setFocusedGroupId}
              sessions={sessions}
              onSplitRight={handleSplitRight}
              onCloseSplit={handleCloseSplit}
              maxGroups={MAX_EDITOR_GROUPS}
              t={t}
              onNewSession={handleNewSession}
              creating={creating}
            />

            {/* Pane 3 — right column. In Files mode the pane always
                renders (FileViewer when a file is selected, empty-state
                placeholder otherwise) so the outer ResizableSplit stays
                mounted across file open/close transitions — that keeps
                FileTreePanel's lazy-loaded expand state intact. In
                Chats mode the pane renders the AgentActivityPanel only
                when Pro mode is on; null otherwise drops the pane via
                filter(Boolean) inside ResizableSplit. */}
            {leftMode === 'files' ? (
              <article className="ap-card h-100 dcc-activity-card">
                <div className="ap-card-body p-0 dcc-file-viewer-body">
                  {openFile ? (
                    <FileViewer file={openFile} />
                  ) : (
                    <div className="dcc-thread-empty">
                      <p>{t('files.pickPrompt', 'Pick a file from the tree to preview it here.')}</p>
                    </div>
                  )}
                </div>
              </article>
            ) : mode === 'pro' ? (
              <article className="ap-card h-100 dcc-activity-card">
                <div className="ap-card-body p-0">
                  <AgentActivityPanel collapsed={false} sessionId={activeSession?.id || null} />
                </div>
              </article>
            ) : null}
          </ResizableSplit>
        </div>
          );
          // ── Pro mode: wrap chat-row + TerminalCard in an outer
          // column-direction ResizableSplit so the divider between
          // the chat surface and terminal panel is draggable (Phase A
          // of the VSCode-style terminal redesign). minSizes:
          // [260, 140] — 260 px keeps the chat usable, 140 px floors
          // the terminal above zero-height drags. Simple mode skips
          // the terminal entirely and renders the row standalone. ──
          if (mode === 'pro') {
            return (
              <div className="dcc-outer-col">
                <ResizableSplit
                  direction="column"
                  storageKey="dcc.outerCol.sizes"
                  defaultSizes={[60, 40]}
                  minSizes={[260, 140]}
                >
                  {chatRow}
                  <TerminalPanel sessionId={activeSession?.id || null} />
                </ResizableSplit>
              </div>
            );
          }
          return chatRow;
        })()}
        </SessionEventsProvider>
      </div>
    </Layout>
  );
};

export default DashboardControlCenter;
