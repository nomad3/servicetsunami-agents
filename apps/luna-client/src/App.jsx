import React, { useState, useCallback, useEffect } from 'react';
import { AuthProvider, useAuth } from './context/AuthContext';
import ChatInterface from './components/ChatInterface';
import LoginForm from './components/LoginForm';
import NotificationBell from './components/NotificationBell';
import TrustBadge from './components/TrustBadge';
import ActionApproval from './components/ActionApproval';
import { useShellPresence } from './hooks/useShellPresence';
import { useTrustProfile } from './hooks/useTrustProfile';
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
    try {
      const { relaunch } = await import('@tauri-apps/plugin-updater');
      await relaunch();
    } catch {
      // Fallback: just reload
      window.location.reload();
    }
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
          <NotificationBell />
          <button className="luna-btn luna-btn-sm" onClick={logout}>Logout</button>
        </div>
      </nav>
      {updateVersion && (
        <div className="update-banner">
          <span>Luna {updateVersion} is available</span>
          <button className="luna-btn luna-btn-sm" onClick={restartForUpdate}>Restart to update</button>
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
    </div>
  );
}

function AppContent() {
  const { user, loading } = useAuth();

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
