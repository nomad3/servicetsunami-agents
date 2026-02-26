import { useMemo } from 'react';
import { Badge, Dropdown, Nav, OverlayTrigger, Tooltip } from 'react-bootstrap';
import {
  FaChartBar as BarChartFill,
  FaSignOutAlt as BoxArrowRight,
  FaBuilding as BuildingFill,
  FaComments as ChatDotsFill,
  FaMicrochip as CpuFill,
  FaDatabase as DatabaseFill,
  FaCog as GearFill,
  FaHome as HouseDoorFill,
  FaMoon as MoonFill,
  FaBookmark as JournalBookmarkFill,
  FaUserCircle as PersonCircle,
  FaPlug as PlugFill,
  FaRobot as Robot,
  FaSun as SunFill,
  FaTerminal as TerminalFill
} from 'react-icons/fa';
import { useTranslation } from 'react-i18next';
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

  // Simplified navigation structure
  const navSections = [
    {
      title: 'COMMAND CENTER',
      items: [
        { path: '/home', icon: HouseDoorFill, label: 'Home', description: 'Your personalized homepage' },
        { path: '/notebooks', icon: JournalBookmarkFill, label: 'Financial Reports', description: 'Financial reports & SQL notebooks' },
        { path: '/dashboard', icon: BarChartFill, label: 'Analytics Overview', description: 'Cross-business metrics and KPIs' },
      ]
    },
    {
      title: 'AI OPERATIONS',
      items: [
        { path: '/chat', icon: ChatDotsFill, label: 'AI Command', description: 'Command your AI agent fleet' },
        { path: '/agents', icon: Robot, label: 'Agent Fleet', description: 'Manage your AI agent fleet' },
        { path: '/task-console', icon: TerminalFill, label: 'Workflow Audit', description: 'Monitor workflows, tasks, and execution traces' },
        { path: '/memory', icon: DatabaseFill, label: 'Memory', description: 'Entities, signals, and relations' },
      ]
    },
    {
      title: 'DATA',
      items: [
        { path: '/integrations', icon: PlugFill, label: 'Integrations', description: 'Skills, connectors, data sources & datasets' },
      ]
    },
    {
      title: 'ADMIN',
      items: [
        { path: '/tenants', icon: BuildingFill, label: 'Organizations', description: 'Manage organizations & business units' },
        { path: '/settings/llm', icon: CpuFill, label: 'AI Models', description: 'Configure AI model providers' },
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
              <div className="nav-section-header">
                <span className="nav-section-title">{section.title}</span>
              </div>
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
