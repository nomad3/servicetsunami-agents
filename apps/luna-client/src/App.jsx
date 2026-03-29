import React from 'react';
import { AuthProvider, useAuth } from './context/AuthContext';
import ChatInterface from './components/ChatInterface';
import LoginForm from './components/LoginForm';
import NotificationBell from './components/NotificationBell';
import { useShellPresence } from './hooks/useShellPresence';
import './App.css';

function AuthenticatedApp() {
  const { logout } = useAuth();
  useShellPresence();

  return (
    <div className="luna-app">
      <nav className="luna-nav">
        <span className="luna-brand">Luna</span>
        <div className="nav-actions">
          <NotificationBell />
          <button className="luna-btn luna-btn-sm" onClick={logout}>Logout</button>
        </div>
      </nav>
      <ChatInterface />
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
