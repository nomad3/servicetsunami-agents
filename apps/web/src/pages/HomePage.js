import React from 'react';
import { Card, Row, Col, Button } from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import {
  FaChartBar,
  FaComments,
  FaFileAlt,
  FaRocket,
  FaLightbulb,
  FaHistory
} from 'react-icons/fa';
import Layout from '../components/Layout';
import './HomePage.css';

const HomePage = () => {
  const { t } = useTranslation('dashboard');
  const navigate = useNavigate();

  const quickActions = [
    {
      icon: FaFileAlt,
      title: t('home.importData'),
      description: t('home.importDataDesc'),
      action: () => navigate('/datasets'),
      color: 'primary'
    },
    {
      icon: FaComments,
      title: t('home.aiCommand'),
      description: t('home.aiCommandDesc'),
      action: () => navigate('/chat'),
      color: 'success'
    },
    {
      icon: FaChartBar,
      title: t('home.analyticsOverview'),
      description: t('home.analyticsOverviewDesc'),
      action: () => navigate('/dashboard'),
      color: 'info'
    }
  ];

  const recentActivity = [
    { label: 'P&L Report - Q4', status: 'Ready for consolidation', time: '2 hours ago', type: 'dataset' },
    { label: 'Business Health Assessment', status: 'Completed', time: 'Yesterday', type: 'report' },
    { label: 'KPI Dashboard', status: 'Auto-updated', time: '3 days ago', type: 'dashboard' }
  ];

  return (
    <Layout>
      <div className="home-page">
        {/* Welcome Header */}
        <div className="welcome-section">
          <div className="welcome-content">
            <h1 className="welcome-title">
              {t('home.welcomeBack')} <span className="wave">{'\uD83D\uDC4B'}</span>
            </h1>
            <p className="welcome-subtitle">
              {t('home.whatToDo')}
            </p>
          </div>
        </div>

        {/* Quick Actions */}
        <div className="quick-actions-section">
          <h2 className="section-title">
            <FaRocket className="section-icon" />
            {t('home.quickActions')}
          </h2>
          <Row className="g-4">
            {quickActions.map((action, index) => (
              <Col key={index} md={4}>
                <Card className={`quick-action-card action-${action.color}`} onClick={action.action}>
                  <Card.Body>
                    <div className="action-icon-wrapper">
                      <action.icon size={32} className="action-icon" />
                    </div>
                    <h3 className="action-title">{action.title}</h3>
                    <p className="action-description">{action.description}</p>
                    <Button variant={action.color} size="sm" className="action-button">
                      {t('home.getStarted')} {'\u2192'}
                    </Button>
                  </Card.Body>
                </Card>
              </Col>
            ))}
          </Row>
        </div>

        {/* Recent Activity */}
        <div className="recent-activity-section">
          <h2 className="section-title">
            <FaHistory className="section-icon" />
            {t('home.recentActivity')}
          </h2>
          <Card className="activity-card">
            <Card.Body>
              {recentActivity.length > 0 ? (
                <div className="activity-list">
                  {recentActivity.map((item, index) => (
                    <div key={index} className="activity-item">
                      <div className="activity-indicator"></div>
                      <div className="activity-content">
                        <div className="activity-header">
                          <span className="activity-label">{item.label}</span>
                          <span className="activity-time">{item.time}</span>
                        </div>
                        <span className="activity-status">{item.status}</span>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="empty-activity">
                  <FaLightbulb size={48} className="empty-icon" />
                  <p className="empty-text">
                    {t('home.noActivity')}
                  </p>
                </div>
              )}
            </Card.Body>
          </Card>
        </div>

        {/* Getting Started Tips */}
        <div className="tips-section">
          <Card className="tips-card">
            <Card.Body>
              <h3 className="tips-title">
                <FaLightbulb className="tips-icon" />
                {t('home.gettingStartedTips')}
              </h3>
              <ul className="tips-list">
                <li>{t('home.tip1')}</li>
                <li>{t('home.tip2')}</li>
                <li>{t('home.tip3')}</li>
                <li>{t('home.tip4')}</li>
              </ul>
            </Card.Body>
          </Card>
        </div>
      </div>
    </Layout>
  );
};

export default HomePage;
