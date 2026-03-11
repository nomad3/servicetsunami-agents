import { useEffect, useState } from 'react';
import { Alert, Col, Row, Spinner } from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../App';
import Layout from '../components/Layout';
import { getDashboardStats } from '../services/analytics';

const DashboardPage = () => {
  const { t } = useTranslation('dashboard');
  const { user } = useAuth();
  const navigate = useNavigate();

  const [dashboardData, setDashboardData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchDashboardData = async () => {
      try {
        setLoading(true);
        const response = await getDashboardStats();
        setDashboardData(response.data);
        setError(null);
      } catch (err) {
        setError(t('error'));
        console.error('Error fetching dashboard stats:', err);
      } finally {
        setLoading(false);
      }
    };
    fetchDashboardData();
  }, []);

  if (loading) {
    return (
      <Layout>
        <div className="text-center py-5">
          <Spinner animation="border" role="status" variant="primary" size="sm" />
          <p className="mt-3 text-muted" style={{ fontSize: '0.85rem' }}>{t('loading')}</p>
        </div>
      </Layout>
    );
  }

  if (error) {
    return (
      <Layout>
        <Alert variant="danger" style={{ fontSize: '0.85rem' }}>{error}</Alert>
      </Layout>
    );
  }

  const { overview, activity, agents, datasets, recent_sessions } = dashboardData || {};

  // System health items
  const systemItems = [
    {
      label: t('system.agents'),
      value: overview?.total_agents ?? 0,
      sub: t('system.deployed', { count: overview?.total_deployments ?? 0 }),
      status: (overview?.total_deployments ?? 0) > 0 ? 'operational' : 'idle',
    },
    {
      label: t('system.integrations'),
      value: (overview?.total_data_sources ?? 0) + (overview?.total_pipelines ?? 0),
      sub: t('system.sourcesPipelines', { sources: overview?.total_data_sources ?? 0, pipelines: overview?.total_pipelines ?? 0 }),
      status: (overview?.total_data_sources ?? 0) > 0 ? 'operational' : 'idle',
    },
    {
      label: t('system.datasets'),
      value: overview?.total_datasets ?? 0,
      sub: t('system.rows', { count: (activity?.dataset_rows_total ?? 0).toLocaleString() }),
      status: (overview?.total_datasets ?? 0) > 0 ? 'operational' : 'idle',
    },
    {
      label: t('system.memory'),
      value: overview?.total_vector_stores ?? 0,
      sub: t('system.vectorStores'),
      status: (overview?.total_vector_stores ?? 0) > 0 ? 'operational' : 'idle',
    },
  ];

  const statusDot = (status) => ({
    width: 6,
    height: 6,
    borderRadius: '50%',
    background: status === 'operational' ? '#22c55e' : '#94a3b8',
    display: 'inline-block',
    marginRight: 6,
    flexShrink: 0,
  });

  const cardStyle = {
    background: 'var(--surface-elevated)',
    border: '1px solid var(--color-border)',
    borderRadius: 8,
    padding: '20px 24px',
  };

  const sectionLabel = {
    fontSize: '0.7rem',
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
    color: 'var(--color-muted)',
    marginBottom: 12,
  };

  return (
    <Layout>
      <div style={{ maxWidth: 1100 }}>
        {/* Header */}
        <div style={{ marginBottom: 28 }}>
          <h4 style={{ fontWeight: 600, marginBottom: 4, color: 'var(--color-foreground)' }}>
            {t('title')}
          </h4>
          <p style={{ fontSize: '0.85rem', color: 'var(--color-muted)', margin: 0 }}>
            {t('subtitle')}
          </p>
        </div>

        {/* System Status */}
        <div style={sectionLabel}>{t('systemStatus')}</div>
        <Row className="g-3 mb-4">
          {systemItems.map((item) => (
            <Col md={3} sm={6} key={item.label}>
              <div style={cardStyle}>
                <div className="d-flex align-items-center mb-2" style={{ gap: 6 }}>
                  <span style={statusDot(item.status)} />
                  <span style={{ fontSize: '0.78rem', color: 'var(--color-muted)', fontWeight: 500 }}>
                    {item.label}
                  </span>
                </div>
                <div style={{ fontSize: '1.5rem', fontWeight: 600, color: 'var(--color-foreground)', lineHeight: 1.2 }}>
                  {item.value}
                </div>
                <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)', marginTop: 4 }}>
                  {item.sub}
                </div>
              </div>
            </Col>
          ))}
        </Row>

        {/* Quick Navigation */}
        <div style={sectionLabel}>{t('quickAccess')}</div>
        <Row className="g-3 mb-4">
          {[
            { label: t('quick.aiChat'), desc: t('quick.aiChatDesc'), path: '/chat' },
            { label: t('quick.agentFleet'), desc: t('quick.agentFleetDesc'), path: '/agents' },
            { label: t('quick.integrations'), desc: t('quick.integrationsDesc'), path: '/integrations' },
            { label: t('quick.workflows'), desc: t('quick.workflowsDesc'), path: '/workflows' },
          ].map((item) => (
            <Col md={3} sm={6} key={item.label}>
              <div
                style={{
                  ...cardStyle,
                  cursor: 'pointer',
                  transition: 'border-color 0.15s ease',
                }}
                onClick={() => navigate(item.path)}
                onMouseEnter={(e) => e.currentTarget.style.borderColor = 'var(--color-primary, #4a90d9)'}
                onMouseLeave={(e) => e.currentTarget.style.borderColor = 'var(--color-border)'}
              >
                <div style={{ fontSize: '0.88rem', fontWeight: 500, color: 'var(--color-foreground)', marginBottom: 2 }}>
                  {item.label}
                </div>
                <div style={{ fontSize: '0.75rem', color: 'var(--color-muted)' }}>
                  {item.desc}
                </div>
              </div>
            </Col>
          ))}
        </Row>

        {/* Two-column layout: Recent Sessions + Agent Fleet */}
        <Row className="g-3">
          <Col lg={7}>
            <div style={sectionLabel}>{t('recentConversations')}</div>
            <div style={cardStyle}>
              {recent_sessions && recent_sessions.length > 0 ? (
                <div>
                  {recent_sessions.slice(0, 6).map((session, idx) => (
                    <div
                      key={session.id}
                      style={{
                        padding: '10px 0',
                        borderBottom: idx < Math.min(recent_sessions.length, 6) - 1 ? '1px solid var(--color-border)' : 'none',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        cursor: 'pointer',
                      }}
                      onClick={() => navigate('/chat')}
                    >
                      <div>
                        <div style={{ fontSize: '0.85rem', fontWeight: 500, color: 'var(--color-foreground)' }}>
                          {session.title}
                        </div>
                        <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>
                          {t('sessions.messages', { count: session.message_count })}
                        </div>
                      </div>
                      <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)', whiteSpace: 'nowrap' }}>
                        {new Date(session.created_at).toLocaleDateString()}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p style={{ color: 'var(--color-muted)', fontSize: '0.82rem', textAlign: 'center', margin: '20px 0' }}>
                  {t('sessions.noRecent')}
                </p>
              )}
            </div>
          </Col>

          <Col lg={5}>
            <div style={sectionLabel}>{t('agentFleet')}</div>
            <div style={cardStyle}>
              {agents && agents.length > 0 ? (
                <div>
                  {agents.slice(0, 6).map((agent, idx) => (
                    <div
                      key={agent.name}
                      style={{
                        padding: '10px 0',
                        borderBottom: idx < Math.min(agents.length, 6) - 1 ? '1px solid var(--color-border)' : 'none',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                      }}
                    >
                      <div style={{ fontSize: '0.85rem', fontWeight: 500, color: 'var(--color-foreground)' }}>
                        {agent.name}
                      </div>
                      <div className="d-flex align-items-center" style={{ gap: 4 }}>
                        <span style={statusDot(agent.deployment_count > 0 ? 'operational' : 'idle')} />
                        <span style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>
                          {agent.deployment_count > 0 ? t('agents.active') : t('agents.ready')}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p style={{ color: 'var(--color-muted)', fontSize: '0.82rem', textAlign: 'center', margin: '20px 0' }}>
                  {t('agents.noAgents')}
                </p>
              )}
            </div>

            {/* Datasets summary */}
            <div style={{ ...sectionLabel, marginTop: 16 }}>{t('datasets')}</div>
            <div style={cardStyle}>
              {datasets && datasets.length > 0 ? (
                <div>
                  {datasets.slice(0, 4).map((dataset, idx) => (
                    <div
                      key={dataset.id}
                      style={{
                        padding: '8px 0',
                        borderBottom: idx < Math.min(datasets.length, 4) - 1 ? '1px solid var(--color-border)' : 'none',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                      }}
                    >
                      <div style={{ fontSize: '0.82rem', color: 'var(--color-foreground)' }}>
                        {dataset.name}
                      </div>
                      <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>
                        {t('datasetsSection.rows', { count: dataset.rows?.toLocaleString() })}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p style={{ color: 'var(--color-muted)', fontSize: '0.82rem', textAlign: 'center', margin: '16px 0' }}>
                  {t('datasetsSection.noDatasets')}
                </p>
              )}
            </div>
          </Col>
        </Row>
      </div>
    </Layout>
  );
};

export default DashboardPage;
