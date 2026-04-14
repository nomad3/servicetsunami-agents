import React, { useState, useCallback, useEffect } from 'react';
import { AuthProvider, useAuth } from './context/AuthContext';
import ChatInterface from './components/ChatInterface';
import LoginForm from './components/LoginForm';
import NotificationBell from './components/NotificationBell';
import TrustBadge from './components/TrustBadge';
import ActionApproval from './components/ActionApproval';
import CommandPalette from './components/CommandPalette';
import ClipboardToast from './components/ClipboardToast';
import WorkflowSuggestions from './components/WorkflowSuggestions';
import SpatialHUD from './components/spatial/SpatialHUD';
import { useShellPresence } from './hooks/useShellPresence';
import { useSessionEvents } from './hooks/useSessionEvents';
import { useTrustProfile } from './hooks/useTrustProfile';
import { useActivityTracker } from './hooks/useActivityTracker';
import { apiJson } from './api';
import './App.css';

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
    // Open the GitHub Release page to download the latest DMG
    // (full auto-install requires Apple Developer code signing)
    window.open('https://github.com/nomad3/servicetsunami-agents/releases/latest', '_blank');
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
    </div>
  );
}

function AppContent() {
  const { user, loading } = useAuth();
  const [windowLabel, setWindowLabel] = useState(null);

  useEffect(() => {
    console.log('[Luna OS] Starting window detection...');
    
    // Safety fallback: default to 'main' after 1 second if detection is stuck
    const timer = setTimeout(() => {
      if (windowLabel === null) {
        console.warn('[Luna OS] Window detection timed out -> main');
        setWindowLabel('main');
      }
    }, 1000);

    const detectWindow = async () => {
      try {
        // Use a more generic way to detect window label if the specific import fails
        const tauriWebview = await import('@tauri-apps/api/webviewWindow').catch(() => null);
        
        if (tauriWebview && tauriWebview.getCurrentWebviewWindow) {
          const appWindow = tauriWebview.getCurrentWebviewWindow();
          console.log('[Luna OS] Detected window:', appWindow.label);
          setWindowLabel(appWindow.label || 'main');
        } else {
          console.log('[Luna OS] Tauri internals not found -> main');
          setWindowLabel('main');
        }
      } catch (e) {
        console.error('[Luna OS] Detection error:', e);
        setWindowLabel('main');
      } finally {
        clearTimeout(timer);
      }
    };

    detectWindow();
    return () => clearTimeout(timer);
  }, []);

  // While detecting, show a minimal loader. 
  // If this stays frozen, it's a React render crash.
  if (windowLabel === null) {
    return (
      <div className="luna-loading" style={{ background: '#000', color: '#64b4ff' }}>
        Initializing Luna OS...
      </div>
    );
  }

  if (windowLabel === 'spatial_hud') {
    return <SpatialHUD />;
  }

  if (loading) return <div className="luna-loading">Loading...</div>;
  if (!user) return <LoginForm />;

  return <AuthenticatedApp />;
}

export default function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  );
}
