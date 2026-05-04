import { useMemo } from 'react';
import { Badge, Dropdown, Nav, OverlayTrigger, Tooltip } from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import {
  FaSignOutAlt as BoxArrowRight,
  FaBuilding as BuildingFill,
  FaComments as ChatDotsFill,
  FaDatabase as DatabaseFill,
  FaCog as GearFill,
  FaHome as HouseDoorFill,
  FaMoon as MoonFill,
  FaUserCircle as PersonCircle,
  FaPlug as PlugFill,
  FaProjectDiagram as ProjectDiagramFill,
  FaRobot as Robot,
  FaSun as SunFill,
  FaPuzzlePiece as PuzzlePiece,
  FaChartLine as ChartLine,
  FaHeartbeat as HeartbeatFill
} from 'react-icons/fa';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../App';
import { useLunaPresence } from '../context/LunaPresenceContext';
import { useTheme } from '../context/ThemeContext';
// LunaAvatar removed
import LunaStateBadge from './luna/LunaStateBadge';
import NotificationBell from './NotificationBell';
import './Layout.css';

const Layout = ({ children }) => {
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const { t, i18n } = useTranslation('common');
  const { theme, toggleTheme } = useTheme();
  const lunaCtx = useLunaPresence();
  const lunaState = lunaCtx?.presence?.state || 'idle';
  const lunaMood = lunaCtx?.presence?.mood || 'calm';

  const currentLanguage = (i18n.language || 'en').split('-')[0];
  const languageOptions = useMemo(
    () => [
      { code: 'en', label: t('language.english') },
      { code: 'es', label: t('language.spanish') },
    ],
    [t, i18n.language]
  );

  const handleLogout = () => {
    auth.logout();
    navigate('/login');
  };

  const handleLanguageChange = (code) => {
    i18n.changeLanguage(code);
  };

  // Navigation structure
  const navSections = [
    {
      title: null,  // No header for top-level dashboard
      items: [
        { path: '/dashboard', icon: HouseDoorFill, label: t('sidebar.dashboard'), description: t('sidebar_desc.dashboard') },
      ]
    },
    {
      title: t('sidebar.aiOperations'),
      items: [
        { path: '/chat', icon: ChatDotsFill, label: t('sidebar.chat'), description: t('sidebar_desc.chat') },
        { path: '/agents', icon: Robot, label: t('sidebar.agents'), description: t('sidebar_desc.agents') },
        { path: '/insights/fleet-health', icon: HeartbeatFill, label: t('sidebar.fleetHealth', 'Fleet Health'), description: t('sidebar_desc.fleetHealth', 'Imported-agent activity and zombies') },
        { path: '/insights/cost', icon: ChartLine, label: t('sidebar.costInsights', 'Cost & Usage'), description: t('sidebar_desc.costInsights', 'Token + cost rollup across the fleet') },
        { path: '/workflows', icon: ProjectDiagramFill, label: t('sidebar.workflows'), description: t('sidebar_desc.workflows') },
        { path: '/memory', icon: DatabaseFill, label: t('sidebar.memory'), description: t('sidebar_desc.memory') },
        { path: '/skills', icon: PuzzlePiece, label: t('sidebar.skills'), description: t('sidebar_desc.skills') },
        { path: '/learning', icon: ChartLine, label: t('sidebar.learning'), description: t('sidebar_desc.learning') },
      ]
    },
    {
      title: t('sidebar.data'),
      items: [
        { path: '/integrations', icon: PlugFill, label: t('sidebar.integrations'), description: t('sidebar_desc.integrations') },
      ]
    },
    {
      title: t('sidebar.admin'),
      items: [
        { path: '/tenants', icon: BuildingFill, label: t('sidebar.organizations'), description: t('sidebar_desc.organizations') },
        { path: '/settings', icon: GearFill, label: t('sidebar.settings'), description: t('sidebar_desc.settings') },
        // Tenant Health is superuser-only on the backend; hide the
        // link entirely for regular tenant admins so it doesn't read
        // as broken when they click and get a 403.
        ...(auth.user?.is_superuser
          ? [{ path: '/admin/tenant-health', icon: HeartbeatFill, label: t('sidebar.tenantHealth', 'Tenant Health'), description: t('sidebar_desc.tenantHealth', 'Cross-tenant superuser triage') }]
          : []),
      ]
    }
  ];

  const isActive = (path) => location.pathname === path;

  return (
    <div className="layout-container">
      {/* Glassmorphic Sidebar */}
      <div className="sidebar-glass">
        <div className="sidebar-header">
          <div className="d-flex align-items-center justify-content-between">
            <Link to="/dashboard" className="brand-link">
              <div className="d-flex flex-column">
                <span className="brand-text">{t('brand')}</span>
                <LunaStateBadge state={lunaState} size="xs" />
              </div>
            </Link>
            <div className="d-flex align-items-center gap-1">
              <NotificationBell />
              <button
                className="theme-toggle"
                onClick={toggleTheme}
                aria-label={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
                title={theme === 'light' ? 'Dark mode' : 'Light mode'}
              >
                {theme === 'light' ? <MoonFill size={16} /> : <SunFill size={16} />}
              </button>
            </div>
          </div>
        </div>

        <Nav className="flex-column sidebar-nav">
          {navSections.map((section, sectionIndex) => (
            <div key={`section-${sectionIndex}`} className="nav-section">
              {section.title && (
                <div className="nav-section-header">
                  <span className="nav-section-title">{section.title}</span>
                </div>
              )}
              {section.items.map((item) => {
                const Icon = item.icon;
                return (
                  <OverlayTrigger
                    key={item.path}
                    placement="right"
                    delay={{ show: 500, hide: 0 }}
                    overlay={<Tooltip id={`tooltip-${item.path}`}>{item.description}</Tooltip>}
                  >
                    <Nav.Link
                      as={Link}
                      to={item.path}
                      className={`sidebar-nav-link ${isActive(item.path) ? 'active' : ''}`}
                    >
                      <Icon className="nav-icon" size={20} />
                      <span className="nav-label">{item.label}</span>
                      {item.badge && (
                        <Badge bg="primary" className="nav-badge">{item.badge}</Badge>
                      )}
                    </Nav.Link>
                  </OverlayTrigger>
                );
              })}
            </div>
          ))}
        </Nav>

        <div className="sidebar-footer">
          <Dropdown drop="up" className="w-100">
            <Dropdown.Toggle variant="link" className="user-dropdown-toggle w-100">
              <div className="d-flex align-items-center gap-2">
                <PersonCircle size={32} className="text-primary" />
                <div className="flex-grow-1 text-start">
                  <div className="user-email">{auth.user?.email || t('layout.guest')}</div>
                  <div className="user-role">{t('sidebar.administrator')}</div>
                </div>
              </div>
            </Dropdown.Toggle>
            <Dropdown.Menu className="w-100">
              <Dropdown.Header>{t('language.label')}</Dropdown.Header>
              {languageOptions.map(({ code, label }) => (
                <Dropdown.Item
                  key={code}
                  active={currentLanguage === code}
                  onClick={() => handleLanguageChange(code)}
                >
                  {label}
                </Dropdown.Item>
              ))}
              <Dropdown.Divider />
              <Dropdown.Item onClick={handleLogout}>
                <BoxArrowRight className="me-2" /> {t('layout.logout')}
              </Dropdown.Item>
            </Dropdown.Menu>
          </Dropdown>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="main-content">
        <div className="content-wrapper">
          {children}
        </div>
      </div>
    </div>
  );
};

export default Layout;
