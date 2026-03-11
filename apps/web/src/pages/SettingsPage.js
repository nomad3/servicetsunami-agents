import React, { useState, useEffect } from 'react';
import { Card, Form, Button, Row, Col, Badge, Spinner, Alert } from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import { FaCog, FaUser, FaBell, FaShieldAlt, FaCreditCard, FaCloud } from 'react-icons/fa';
import Layout from '../components/Layout';
import api from '../services/api';
import './SettingsPage.css';

const SettingsPage = () => {
  const { t } = useTranslation('settings');
  const [databricksStatus, setDatabricksStatus] = useState(null);
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [initializing, setInitializing] = useState(false);
  const [message, setMessage] = useState(null);

  useEffect(() => {
    fetchDatabricksStatus();
  }, []);

  const fetchDatabricksStatus = async () => {
    try {
      setLoadingStatus(true);
      const response = await api.get('/databricks/status');
      setDatabricksStatus(response.data);
    } catch (err) {
      console.error('Error fetching Databricks status:', err);
    } finally {
      setLoadingStatus(false);
    }
  };

  const handleInitialize = async () => {
    try {
      setInitializing(true);
      setMessage(null);
      const response = await api.post('/databricks/initialize');
      setMessage({ type: 'success', text: t('databricks.initSuccess') });
      fetchDatabricksStatus(); // Refresh status
    } catch (err) {
      setMessage({
        type: 'danger',
        text: err.response?.data?.detail || t('databricks.initFailed')
      });
    } finally {
      setInitializing(false);
    }
  };

  return (
    <Layout>
      <div className="settings-page">
        <div className="page-header">
          <h1 className="page-title">
            <FaCog className="title-icon" />
            {t('title')}
          </h1>
          <p className="page-subtitle">{t('subtitle')}</p>
        </div>

        <Row className="g-4">
          {/* Profile Settings */}
          <Col md={12}>
            <Card className="settings-card">
              <Card.Body>
                <div className="settings-section-header">
                  <FaUser className="section-icon" />
                  <h3 className="section-title">{t('profile.title')}</h3>
                </div>
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
                    <Button variant="primary">{t('profile.saveChanges')}</Button>
                  </div>
                </Form>
              </Card.Body>
            </Card>
          </Col>

          {/* Notification Settings */}
          <Col md={12}>
            <Card className="settings-card">
              <Card.Body>
                <div className="settings-section-header">
                  <FaBell className="section-icon" />
                  <h3 className="section-title">{t('notifications.title')}</h3>
                </div>
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
              </Card.Body>
            </Card>
          </Col>

          {/* Security Settings */}
          <Col md={12}>
            <Card className="settings-card">
              <Card.Body>
                <div className="settings-section-header">
                  <FaShieldAlt className="section-icon" />
                  <h3 className="section-title">{t('security.title')}</h3>
                </div>
                <div className="security-item">
                  <div className="security-info">
                    <strong>{t('security.password')}</strong>
                    <p className="security-text">{t('security.passwordLastChanged')}</p>
                  </div>
                  <Button variant="outline-primary" size="sm">{t('security.changePassword')}</Button>
                </div>
                <div className="security-item">
                  <div className="security-info">
                    <strong>{t('security.twoFactor')}</strong>
                    <p className="security-text">
                      <Badge bg="warning">{t('security.twoFactorNotEnabled')}</Badge> {t('security.twoFactorDescription')}
                    </p>
                  </div>
                  <Button variant="outline-primary" size="sm">{t('security.enable2FA')}</Button>
                </div>
              </Card.Body>
            </Card>
          </Col>

          {/* Databricks Integration */}
          <Col md={12}>
            <Card className="settings-card">
              <Card.Body>
                <div className="settings-section-header">
                  <FaCloud className="section-icon" />
                  <h3 className="section-title">{t('databricks.title')}</h3>
                </div>

                {message && (
                  <Alert variant={message.type} dismissible onClose={() => setMessage(null)} className="mb-3">
                    {message.text}
                  </Alert>
                )}

                {loadingStatus ? (
                  <div className="text-center py-3">
                    <Spinner animation="border" size="sm" /> {t('databricks.loading')}
                  </div>
                ) : databricksStatus ? (
                  <>
                    <div className="security-item">
                      <div className="security-info">
                        <strong>{t('databricks.mcpServer')}</strong>
                        <p className="security-text">
                          {databricksStatus.enabled ? (
                            <>
                              {databricksStatus.mcp_server?.healthy ? (
                                <Badge bg="success">{t('databricks.connected')}</Badge>
                              ) : (
                                <Badge bg="warning">{t('databricks.pendingSetup')}</Badge>
                              )}
                              {' '}{t('databricks.server', { url: databricksStatus.mcp_server?.url })}
                            </>
                          ) : (
                            <Badge bg="secondary">{t('databricks.disabled')}</Badge>
                          )}
                        </p>
                      </div>
                    </div>

                    {databricksStatus.enabled && (
                      <>
                        <div className="security-item">
                          <div className="security-info">
                            <strong>{t('databricks.unityCatalog')}</strong>
                            <p className="security-text">
                              {databricksStatus.tenant_catalog?.exists ? (
                                <>
                                  <Badge bg="success">{t('databricks.initialized')}</Badge>
                                  {' '}{t('databricks.catalog', { name: databricksStatus.tenant_catalog?.catalog_name })}
                                  {databricksStatus.tenant_catalog?.schemas &&
                                    ` (${t('databricks.schemas', { count: databricksStatus.tenant_catalog.schemas.length })})`}
                                </>
                              ) : (
                                <>
                                  <Badge bg="warning">{t('databricks.notInitialized')}</Badge>
                                  {' '}{t('databricks.setupWorkspace')}
                                </>
                              )}
                            </p>
                          </div>
                          {!databricksStatus.tenant_catalog?.exists && (
                            <Button
                              variant="outline-primary"
                              size="sm"
                              onClick={handleInitialize}
                              disabled={initializing}
                            >
                              {initializing ? (
                                <>
                                  <Spinner animation="border" size="sm" className="me-2" />
                                  {t('databricks.initializing')}
                                </>
                              ) : (
                                t('databricks.initializeCatalog')
                              )}
                            </Button>
                          )}
                        </div>

                        <div className="security-item">
                          <div className="security-info">
                            <strong>{t('databricks.capabilities')}</strong>
                            <p className="security-text">
                              {databricksStatus.capabilities && (
                                <div className="d-flex gap-2 flex-wrap mt-2">
                                  {databricksStatus.capabilities.datasets && <Badge bg="info">Datasets</Badge>}
                                  {databricksStatus.capabilities.notebooks && <Badge bg="info">Notebooks</Badge>}
                                  {databricksStatus.capabilities.jobs && <Badge bg="info">Jobs</Badge>}
                                  {databricksStatus.capabilities.model_serving && <Badge bg="info">Model Serving</Badge>}
                                  {databricksStatus.capabilities.vector_search && <Badge bg="info">Vector Search</Badge>}
                                </div>
                              )}
                            </p>
                          </div>
                        </div>

                        {databricksStatus.mcp_server?.error && (
                          <Alert variant="warning" className="mb-0 mt-3">
                            <strong>{t('databricks.connectionIssue')}</strong> {databricksStatus.mcp_server.error}
                            <br />
                            <small>{t('databricks.contactSupport')}</small>
                          </Alert>
                        )}
                      </>
                    )}
                  </>
                ) : (
                  <Alert variant="info">{t('databricks.unableToLoad')}</Alert>
                )}
              </Card.Body>
            </Card>
          </Col>

          {/* Plan & Billing */}
          <Col md={12}>
            <Card className="settings-card">
              <Card.Body>
                <div className="settings-section-header">
                  <FaCreditCard className="section-icon" />
                  <h3 className="section-title">{t('billing.title')}</h3>
                </div>
                <div className="plan-info">
                  <div className="current-plan">
                    <div>
                      <h4 className="plan-name">{t('billing.planName')}</h4>
                      <p className="plan-description">{t('billing.planDescription')}</p>
                    </div>
                    <Badge bg="success" className="plan-badge">{t('billing.active')}</Badge>
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
                    <Button variant="outline-primary">{t('billing.manageSubscription')}</Button>
                    <Button variant="link">{t('billing.viewHistory')}</Button>
                  </div>
                </div>
              </Card.Body>
            </Card>
          </Col>
        </Row>
      </div>
    </Layout>
  );
};

export default SettingsPage;
