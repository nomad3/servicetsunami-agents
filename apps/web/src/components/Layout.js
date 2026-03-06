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
  FaSun as SunFill
} from 'react-icons/fa';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../App';
import { useTheme } from '../context/ThemeContext';
import './Layout.css';

const Layout = ({ children }) => {
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const { t, i18n } = useTranslation('common');
  const { theme, toggleTheme } = useTheme();

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
        { path: '/dashboard', icon: HouseDoorFill, label: 'Dashboard', description: 'Operations overview' },
      ]
    },
    {
      title: 'AI OPERATIONS',
      items: [
        { path: '/chat', icon: ChatDotsFill, label: 'AI Chat', description: 'Chat with your AI agent fleet' },
        { path: '/agents', icon: Robot, label: 'Agent Fleet', description: 'Manage your AI agent fleet' },
        { path: '/workflows', icon: ProjectDiagramFill, label: 'Workflows', description: 'Workflow designs and execution audit' },
        { path: '/memory', icon: DatabaseFill, label: 'Knowledge Base', description: 'Entities, relations, and signals' },
      ]
    },
    {
      title: 'DATA',
      items: [
        { path: '/integrations', icon: PlugFill, label: 'Integrations', description: 'Connectors, data sources & datasets' },
      ]
    },
    {
      title: 'ADMIN',
      items: [
        { path: '/tenants', icon: BuildingFill, label: 'Organizations', description: 'Manage organizations & business units' },
        { path: '/settings', icon: GearFill, label: 'Settings', description: 'Platform settings' },
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
              <div className="brand-icon">
                <Robot size={28} />
              </div>
              <span className="brand-text">{t('brand')}</span>
            </Link>
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
                  <div className="user-email">{auth.user?.email || 'Guest'}</div>
                  <div className="user-role">Administrator</div>
                </div>
              </div>
            </Dropdown.Toggle>
            <Dropdown.Menu className="w-100">
              <Dropdown.Header>Language</Dropdown.Header>
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
