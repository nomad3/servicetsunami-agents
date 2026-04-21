import React, { useState, useEffect } from 'react';
import { Form, Row, Col, Spinner, Alert } from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import Layout from '../components/Layout';
import api from '../services/api';
import './SettingsPage.css';

const SettingsPage = () => {
  const { t } = useTranslation('settings');
  const [postgresStatus, setPostgreSQLStatus] = useState(null);
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [initializing, setInitializing] = useState(false);
  const [message, setMessage] = useState(null);

  useEffect(() => {
    fetchPostgreSQLStatus();
  }, []);

  const fetchPostgreSQLStatus = async () => {
    try {
      setLoadingStatus(true);
      const response = await api.get('/postgres/status');
      setPostgreSQLStatus(response.data);
    } catch (err) {
      console.error('Error fetching PostgreSQL status:', err);
    } finally {
      setLoadingStatus(false);
    }
  };

  const handleInitialize = async () => {
    try {
      setInitializing(true);
      setMessage(null);
      const response = await api.post('/postgres/initialize');
      setMessage({ type: 'success', text: t('postgres.initSuccess') });
      fetchPostgreSQLStatus(); // Refresh status
    } catch (err) {
      setMessage({
        type: 'danger',
        text: err.response?.data?.detail || t('postgres.initFailed')
      });
    } finally {
      setInitializing(false);
    }
  };

  return (
    <Layout>
      <div className="settings-page">
        <header className="ap-page-header">
          <div>
            <h1 className="ap-page-title">{t('title')}</h1>
            <p className="ap-page-subtitle">{t('subtitle')}</p>
          </div>
        </header>

        <Row className="g-4">
          {/* Profile Settings */}
          <Col md={12}>
            <article className="ap-card">
              <div className="ap-card-body">
                <h3 className="ap-card-title settings-section-title">{t('profile.title')}</h3>
                <Form>
                  <Row className="g-3">
                    <Col md={6}>
                      <Form.Group>
                        <Form.Label>{t('profile.fullName')}</Form.Label>
                        <Form.Control type="text" placeholder={t('profile.fullNamePlaceholder')} />
                      </Form.Group>
                    </Col>
                    <Col md={6}>
                      <Form.Group>
                        <Form.Label>{t('profile.email')}</Form.Label>
                        <Form.Control type="email" placeholder={t('profile.emailPlaceholder')} disabled />
                      </Form.Group>
                    </Col>
                    <Col md={12}>
                      <Form.Group>
                        <Form.Label>{t('profile.organization')}</Form.Label>
                        <Form.Control type="text" placeholder={t('profile.organizationPlaceholder')} />
                      </Form.Group>
                    </Col>
                  </Row>
                  <div className="settings-actions">
                    <button type="button" className="ap-btn-primary">{t('profile.saveChanges')}</button>
                  </div>
                </Form>
              </div>
            </article>
          </Col>

          {/* Notification Settings */}
          <Col md={12}>
            <article className="ap-card">
              <div className="ap-card-body">
                <h3 className="ap-card-title settings-section-title">{t('notifications.title')}</h3>
                <Form>
                  <Form.Check
                    type="switch"
                    id="email-notifications"
                    label={t('notifications.emailUpdates')}
                    className="settings-switch"
                    defaultChecked
                  />
                  <Form.Check
                    type="switch"
                    id="data-alerts"
                    label={t('notifications.dataAlerts')}
                    className="settings-switch"
                    defaultChecked
                  />
                  <Form.Check
                    type="switch"
                    id="ai-insights"
                    label={t('notifications.aiInsights')}
                    className="settings-switch"
                  />
                  <Form.Check
                    type="switch"
                    id="system-updates"
                    label={t('notifications.systemUpdates')}
                    className="settings-switch"
                    defaultChecked
                  />
                </Form>
              </div>
            </article>
          </Col>

          {/* Security Settings */}
          <Col md={12}>
            <article className="ap-card">
              <div className="ap-card-body">
                <h3 className="ap-card-title settings-section-title">{t('security.title')}</h3>
                <div className="security-item">
                  <div className="security-info">
                    <strong>{t('security.password')}</strong>
                    <p className="security-text">{t('security.passwordLastChanged')}</p>
                  </div>
                  <button type="button" className="ap-btn-secondary ap-btn-sm">{t('security.changePassword')}</button>
                </div>
                <div className="security-item">
                  <div className="security-info">
                    <strong>{t('security.twoFactor')}</strong>
                    <p className="security-text">
                      <span className="ap-status ap-status-draft">{t('security.twoFactorNotEnabled')}</span> {t('security.twoFactorDescription')}
                    </p>
                  </div>
                  <button type="button" className="ap-btn-secondary ap-btn-sm">{t('security.enable2FA')}</button>
                </div>
              </div>
            </article>
          </Col>

          {/* Plan & Billing */}
          <Col md={12}>
            <article className="ap-card">
              <div className="ap-card-body">
                <h3 className="ap-card-title settings-section-title">{t('billing.title')}</h3>
                <div className="plan-info">
                  <div className="current-plan">
                    <div>
                      <h4 className="plan-name">{t('billing.planName')}</h4>
                      <p className="plan-description">{t('billing.planDescription')}</p>
                    </div>
                    <span className="ap-status ap-status-production">{t('billing.active')}</span>
                  </div>
                  <div className="billing-details">
                    <div className="billing-item">
                      <span className="billing-label">{t('billing.nextBilling')}</span>
                      <span className="billing-value">January 1, 2026</span>
                    </div>
                    <div className="billing-item">
                      <span className="billing-label">{t('billing.amount')}</span>
                      <span className="billing-value">$99/month</span>
                    </div>
                  </div>
                  <div className="settings-actions">
                    <button type="button" className="ap-btn-secondary">{t('billing.manageSubscription')}</button>
                    <button type="button" className="ap-btn-ghost">{t('billing.viewHistory')}</button>
                  </div>
                </div>
              </div>
            </article>
          </Col>
        </Row>
      </div>
    </Layout>
  );
};

export default SettingsPage;
