import { createContext, useContext, useState } from 'react';
import { Navigate, Route, BrowserRouter as Router, Routes, useNavigate } from 'react-router-dom';
import { ToastProvider } from './components/common';
import ProtectedRoute from './components/ProtectedRoute';
import { LunaPresenceProvider } from './context/LunaPresenceContext';
import { ThemeProvider } from './context/ThemeContext';
import LandingPage from './LandingPage';
// Agent Kits removed - using ADK for agent configuration
import AgentDetailPage from './pages/AgentDetailPage';
import AgentsPage from './pages/AgentsPage';
import FleetHealthPage from './pages/FleetHealthPage';
import AgentWizardPage from './pages/AgentWizardPage';
import BrandingPage from './pages/BrandingPage';
import ChatPage from './pages/ChatPage';
import DashboardPage from './pages/DashboardPage';
// DatasetsPage and DataSourcesPage merged into IntegrationsPage
import DeploymentsPage from './pages/DeploymentsPage';
import IntegrationsPage from './pages/IntegrationsPage';
import LoginPage from './pages/LoginPage';
import MemoryPage from './pages/MemoryPage';
import NotebooksPage from './pages/NotebooksPage';
import RegisterPage from './pages/RegisterPage';
import ResetPasswordPage from './pages/ResetPasswordPage';
import SettingsPage from './pages/SettingsPage';
import TeamsPage from './pages/TeamsPage';
import TenantsPage from './pages/TenantsPage';
import ToolsPage from './pages/ToolsPage';
import VectorStoresPage from './pages/VectorStoresPage';
import SkillsPage from './pages/SkillsPage';
import WorkflowsPage from './pages/WorkflowsPage';
import WorkflowBuilder from './components/workflows/WorkflowBuilder';
import LearningPage from './pages/LearningPage';
import api from './services/api';
import authService from './services/auth';

// Create an Auth Context
const AuthContext = createContext(null);

// Auth Provider component
const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(authService.getCurrentUser());
  const navigate = useNavigate();
  // In-flight guard for refreshUser — coalesces concurrent callers
  // (rapid SettingsPage save clicks, websocket-triggered refreshes,
  // etc.) onto a single /users/me call. Without this, thundering-herd
  // refetches on auth event spam waste API quota and can race the
  // localStorage write. M3 from the 2026-05-02 holistic review.
  // Stored on a ref-style holder rather than React state so reads in
  // the closure see the latest in-flight Promise without a re-render.
  const refreshInFlight = { current: null };

  const login = async (email, password) => {
    const userData = await authService.login(email, password);
    setUser(userData);
    return userData;
  };

  const logout = () => {
    authService.logout();
    setUser(null);
    navigate('/login');
  };

  // Re-fetch the current user from the API and update the cached
  // localStorage copy. Pages that mutate self-editable fields (like
  // SettingsPage's full_name save) call this so the in-memory user
  // object reflects the change without a full page reload.
  const refreshUser = async () => {
    if (refreshInFlight.current) return refreshInFlight.current;
    refreshInFlight.current = (async () => {
      try {
        const resp = await api.get('/users/me');
        const fresh = resp.data;
        // Preserve the access_token that login persisted alongside the user.
        const existing = authService.getCurrentUser() || {};
        const merged = { ...existing, ...fresh };
        localStorage.setItem('user', JSON.stringify(merged));
        setUser(merged);
        return merged;
      } catch {
        return null;
      } finally {
        refreshInFlight.current = null;
      }
    })();
    return refreshInFlight.current;
  };

  const value = { user, login, logout, refreshUser };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

// Custom hook to use auth context
export const useAuth = () => {
  return useContext(AuthContext);
};

function App() {
  return (
    <ThemeProvider>
      <Router>
        <AuthProvider>
          <LunaPresenceProvider>
            <ToastProvider>
              <Routes>
                <Route path="/" element={<LandingPage />} />
                <Route path="/login" element={<LoginPage />} />
                <Route path="/auth/login" element={<LoginPage />} />
                <Route path="/register" element={<RegisterPage />} />
                <Route path="/reset-password" element={<ResetPasswordPage />} />
                <Route path="/auth/reset-password" element={<ResetPasswordPage />} />
                <Route path="/home" element={<Navigate to="/dashboard" replace />} />
                <Route path="/dashboard" element={<ProtectedRoute><DashboardPage /></ProtectedRoute>} />
                <Route path="/data-sources" element={<Navigate to="/integrations?tab=data-sources" replace />} />
                <Route path="/integrations" element={<ProtectedRoute><IntegrationsPage /></ProtectedRoute>} />
                <Route path="/notebooks" element={<ProtectedRoute><NotebooksPage /></ProtectedRoute>} />
                <Route path="/agents" element={<ProtectedRoute><AgentsPage /></ProtectedRoute>} />
                <Route path="/insights/fleet-health" element={<ProtectedRoute><FleetHealthPage /></ProtectedRoute>} />
                <Route path="/agents/wizard" element={<ProtectedRoute><AgentWizardPage /></ProtectedRoute>} />
                <Route path="/agents/:id" element={<ProtectedRoute><AgentDetailPage /></ProtectedRoute>} />
                <Route path="/datasets" element={<Navigate to="/integrations?tab=datasets" replace />} />
                <Route path="/chat" element={<ProtectedRoute><ChatPage /></ProtectedRoute>} />
                <Route path="/tools" element={<ProtectedRoute><ToolsPage /></ProtectedRoute>} />
                <Route path="/deployments" element={<ProtectedRoute><DeploymentsPage /></ProtectedRoute>} />
                <Route path="/vector-stores" element={<ProtectedRoute><VectorStoresPage /></ProtectedRoute>} />
                {/* Agent Kits route removed - using ADK for agent configuration */}
                <Route path="/tenants" element={<ProtectedRoute><TenantsPage /></ProtectedRoute>} />
                <Route path="/teams" element={<ProtectedRoute><TeamsPage /></ProtectedRoute>} />
                <Route path="/memory" element={<ProtectedRoute><MemoryPage /></ProtectedRoute>} />
                <Route path="/skills" element={<ProtectedRoute><SkillsPage /></ProtectedRoute>} />
                <Route path="/learning" element={<ProtectedRoute><LearningPage /></ProtectedRoute>} />
                <Route path="/workflows" element={<ProtectedRoute><WorkflowsPage /></ProtectedRoute>} />
                <Route path="/workflows/builder" element={<ProtectedRoute><WorkflowBuilder /></ProtectedRoute>} />
                <Route path="/workflows/builder/:id" element={<ProtectedRoute><WorkflowBuilder /></ProtectedRoute>} />
                <Route path="/task-console" element={<Navigate to="/workflows?tab=executions" replace />} />
                <Route path="/settings" element={<ProtectedRoute><SettingsPage /></ProtectedRoute>} />
                <Route path="/settings/llm" element={<Navigate to="/integrations?tab=ai-models" replace />} />
                <Route path="/settings/branding" element={<ProtectedRoute><BrandingPage /></ProtectedRoute>} />
                <Route path="/branding" element={<ProtectedRoute><BrandingPage /></ProtectedRoute>} />
              </Routes>
            </ToastProvider>
          </LunaPresenceProvider>
        </AuthProvider>
      </Router>
    </ThemeProvider>
  );
}

export default App;
