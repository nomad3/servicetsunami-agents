import React, { createContext, lazy, Suspense, useContext, useEffect, useState } from 'react';
import { Navigate, Route, BrowserRouter as Router, Routes, useNavigate } from 'react-router-dom';
import { LoadingSpinner, ToastProvider } from './components/common';
import ProtectedRoute from './components/ProtectedRoute';
import { LunaPresenceProvider } from './context/LunaPresenceContext';
import { ThemeProvider } from './context/ThemeContext';
import { initMarketingAnalytics } from './services/marketingAnalytics';
import api from './services/api';
import authService from './services/auth';

// ── Critical-path routes (kept eager) ──
// LandingPage + AlphaLandingPage render at "/" before auth and dictate
// LCP for cold marketing-site visitors; LoginPage is the next click on
// the auth funnel. Both ship in the initial bundle so unauth visitors
// don't pay a Suspense round-trip on first paint.
import LandingPage from './LandingPage';
import AlphaLandingPage from './AlphaLandingPage';
import LoginPage from './pages/LoginPage';

// ── Hot routes (lazy + prefetched) ──
// Dashboard is the post-login default and gets webpackPrefetch hinted
// from a useEffect below so its chunk arrives at idle priority before
// the user logs in. Edge cache + prefetch + lazy = "instant" post-login
// without sacrificing cold-marketing-visit FCP.
const DashboardControlCenter = lazy(() => import(/* webpackChunkName: "dashboard" */ './pages/DashboardControlCenter'));

// ── Lazy-loaded route pages ──
// Each becomes its own webpack chunk loaded on navigation. The
// webpackChunkName magic comment gives each chunk a stable filename so
// the edge cache (PR #565) can serve them with `immutable` headers
// across releases that don't touch the page.
// Agent Kits removed - using ADK for agent configuration
const AgentDetailPage = lazy(() => import(/* webpackChunkName: "agent-detail" */ './pages/AgentDetailPage'));
const AgentsPage = lazy(() => import(/* webpackChunkName: "agents" */ './pages/AgentsPage'));
const FleetHealthPage = lazy(() => import(/* webpackChunkName: "fleet-health" */ './pages/FleetHealthPage'));
const CostInsightsPage = lazy(() => import(/* webpackChunkName: "cost-insights" */ './pages/CostInsightsPage'));
const CoalitionReplayPage = lazy(() => import(/* webpackChunkName: "coalition-replay" */ './pages/CoalitionReplayPage'));
const TenantHealthPage = lazy(() => import(/* webpackChunkName: "tenant-health" */ './pages/TenantHealthPage'));
const AgentWizardPage = lazy(() => import(/* webpackChunkName: "agent-wizard" */ './pages/AgentWizardPage'));
const BrandingPage = lazy(() => import(/* webpackChunkName: "branding" */ './pages/BrandingPage'));
const ChatPage = lazy(() => import(/* webpackChunkName: "chat" */ './pages/ChatPage'));
const DashboardLegacyPage = lazy(() => import(/* webpackChunkName: "dashboard-legacy" */ './pages/DashboardLegacyPage'));
const DeviceLoginPage = lazy(() => import(/* webpackChunkName: "device-login" */ './pages/DeviceLoginPage'));
// DatasetsPage and DataSourcesPage merged into IntegrationsPage
const DeploymentsPage = lazy(() => import(/* webpackChunkName: "deployments" */ './pages/DeploymentsPage'));
const IntegrationsPage = lazy(() => import(/* webpackChunkName: "integrations" */ './pages/IntegrationsPage'));
const MemoryPage = lazy(() => import(/* webpackChunkName: "memory" */ './pages/MemoryPage'));
const NotebooksPage = lazy(() => import(/* webpackChunkName: "notebooks" */ './pages/NotebooksPage'));
const OnboardingPage = lazy(() => import(/* webpackChunkName: "onboarding" */ './pages/OnboardingPage'));
const RegisterPage = lazy(() => import(/* webpackChunkName: "register" */ './pages/RegisterPage'));
const ResetPasswordPage = lazy(() => import(/* webpackChunkName: "reset-password" */ './pages/ResetPasswordPage'));
const SettingsPage = lazy(() => import(/* webpackChunkName: "settings" */ './pages/SettingsPage'));
const TeamsPage = lazy(() => import(/* webpackChunkName: "teams" */ './pages/TeamsPage'));
const TenantsPage = lazy(() => import(/* webpackChunkName: "tenants" */ './pages/TenantsPage'));
const ToolsPage = lazy(() => import(/* webpackChunkName: "tools" */ './pages/ToolsPage'));
const VectorStoresPage = lazy(() => import(/* webpackChunkName: "vector-stores" */ './pages/VectorStoresPage'));
const SkillsPage = lazy(() => import(/* webpackChunkName: "skills" */ './pages/SkillsPage'));
const WorkflowsPage = lazy(() => import(/* webpackChunkName: "workflows" */ './pages/WorkflowsPage'));
const WorkflowBuilder = lazy(() => import(/* webpackChunkName: "workflow-builder" */ './components/workflows/WorkflowBuilder'));
const LearningPage = lazy(() => import(/* webpackChunkName: "learning" */ './pages/LearningPage'));

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

  // Re-fetch the current user from the API and update the cached
  // localStorage copy. Called by login (so is_superuser / email /
  // tenant land immediately on the user blob — the /auth/login response
  // alone only carries access_token) and by pages that mutate
  // self-editable fields (SettingsPage save).
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

  const login = async (email, password) => {
    const userData = await authService.login(email, password);
    setUser(userData);
    // Hydrate is_superuser / email / tenant onto the user blob so
    // sidebar navigation that gates on those fields renders correctly
    // on the first authenticated page after login. Errors are
    // swallowed inside refreshUser, so this never breaks login itself.
    refreshUser();
    return userData;
  };

  const logout = () => {
    authService.logout();
    setUser(null);
    navigate('/login');
  };

  // Existing-session hydration: if the user reloads the page (or
  // returns from a browser restart) their localStorage blob may have
  // just the access_token from an older login that didn't merge
  // /users/me. Detect that — `email` is the cheapest sentinel since
  // it always lands when /users/me succeeds — and refresh once.
  useEffect(() => {
    if (user?.access_token && !user?.email) {
      refreshUser();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value = { user, login, logout, refreshUser };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

// Custom hook to use auth context
export const useAuth = () => {
  return useContext(AuthContext);
};

// ── Chunk-load error boundary ──
// When a deploy ships, the on-the-wire `index.html` points at fresh
// chunk filenames (content-hashed). Any user with a stale tab open
// will attempt to lazy-load a chunk that no longer exists at the CDN
// → React surfaces an opaque white-screen. This boundary catches
// ChunkLoadError, logs it once, and force-reloads the page so the
// browser fetches the new index.html. Anything else rethrows so we
// don't swallow real bugs.
class ChunkLoadErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { reloading: false };
  }
  static getDerivedStateFromError(error) {
    const isChunkError =
      error?.name === 'ChunkLoadError' ||
      /Loading chunk \S+ failed/i.test(error?.message || '') ||
      /Loading CSS chunk \S+ failed/i.test(error?.message || '');
    if (isChunkError) return { reloading: true };
    throw error;
  }
  componentDidCatch(error) {
    if (this.state.reloading) {
      // eslint-disable-next-line no-console
      console.warn('[chunk-reload] stale bundle detected, reloading', error);
      // Defer one tick so React can paint the fallback before navigation.
      setTimeout(() => window.location.reload(), 0);
    }
  }
  render() {
    if (this.state.reloading) {
      return <LoadingSpinner fullScreen text="Updating…" />;
    }
    return this.props.children;
  }
}

function App() {
  useEffect(() => { initMarketingAnalytics(); }, []);

  // ── Hot-route prefetch (authenticated visitors only) ──
  // Once the eager bundle has painted, hint the browser to start
  // downloading the dashboard chunk. webpackPrefetch=true emits a
  // <link rel="prefetch"> which the browser fetches at idle priority —
  // zero impact on FCP but turns the login → dashboard hop into a
  // warm-cache load. The duplicate webpackChunkName is intentional and
  // matches the lazy() declaration above so webpack reuses the same
  // chunk file instead of emitting a second copy. Gated on auth so
  // unauthenticated marketing-site visitors don't pay the bandwidth.
  useEffect(() => {
    if (!authService.isAuthenticated?.()) return;
    // eslint-disable-next-line no-unused-expressions
    import(/* webpackPrefetch: true, webpackChunkName: "dashboard" */ './pages/DashboardControlCenter');
  }, []);

  return (
    <ThemeProvider>
      <Router>
        <AuthProvider>
          <LunaPresenceProvider>
            <ToastProvider>
              {/* ChunkLoadErrorBoundary catches stale-chunk errors
                  from rolling deploys and force-reloads. Suspense
                  fallback below covers the brief window between
                  navigation and the lazy chunk's network fetch. Most
                  lazy chunks are <50 KB gzipped so the spinner is
                  usually a single frame on a warm connection. */}
              <ChunkLoadErrorBoundary>
              <Suspense fallback={<LoadingSpinner fullScreen text="Loading…" />}>
              <Routes>
                {/* Root: alpha.agentprovision.com renders the CLI
                    landing; agentprovision.com renders the main one.
                    Hostname-sniff so the same SPA bundle handles both
                    apex domains without a separate build. */}
                <Route
                  path="/"
                  element={
                    typeof window !== 'undefined' &&
                    window.location.hostname.startsWith('alpha.')
                      ? <AlphaLandingPage />
                      : <LandingPage />
                  }
                />
                {/* /alpha is also reachable directly (e.g. for staging
                    or share-links). Idempotent with the hostname-
                    sniffed root above. */}
                <Route path="/alpha" element={<AlphaLandingPage />} />
                <Route path="/login" element={<LoginPage />} />
                <Route path="/auth/login" element={<LoginPage />} />
                {/* Device-auth landing for `alpha login` (task #201). Wrapped in
                    ProtectedRoute because the approve endpoint needs
                    current_user; unauthenticated visitors get the standard
                    redirect-to-login flow that returns them here after sign-in. */}
                <Route path="/login/device" element={<ProtectedRoute><DeviceLoginPage /></ProtectedRoute>} />
                <Route path="/register" element={<RegisterPage />} />
                <Route path="/reset-password" element={<ResetPasswordPage />} />
                <Route path="/auth/reset-password" element={<ResetPasswordPage />} />
                <Route path="/home" element={<Navigate to="/dashboard" replace />} />
                <Route path="/dashboard" element={<ProtectedRoute><DashboardControlCenter /></ProtectedRoute>} />
                {/* Legacy widget dashboard kept reachable for one
                    release while users adopt the new IDE shell.
                    Plan to remove after Phase 3 of the Alpha Control
                    Center rollout (see docs/plans/2026-05-15-alpha-
                    control-center-ide-shell-design.md). */}
                <Route path="/dashboard/legacy" element={<ProtectedRoute><DashboardLegacyPage /></ProtectedRoute>} />
                {/* /den sunset — its capabilities (event stream, tier
                    gating, terminal drawer) are being merged into
                    Dashboard + AI Chat. Redirect preserves any
                    bookmarks shared during the brief Tier 0–1 rollout. */}
                <Route path="/den" element={<Navigate to="/dashboard" replace />} />
                {/* PR-Q6: guided initial-training wizard. Mirrors the
                    CLI `alpha quickstart` flow (apps/agentprovision-cli/
                    src/commands/quickstart.rs) as React screens.
                    Linked from the dashboard route guard which
                    auto-redirects un-onboarded tenants on first
                    mount. See apps/web/src/pages/OnboardingPage.js. */}
                <Route path="/onboarding" element={<ProtectedRoute><OnboardingPage /></ProtectedRoute>} />
                <Route path="/data-sources" element={<Navigate to="/integrations?tab=data-sources" replace />} />
                <Route path="/integrations" element={<ProtectedRoute><IntegrationsPage /></ProtectedRoute>} />
                <Route path="/notebooks" element={<ProtectedRoute><NotebooksPage /></ProtectedRoute>} />
                <Route path="/agents" element={<ProtectedRoute><AgentsPage /></ProtectedRoute>} />
                <Route path="/insights/fleet-health" element={<ProtectedRoute><FleetHealthPage /></ProtectedRoute>} />
                <Route path="/insights/cost" element={<ProtectedRoute><CostInsightsPage /></ProtectedRoute>} />
                <Route path="/insights/collaborations" element={<ProtectedRoute><CoalitionReplayPage /></ProtectedRoute>} />
                <Route path="/insights/collaborations/:id" element={<ProtectedRoute><CoalitionReplayPage /></ProtectedRoute>} />
                <Route path="/admin/tenant-health" element={<ProtectedRoute><TenantHealthPage /></ProtectedRoute>} />
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
              </Suspense>
              </ChunkLoadErrorBoundary>
            </ToastProvider>
          </LunaPresenceProvider>
        </AuthProvider>
      </Router>
    </ThemeProvider>
  );
}

export default App;
