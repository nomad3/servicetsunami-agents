import React, { useState, useCallback, useEffect } from 'react';
import { AuthProvider, useAuth } from './context/AuthContext';
import { GestureProvider } from './context/GestureContext';
import ChatInterface from './components/ChatInterface';
import LoginForm from './components/LoginForm';
import NotificationBell from './components/NotificationBell';
import TrustBadge from './components/TrustBadge';
import ActionApproval from './components/ActionApproval';
import CommandPalette from './components/CommandPalette';
import ClipboardToast from './components/ClipboardToast';
import WorkflowSuggestions from './components/WorkflowSuggestions';
import SpatialHUD from './components/spatial/SpatialHUD';
import GestureOverlay from './components/gestures/GestureOverlay';
import GestureBindingsPage from './components/gestures/GestureBindingsPage';
import GestureCalibration from './components/gestures/GestureCalibration';
import LunaCursor from './components/luna/LunaCursor';
import { useShellPresence } from './hooks/useShellPresence';
import { useSessionEvents } from './hooks/useSessionEvents';
import { useTrustProfile } from './hooks/useTrustProfile';
import { useActivityTracker } from './hooks/useActivityTracker';
import { apiJson } from './api';
import './App.css';

function dispatchGestureAction(binding, event) {
  // Best-effort audit + RL log to the API. Fire-and-forget; never blocks UI.
  import('./api').then(({ postGestureDispatch }) => {
    postGestureDispatch({
      binding_id: binding.id,
      gesture: binding.gesture,
      action_kind: binding.action.kind,
      screen: window.location.hash || window.location.pathname,
      frontmost_app: 'Luna',
      latency_ms: typeof event?.ts === 'number' ? Date.now() - event.ts : null,
      confidence: typeof event?.confidence === 'number' ? event.confidence : null,
    });
  }).catch(() => {});

  switch (binding.action.kind) {
    case 'nav_hud':
      window.dispatchEvent(new Event('luna-toggle-hud'));
      break;
    case 'nav_chat':
      window.dispatchEvent(new Event('luna-focus-chat'));
      break;
    case 'nav_command_palette':
      window.dispatchEvent(new Event('toggle-palette'));
      break;
    case 'nav_bindings':
      window.location.hash = '#/settings/gestures';
      break;
    case 'agent_next':
      window.dispatchEvent(new Event('luna-agent-next'));
      break;
    case 'agent_prev':
      window.dispatchEvent(new Event('luna-agent-prev'));
      break;
    case 'dismiss':
      window.dispatchEvent(new Event('luna-dismiss'));
      break;
    case 'memory_record':
      window.dispatchEvent(new CustomEvent('luna-memory-record', { detail: binding }));
      break;
    case 'scroll_up':
      window.scrollBy({ top: -120, behavior: 'smooth' });
      break;
    case 'scroll_down':
      window.scrollBy({ top: 120, behavior: 'smooth' });
      break;
    default:
      // Unknown / Phase 3 actions handled elsewhere.
      break;
  }
}

function useTheme() {
  const [theme, setTheme] = useState(() => localStorage.getItem('luna_theme') || 'dark');
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('luna_theme', theme);
  }, [theme]);
  const toggle = useCallback(() => setTheme(t => t === 'dark' ? 'light' : 'dark'), []);
  return { theme, toggle };
}

function useUpdateBanner() {
  const [updateVersion, setUpdateVersion] = useState(null);
  useEffect(() => {
    let unlisten;
    (async () => {
      try {
        const { listen } = await import('@tauri-apps/api/event');
        unlisten = await listen('update-available', (event) => {
          setUpdateVersion(event.payload);
        });
      } catch {} // Not in Tauri (PWA mode)
    })();
    return () => { unlisten?.(); };
  }, []);
  const dismiss = useCallback(() => setUpdateVersion(null), []);
  const restart = useCallback(async () => {
    let signingOk = false;
    try {
      const tauri = await import('@tauri-apps/api/core');
      signingOk = await tauri.invoke('updater_signing_status');
      if (signingOk) {
        // install_update downloads, verifies, applies, restarts. Only call
        // this when the bundle was built with a non-empty updater pubkey;
        // otherwise tauri-plugin-updater fails at verify_signature *after*
        // downloading the full DMG, which is wasteful and confusing.
        await tauri.invoke('install_update');
        return;
      }
    } catch (e) {
      console.warn('[Luna] install_update failed; opening releases page', e);
    }
    // Fallback: signing not configured, or install_update threw.
    window.open(
      'https://github.com/nomad3/servicetsunami-agents/releases/latest',
      '_blank',
    );
  }, []);
  return { updateVersion, dismiss, restart };
}

function AuthenticatedApp() {
  const { logout } = useAuth();
  const { handoff } = useShellPresence();
  const { trust, needsConfirmation } = useTrustProfile();
  const { theme, toggle: toggleTheme } = useTheme();
  const { updateVersion, dismiss: dismissUpdate, restart: restartForUpdate } = useUpdateBanner();
  const [pendingAction, setPendingAction] = useState(null);
  const pendingResolve = React.useRef(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [activeSessionId, setActiveSessionId] = useState(null);

  useActivityTracker();
  useSessionEvents(activeSessionId);

  // Listen for session changes from ChatInterface
  useEffect(() => {
    const handleSessionChange = (e) => setActiveSessionId(e.detail);
    window.addEventListener('luna-session-change', handleSessionChange);
    return () => window.removeEventListener('luna-session-change', handleSessionChange);
  }, []);

  // Listen for toggle-palette event from Tauri global shortcut
  useEffect(() => {
    let unlisten;
    (async () => {
      try {
        const { listen } = await import('@tauri-apps/api/event');
        unlisten = await listen('toggle-palette', () => {
          setPaletteOpen(prev => !prev);
        });
      } catch {}
    })();
    return () => { unlisten?.(); };
  }, []);

  const quickSessionRef = React.useRef(null);
  const handlePaletteSend = useCallback(async (text) => {
    try {
      if (!quickSessionRef.current) {
        const sessions = await apiJson('/api/v1/chat/sessions');
        const existing = sessions.find(s => s.title === 'Luna Quick');
        if (existing) {
          quickSessionRef.current = existing.id;
        } else {
          const created = await apiJson('/api/v1/chat/sessions', {
            method: 'POST',
            body: JSON.stringify({ title: 'Luna Quick' }),
          });
          quickSessionRef.current = created.id;
        }
      }
      apiJson(`/api/v1/chat/sessions/${quickSessionRef.current}/messages`, {
        method: 'POST',
        body: JSON.stringify({ content: text }),
      }).catch(() => {});
    } catch {}
  }, []);

  const requestAction = useCallback(async (action) => {
    if (!needsConfirmation) return true;
    return new Promise((resolve) => {
      pendingResolve.current = resolve;
      setPendingAction(action);
    });
  }, [needsConfirmation]);

  const handleApprove = useCallback(() => {
    pendingResolve.current?.(true);
    setPendingAction(null);
  }, []);

  const handleDeny = useCallback(() => {
    pendingResolve.current?.(false);
    setPendingAction(null);
  }, []);

  return (
    <div className="luna-app">
      <nav className="luna-nav">
        <span className="luna-brand">Luna</span>
        <div className="nav-actions">
          <button className="theme-toggle" onClick={toggleTheme} title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}>
            {theme === 'dark' ? '\u2600' : '\u263E'}
          </button>
          <TrustBadge trust={trust} />
          <button className="theme-toggle" onClick={() => setSuggestionsOpen(!suggestionsOpen)} title="Workflow suggestions">
            {'\u26A1'}
          </button>
          <NotificationBell />
          <button className="luna-btn luna-btn-sm" onClick={logout}>Logout</button>
        </div>
      </nav>
      {updateVersion && (
        <div className="update-banner">
          <span>Luna {updateVersion} is available</span>
          <button className="luna-btn luna-btn-sm" onClick={restartForUpdate}>Download update</button>
          <button className="update-dismiss" onClick={dismissUpdate}>&times;</button>
        </div>
      )}
      <ChatInterface handoff={handoff} requestAction={requestAction} />
      <ActionApproval
        action={pendingAction}
        onApprove={handleApprove}
        onDeny={handleDeny}
        onDismiss={handleDeny}
      />
      <CommandPalette
        visible={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        onSend={handlePaletteSend}
      />
      <ClipboardToast />
      <WorkflowSuggestions visible={suggestionsOpen} onClose={() => setSuggestionsOpen(false)} />
      <GestureOverlay />
      <LunaCursor />
    </div>
  );
}

// Children of GestureProvider so the SpatialHUD webview never registers
// onAction (preventing double-fire of every binding).
function GestureScope({ children, windowLabel }) {
  if (windowLabel === 'spatial_hud') {
    // HUD reads via useGesture() but doesn't dispatch actions.
    return <GestureProvider onAction={undefined}>{children}</GestureProvider>;
  }
  return <GestureProvider onAction={dispatchGestureAction}>{children}</GestureProvider>;
}

function useHashRoute() {
  const [hash, setHash] = useState(() => window.location.hash || '');
  useEffect(() => {
    const handler = () => setHash(window.location.hash || '');
    window.addEventListener('hashchange', handler);
    return () => window.removeEventListener('hashchange', handler);
  }, []);
  return hash;
}

function AppContent({ windowLabel }) {
  const { user, loading } = useAuth();
  const hash = useHashRoute();
  const [showCalibration, setShowCalibration] = useState(() => {
    try { return !localStorage.getItem('gesture_calibrated'); } catch { return false; }
  });

  // Phase 4: gate engine boot on login so we don't burn camera + Apple Vision
  // cycles on the login screen. Stops engine on logout.
  useEffect(() => {
    if (windowLabel === 'spatial_hud') return;
    let cancelled = false;
    (async () => {
      const tauri = await import('@tauri-apps/api/core').catch(() => null);
      if (!tauri || cancelled) return;
      if (user) {
        try { await tauri.invoke('gesture_start'); } catch {}
      } else {
        try { await tauri.invoke('gesture_stop'); } catch {}
      }
    })();
    return () => { cancelled = true; };
  }, [user, windowLabel]);

  if (windowLabel === 'spatial_hud') {
    return <SpatialHUD />;
  }
  if (loading) return <div className="luna-loading">Loading...</div>;
  if (!user) return <LoginForm />;

  return (
    <>
      {hash.startsWith('#/settings/gestures') ? <GestureBindingsPage /> : <AuthenticatedApp />}
      {showCalibration && (
        <GestureCalibration onDone={() => setShowCalibration(false)} />
      )}
    </>
  );
}

function RootShell() {
  // Owns windowLabel detection so GestureProvider can decide whether to
  // dispatch actions (HUD must not, or each binding fires twice — see
  // Phase 1 review issue #5).
  const [windowLabel, setWindowLabel] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      if (!cancelled) setWindowLabel((cur) => cur ?? 'main');
    }, 1000);
    (async () => {
      try {
        const tauriWebview = await import('@tauri-apps/api/webviewWindow').catch(() => null);
        if (tauriWebview && tauriWebview.getCurrentWebviewWindow) {
          const w = tauriWebview.getCurrentWebviewWindow();
          if (!cancelled) setWindowLabel(w.label || 'main');
        } else if (!cancelled) {
          setWindowLabel('main');
        }
      } catch {
        if (!cancelled) setWindowLabel('main');
      } finally {
        clearTimeout(timer);
      }
    })();
    return () => { cancelled = true; clearTimeout(timer); };
  }, []);

  if (windowLabel === null) {
    return <div className="luna-loading" style={{ background: '#000', color: '#64b4ff' }}>Initializing Luna OS...</div>;
  }

  return (
    <GestureScope windowLabel={windowLabel}>
      <AppContent windowLabel={windowLabel} />
    </GestureScope>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <RootShell />
    </AuthProvider>
  );
}
