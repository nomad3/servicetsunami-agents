import { useEffect, useState } from 'react';
import { Alert, Badge, Button, Col, Container, Form, Row, Spinner, Table } from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import Layout from '../components/Layout';
import learningService from '../services/learningService';

// ── Shared styles (Ocean Theme) ──────────────────────────────────

const cardStyle = {
  background: 'var(--surface-elevated)',
  border: '1px solid var(--color-border)',
  borderRadius: 10,
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

const tabsBarStyle = {
  display: 'flex',
  gap: 0,
  borderBottom: '1px solid var(--color-border)',
  marginBottom: '1.25rem',
};

const tabBtnBase = {
  background: 'none',
  border: 'none',
  padding: '0.65rem 1.2rem',
  fontSize: '0.82rem',
  fontWeight: 600,
  color: 'var(--color-muted)',
  cursor: 'pointer',
  borderBottom: '2px solid transparent',
  transition: 'color 0.2s, border-color 0.2s',
};

const tabBtnActive = {
  ...tabBtnBase,
  color: 'var(--color-foreground)',
  borderBottomColor: '#60a5fa',
};

// ── Metric Tile ─────────────────────────────────────────────────

function MetricTile({ label, value, sub, accent }) {
  return (
    <div
      style={{
        ...cardStyle,
        textAlign: 'center',
        transition: 'border-color 0.2s',
      }}
      onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'rgba(96,165,250,0.3)')}
      onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--color-border)')}
    >
      <div
        style={{
          fontSize: '1.6rem',
          fontWeight: 700,
          color: accent || 'var(--color-foreground)',
          lineHeight: 1.1,
          marginBottom: 4,
        }}
      >
        {value ?? '—'}
      </div>
      <div
        style={{
          fontSize: '0.78rem',
          fontWeight: 600,
          color: 'var(--color-foreground)',
          marginBottom: 2,
        }}
      >
        {label}
      </div>
      {sub && (
        <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>{sub}</div>
      )}
    </div>
  );
}

// ── Overview Tab ─────────────────────────────────────────────────

function OverviewTab({ t }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        const result = await learningService.getOverview();
        setData(result);
        setError(null);
      } catch (err) {
        setError(t('overview.errorLoad'));
        console.error('Failed to load learning overview:', err);
      } finally {
        setLoading(false);
      }
    })();
  }, [t]);

  if (loading) {
    return (
      <div className="text-center py-5">
        <Spinner animation="border" size="sm" variant="primary" />
        <p className="mt-3" style={{ fontSize: '0.85rem', color: 'var(--color-muted)' }}>
          {t('loading')}
        </p>
      </div>
    );
  }

  if (error) {
    return <Alert variant="danger" style={{ fontSize: '0.85rem' }}>{error}</Alert>;
  }

  const tiles = [
    {
      label: t('overview.totalExperiences'),
      value: data?.total_experiences?.toLocaleString() ?? 0,
      sub: t('overview.allTime'),
      accent: '#60a5fa',
    },
    {
      label: t('overview.avgReward'),
      value: data?.avg_reward_30d != null ? data.avg_reward_30d.toFixed(3) : '—',
      sub: t('overview.last30d'),
      accent: data?.avg_reward_30d > 0 ? '#34d399' : data?.avg_reward_30d < 0 ? '#f87171' : undefined,
    },
    {
      label: t('overview.explorationRate'),
      value: data?.exploration_rate != null ? `${(data.exploration_rate * 100).toFixed(1)}%` : '—',
      sub: t('overview.epsilonGreedy'),
    },
    {
      label: t('overview.policyVersion'),
      value: data?.policy_version ?? '—',
      sub: t('overview.lastUpdated', { date: data?.policy_updated_at ? new Date(data.policy_updated_at).toLocaleDateString() : '—' }),
    },
  ];

  return (
    <>
      <div style={sectionLabel}>{t('overview.metricsLabel')}</div>
      <Row className="g-3 mb-4">
        {tiles.map((tile) => (
          <Col md={3} sm={6} key={tile.label}>
            <MetricTile {...tile} />
          </Col>
        ))}
      </Row>

      <Row className="g-3 mb-4">
        {data?.top_decision_points?.length > 0 && (
          <Col md={6}>
            <div style={sectionLabel}>{t('overview.topDecisionPoints', 'Top Decision Points')}</div>
            <div style={cardStyle}>
              {data.top_decision_points.map((dp, idx) => (
                <div
                  key={dp.id ?? idx}
                  style={{
                    padding: '10px 0',
                    borderBottom:
                      idx < data.top_decision_points.length - 1
                        ? '1px solid var(--color-border)'
                        : 'none',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                  }}
                >
                  <div>
                    <div style={{ fontSize: '0.85rem', fontWeight: 500, color: 'var(--color-foreground)' }}>
                      {dp.name?.replace(/_/g, ' ')}
                    </div>
                    <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>
                      {dp.experience_count ?? dp.count ?? 0} experiences
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: '0.85rem', fontWeight: 600, color: dp.avg_reward > 0 ? '#34d399' : dp.avg_reward < 0 ? '#f87171' : '#60a5fa' }}>
                      {dp.avg_reward != null ? (dp.avg_reward > 0 ? `+${dp.avg_reward.toFixed(2)}` : dp.avg_reward.toFixed(2)) : 'unrated'}
                    </div>
                    <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)' }}>
                      avg reward
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </Col>
        )}

        {data?.recent_activity?.length > 0 && (
          <Col md={6}>
            <div style={sectionLabel}>Recent Activity</div>
            <div style={cardStyle}>
              {data.recent_activity.slice(0, 8).map((act, idx) => (
                <div
                  key={act.id ?? idx}
                  style={{
                    padding: '8px 0',
                    borderBottom:
                      idx < Math.min(data.recent_activity.length, 8) - 1
                        ? '1px solid var(--color-border)'
                        : 'none',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <Badge
                        bg={act.decision_point === 'agent_selection' ? 'primary' : act.decision_point === 'tool_selection' ? 'info' : 'secondary'}
                        style={{ fontSize: '0.65rem', fontWeight: 500, marginRight: 6 }}
                      >
                        {act.decision_point?.replace(/_/g, ' ')}
                      </Badge>
                      <span style={{ fontSize: '0.8rem', color: 'var(--color-foreground)' }}>
                        {act.action_preview || act.state_preview?.substring(0, 60) || '—'}
                      </span>
                    </div>
                    <div style={{ flexShrink: 0, marginLeft: 8 }}>
                      {act.reward != null ? (
                        <span style={{ fontSize: '0.8rem', fontWeight: 600, color: act.reward > 0 ? '#34d399' : '#f87171' }}>
                          {act.reward > 0 ? '+' : ''}{act.reward.toFixed(1)}
                        </span>
                      ) : (
                        <span style={{ fontSize: '0.7rem', color: 'var(--color-muted)' }}>unrated</span>
                      )}
                    </div>
                  </div>
                  {act.state_preview && (
                    <div style={{ fontSize: '0.72rem', color: 'var(--color-muted)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {act.state_preview}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </Col>
        )}
      </Row>
    </>
  );
}

// ── Decision Points Tab ──────────────────────────────────────────

function DecisionPointsTab({ t }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [expandedId, setExpandedId] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        const result = await learningService.getDecisionPoints();
        setItems(result || []);
        setError(null);
      } catch (err) {
        setError(t('decisionPoints.errorLoad'));
        console.error('Failed to load decision points:', err);
      } finally {
        setLoading(false);
      }
    })();
  }, [t]);

  if (loading) {
    return (
      <div className="text-center py-5">
        <Spinner animation="border" size="sm" variant="primary" />
      </div>
    );
  }

  if (error) {
    return <Alert variant="danger" style={{ fontSize: '0.85rem' }}>{error}</Alert>;
  }

  if (items.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--color-muted)' }}>
        <div style={{ fontSize: '2.5rem', marginBottom: '0.75rem', opacity: 0.4 }}>🎯</div>
        <p style={{ fontSize: '0.9rem' }}>{t('decisionPoints.empty')}</p>
      </div>
    );
  }

  return (
    <div style={cardStyle}>
      <Table responsive hover style={{ marginBottom: 0, fontSize: '0.85rem' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
            <th style={{ fontWeight: 600, color: 'var(--color-muted)', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.05em', paddingBottom: 10, background: 'transparent', border: 'none' }}>
              {t('decisionPoints.colName')}
            </th>
            <th style={{ fontWeight: 600, color: 'var(--color-muted)', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.05em', paddingBottom: 10, background: 'transparent', border: 'none' }}>
              {t('decisionPoints.colScoreVersion')}
            </th>
            <th style={{ fontWeight: 600, color: 'var(--color-muted)', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.05em', paddingBottom: 10, background: 'transparent', border: 'none' }}>
              {t('decisionPoints.colExperienceCount')}
            </th>
            <th style={{ fontWeight: 600, color: 'var(--color-muted)', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.05em', paddingBottom: 10, background: 'transparent', border: 'none' }}>
              {t('decisionPoints.colExplorationRate')}
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((dp) => (
            <>
              <tr
                key={dp.id}
                style={{ cursor: 'pointer', borderBottom: '1px solid var(--color-border)' }}
                onClick={() => setExpandedId(expandedId === dp.id ? null : dp.id)}
              >
                <td style={{ color: 'var(--color-foreground)', fontWeight: 500, border: 'none', paddingTop: 12, paddingBottom: 12 }}>
                  {dp.name}
                </td>
                <td style={{ color: '#60a5fa', border: 'none', paddingTop: 12, paddingBottom: 12 }}>
                  {dp.avg_reward != null ? dp.avg_reward.toFixed(3) : '—'}
                  {dp.version && (
                    <span style={{ marginLeft: 6, fontSize: '0.7rem', color: 'var(--color-muted)' }}>
                      v{dp.version}
                    </span>
                  )}
                </td>
                <td style={{ color: 'var(--color-foreground)', border: 'none', paddingTop: 12, paddingBottom: 12 }}>
                  {dp.experience_count?.toLocaleString() ?? 0}
                </td>
                <td style={{ color: 'var(--color-foreground)', border: 'none', paddingTop: 12, paddingBottom: 12 }}>
                  {dp.exploration_rate != null
                    ? `${(dp.exploration_rate * 100).toFixed(1)}%`
                    : '—'}
                </td>
              </tr>
              {expandedId === dp.id && (
                <tr key={`${dp.id}-detail`}>
                  <td
                    colSpan={4}
                    style={{
                      background: 'var(--surface-contrast, rgba(0,0,0,0.02))',
                      border: 'none',
                      padding: '12px 16px',
                      borderBottom: '1px solid var(--color-border)',
                    }}
                  >
                    <div style={{ fontSize: '0.82rem', color: 'var(--color-soft)' }}>
                      {dp.description
                        ? dp.description
                        : <span style={{ color: 'var(--color-muted)', fontStyle: 'italic' }}>{t('decisionPoints.noDescription')}</span>}
                    </div>
                    {dp.actions && dp.actions.length > 0 && (
                      <div style={{ marginTop: 8, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        {dp.actions.map((action) => (
                          <Badge
                            key={action}
                            bg="secondary"
                            style={{ fontSize: '0.7rem', fontWeight: 500, padding: '3px 8px' }}
                          >
                            {action}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </td>
                </tr>
              )}
            </>
          ))}
        </tbody>
      </Table>
    </div>
  );
}

// ── Experiments Tab ──────────────────────────────────────────────

function ExperimentsTab({ t }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        const result = await learningService.getExperiments();
        setItems(result || []);
        setError(null);
      } catch (err) {
        setError(t('experiments.errorLoad'));
        console.error('Failed to load experiments:', err);
      } finally {
        setLoading(false);
      }
    })();
  }, [t]);

  if (loading) {
    return (
      <div className="text-center py-5">
        <Spinner animation="border" size="sm" variant="primary" />
      </div>
    );
  }

  if (error) {
    return <Alert variant="danger" style={{ fontSize: '0.85rem' }}>{error}</Alert>;
  }

  if (items.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--color-muted)' }}>
        <div style={{ fontSize: '2.5rem', marginBottom: '0.75rem', opacity: 0.4 }}>🔬</div>
        <p style={{ fontSize: '0.9rem' }}>{t('experiments.empty')}</p>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      {items.map((exp, idx) => {
        const rewardColor =
          exp.reward > 0 ? '#34d399' : exp.reward < 0 ? '#f87171' : 'var(--color-muted)';
        return (
          <div key={exp.id ?? idx} style={cardStyle}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: '0.78rem', color: 'var(--color-muted)', marginBottom: 2 }}>
                  {t('experiments.decisionPoint')}:{' '}
                  <span style={{ color: 'var(--color-soft)', fontWeight: 500 }}>
                    {exp.decision_point_name ?? exp.decision_point_id ?? '—'}
                  </span>
                </div>
                <div style={{ fontSize: '0.88rem', fontWeight: 500, color: 'var(--color-foreground)', marginBottom: 4 }}>
                  {exp.action ?? '—'}
                </div>
                {exp.context && (
                  <div style={{ fontSize: '0.78rem', color: 'var(--color-muted)', lineHeight: 1.4 }}>
                    {exp.context}
                  </div>
                )}
              </div>
              <div style={{ textAlign: 'right', flexShrink: 0 }}>
                <div style={{ fontSize: '1.1rem', fontWeight: 700, color: rewardColor }}>
                  {exp.reward != null ? (exp.reward > 0 ? `+${exp.reward.toFixed(3)}` : exp.reward.toFixed(3)) : '—'}
                </div>
                <div style={{ fontSize: '0.7rem', color: 'var(--color-muted)' }}>
                  {t('experiments.reward')}
                </div>
                {exp.created_at && (
                  <div style={{ fontSize: '0.7rem', color: 'var(--color-muted)', marginTop: 4 }}>
                    {new Date(exp.created_at).toLocaleDateString()}
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Reviews Tab ──────────────────────────────────────────────────

function ReviewsTab({ t }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [rating, setRating] = useState({});

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        const result = await learningService.getPendingReviews();
        setItems(result || []);
        setError(null);
      } catch (err) {
        setError(t('reviews.errorLoad'));
        console.error('Failed to load pending reviews:', err);
      } finally {
        setLoading(false);
      }
    })();
  }, [t]);

  const handleRate = async (id, value) => {
    try {
      setRating((prev) => ({ ...prev, [id]: 'submitting' }));
      await learningService.rateExperience(id, value);
      setRating((prev) => ({ ...prev, [id]: 'done' }));
      setItems((prev) => prev.filter((item) => item.id !== id));
    } catch (err) {
      console.error('Failed to rate experience:', err);
      setRating((prev) => ({ ...prev, [id]: 'error' }));
    }
  };

  if (loading) {
    return (
      <div className="text-center py-5">
        <Spinner animation="border" size="sm" variant="primary" />
      </div>
    );
  }

  if (error) {
    return <Alert variant="danger" style={{ fontSize: '0.85rem' }}>{error}</Alert>;
  }

  if (items.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--color-muted)' }}>
        <div style={{ fontSize: '2.5rem', marginBottom: '0.75rem', opacity: 0.4 }}>✅</div>
        <p style={{ fontSize: '0.9rem' }}>{t('reviews.empty')}</p>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      {items.map((item, idx) => {
        const status = rating[item.id];
        return (
          <div key={item.id ?? idx} style={cardStyle}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: '0.78rem', color: 'var(--color-muted)', marginBottom: 2 }}>
                  {t('reviews.decisionPoint')}:{' '}
                  <span style={{ color: 'var(--color-soft)', fontWeight: 500 }}>
                    {item.decision_point_name ?? item.decision_point_id ?? '—'}
                  </span>
                </div>
                <div style={{ fontSize: '0.88rem', fontWeight: 500, color: 'var(--color-foreground)', marginBottom: 4 }}>
                  {item.action ?? '—'}
                </div>
                {item.outcome && (
                  <div style={{ fontSize: '0.78rem', color: 'var(--color-muted)', lineHeight: 1.4 }}>
                    {t('reviews.outcome')}: {item.outcome}
                  </div>
                )}
              </div>
              <div style={{ display: 'flex', gap: 6, flexShrink: 0, alignItems: 'center' }}>
                {status === 'submitting' ? (
                  <Spinner animation="border" size="sm" />
                ) : status === 'done' ? (
                  <span style={{ fontSize: '0.8rem', color: '#34d399' }}>{t('reviews.rated')}</span>
                ) : (
                  <>
                    <Button
                      size="sm"
                      variant="outline-success"
                      style={{ fontSize: '0.75rem', padding: '3px 10px' }}
                      onClick={() => handleRate(item.id, 'good')}
                    >
                      {t('reviews.good')}
                    </Button>
                    <Button
                      size="sm"
                      variant="outline-warning"
                      style={{ fontSize: '0.75rem', padding: '3px 10px' }}
                      onClick={() => handleRate(item.id, 'acceptable')}
                    >
                      {t('reviews.acceptable')}
                    </Button>
                    <Button
                      size="sm"
                      variant="outline-danger"
                      style={{ fontSize: '0.75rem', padding: '3px 10px' }}
                      onClick={() => handleRate(item.id, 'poor')}
                    >
                      {t('reviews.poor')}
                    </Button>
                  </>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Settings Tab ─────────────────────────────────────────────────

function SettingsTab({ t }) {
  const [settings, setSettings] = useState({
    exploration_rate: 0.1,
    use_global_baseline: true,
    contribute_to_global: true,
    reward_weight_implicit: 1.0,
    reward_weight_explicit: 2.0,
    reward_weight_admin: 5.0,
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        const result = await learningService.getSettings();
        if (result) setSettings((prev) => ({ ...prev, ...result }));
        setError(null);
      } catch (err) {
        // non-fatal: use defaults
        console.warn('Could not load learning settings, using defaults:', err);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const handleSave = async () => {
    try {
      setSaving(true);
      setSaved(false);
      await learningService.updateSettings(settings);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      setError(t('settings.errorSave'));
      console.error('Failed to save learning settings:', err);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="text-center py-5">
        <Spinner animation="border" size="sm" variant="primary" />
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 680 }}>
      {error && (
        <Alert variant="danger" dismissible onClose={() => setError(null)} style={{ fontSize: '0.85rem' }}>
          {error}
        </Alert>
      )}
      {saved && (
        <Alert variant="success" style={{ fontSize: '0.85rem' }}>{t('settings.saved')}</Alert>
      )}

      {/* Exploration */}
      <div style={{ ...cardStyle, marginBottom: 16 }}>
        <h6 style={{ color: 'var(--color-foreground)', fontWeight: 600, marginBottom: 16 }}>
          {t('settings.explorationTitle')}
        </h6>

        <Form.Group className="mb-3">
          <Form.Label style={{ fontSize: '0.85rem', color: 'var(--color-foreground)', fontWeight: 500 }}>
            {t('settings.explorationRate')}{' '}
            <span style={{ color: '#60a5fa', fontWeight: 700 }}>
              {(settings.exploration_rate * 100).toFixed(1)}%
            </span>
          </Form.Label>
          <Form.Range
            min={0}
            max={0.2}
            step={0.005}
            value={settings.exploration_rate}
            onChange={(e) =>
              setSettings((prev) => ({ ...prev, exploration_rate: parseFloat(e.target.value) }))
            }
          />
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.72rem', color: 'var(--color-muted)' }}>
            <span>0%</span>
            <span>20%</span>
          </div>
        </Form.Group>
      </div>

      {/* Toggles */}
      <div style={{ ...cardStyle, marginBottom: 16 }}>
        <h6 style={{ color: 'var(--color-foreground)', fontWeight: 600, marginBottom: 16 }}>
          {t('settings.behaviorTitle')}
        </h6>

        <Form.Check
          type="switch"
          id="use-global-baseline"
          label={
            <span style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>
              {t('settings.useGlobalBaseline')}
            </span>
          }
          checked={settings.use_global_baseline}
          onChange={(e) =>
            setSettings((prev) => ({ ...prev, use_global_baseline: e.target.checked }))
          }
          className="mb-3"
        />

        <Form.Check
          type="switch"
          id="contribute-global"
          label={
            <span style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>
              {t('settings.contributeGlobal')}
            </span>
          }
          checked={settings.contribute_to_global}
          onChange={(e) =>
            setSettings((prev) => ({ ...prev, contribute_to_global: e.target.checked }))
          }
        />
      </div>

      {/* Reward Weights */}
      <div style={{ ...cardStyle, marginBottom: 24 }}>
        <h6 style={{ color: 'var(--color-foreground)', fontWeight: 600, marginBottom: 16 }}>
          {t('settings.rewardWeightsTitle')}
        </h6>

        <Row className="g-3">
          {[
            { key: 'reward_weight_implicit', label: t('settings.weightImplicit') },
            { key: 'reward_weight_explicit', label: t('settings.weightExplicit') },
            { key: 'reward_weight_admin', label: t('settings.weightAdmin') },
          ].map(({ key, label }) => (
            <Col sm={4} key={key}>
              <Form.Group>
                <Form.Label style={{ fontSize: '0.82rem', color: 'var(--color-muted)', fontWeight: 500 }}>
                  {label}
                </Form.Label>
                <Form.Control
                  type="number"
                  size="sm"
                  min={0}
                  step={0.1}
                  value={settings[key]}
                  onChange={(e) =>
                    setSettings((prev) => ({ ...prev, [key]: parseFloat(e.target.value) || 0 }))
                  }
                  style={{
                    background: 'var(--surface-elevated)',
                    border: '1px solid var(--color-border)',
                    color: 'var(--color-foreground)',
                    fontSize: '0.85rem',
                    borderRadius: 8,
                  }}
                />
              </Form.Group>
            </Col>
          ))}
        </Row>
      </div>

      <Button
        variant="primary"
        size="sm"
        onClick={handleSave}
        disabled={saving}
        style={{ minWidth: 100 }}
      >
        {saving ? <Spinner animation="border" size="sm" /> : t('settings.save')}
      </Button>
    </div>
  );
}

// ── Platform Performance Tab ─────────────────────────────────────

function PlatformPerformanceTab({ t }) {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        const result = await learningService.getPlatformPerformance(3);
        setData(result || []);
        setError(null);
      } catch (err) {
        setError('Failed to load platform performance');
        console.error(err);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) {
    return <div className="text-center py-5"><Spinner animation="border" size="sm" variant="primary" /></div>;
  }
  if (error) {
    return <Alert variant="danger" style={{ fontSize: '0.85rem' }}>{error}</Alert>;
  }
  if (data.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--color-muted)' }}>
        <div style={{ fontSize: '2.5rem', marginBottom: '0.75rem', opacity: 0.4 }}>📊</div>
        <p style={{ fontSize: '0.9rem' }}>Not enough data yet. Use different platforms and agents to see performance comparisons.</p>
      </div>
    );
  }

  // Group by platform
  const platforms = {};
  data.forEach((item) => {
    const p = item.platform || 'unknown';
    if (!platforms[p]) platforms[p] = { items: [], totalReward: 0, totalCount: 0 };
    platforms[p].items.push(item);
    platforms[p].totalReward += (item.avg_reward || 0) * (item.total || 0);
    platforms[p].totalCount += item.total || 0;
  });

  return (
    <>
      <div style={sectionLabel}>Platform Comparison</div>
      <Row className="g-3 mb-4">
        {Object.entries(platforms).map(([platform, info]) => {
          const avgReward = info.totalCount > 0 ? info.totalReward / info.totalCount : 0;
          const color = avgReward > 0 ? '#34d399' : avgReward < 0 ? '#f87171' : '#60a5fa';
          return (
            <Col md={4} sm={6} key={platform}>
              <div style={{ ...cardStyle, borderLeft: `3px solid ${color}` }}>
                <div style={{ fontSize: '0.72rem', fontWeight: 600, textTransform: 'uppercase', color: 'var(--color-muted)', marginBottom: 8, letterSpacing: '0.05em' }}>
                  {platform.replace(/_/g, ' ')}
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <div>
                    <span style={{ fontSize: '1.4rem', fontWeight: 700, color }}>
                      {avgReward > 0 ? '+' : ''}{avgReward.toFixed(3)}
                    </span>
                    <span style={{ fontSize: '0.75rem', color: 'var(--color-muted)', marginLeft: 6 }}>avg reward</span>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: '0.9rem', fontWeight: 600, color: 'var(--color-foreground)' }}>
                      {info.totalCount}
                    </div>
                    <div style={{ fontSize: '0.7rem', color: 'var(--color-muted)' }}>tasks</div>
                  </div>
                </div>
              </div>
            </Col>
          );
        })}
      </Row>

      <div style={sectionLabel}>Breakdown by Agent & Task Type</div>
      <div style={cardStyle}>
        <Table responsive hover style={{ marginBottom: 0, fontSize: '0.85rem' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
              {['Platform', 'Agent', 'Task Type', 'Count', 'Avg Reward', 'Positive %'].map((h) => (
                <th key={h} style={{ fontWeight: 600, color: 'var(--color-muted)', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.05em', paddingBottom: 10, background: 'transparent', border: 'none' }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map((item, idx) => (
              <tr key={idx} style={{ borderBottom: '1px solid var(--color-border)' }}>
                <td style={{ color: 'var(--color-foreground)', fontWeight: 500, border: 'none', paddingTop: 10, paddingBottom: 10 }}>
                  <Badge bg={item.platform === 'claude_code' ? 'primary' : item.platform === 'codex' ? 'dark' : item.platform === 'gemini_cli' ? 'success' : 'secondary'} style={{ fontSize: '0.7rem' }}>
                    {(item.platform || 'unknown').replace(/_/g, ' ')}
                  </Badge>
                </td>
                <td style={{ color: 'var(--color-foreground)', border: 'none', paddingTop: 10, paddingBottom: 10 }}>
                  {item.agent_slug || '—'}
                </td>
                <td style={{ color: 'var(--color-foreground)', border: 'none', paddingTop: 10, paddingBottom: 10 }}>
                  {item.task_type || '—'}
                </td>
                <td style={{ color: 'var(--color-foreground)', border: 'none', paddingTop: 10, paddingBottom: 10 }}>
                  {item.total || 0}
                </td>
                <td style={{ color: (item.avg_reward || 0) > 0 ? '#34d399' : (item.avg_reward || 0) < 0 ? '#f87171' : '#60a5fa', fontWeight: 600, border: 'none', paddingTop: 10, paddingBottom: 10 }}>
                  {item.avg_reward != null ? ((item.avg_reward > 0 ? '+' : '') + item.avg_reward.toFixed(3)) : '—'}
                </td>
                <td style={{ color: 'var(--color-foreground)', border: 'none', paddingTop: 10, paddingBottom: 10 }}>
                  {item.positive_pct != null ? `${item.positive_pct.toFixed(1)}%` : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>
    </>
  );
}

// ── Main Page ────────────────────────────────────────────────────

const TABS = ['overview', 'platformPerformance', 'decisionPoints', 'experiments', 'reviews', 'settings'];

const TAB_LABELS = {
  overview: 'Overview',
  platformPerformance: 'Platform Performance',
  decisionPoints: 'Decision Points',
  experiments: 'Experiments',
  reviews: 'Reviews',
  settings: 'Settings',
};

function LearningPage() {
  const { t } = useTranslation('learning');
  const [activeTab, setActiveTab] = useState('overview');

  const handleExport = async () => {
    try {
      const blob = await learningService.exportExperiences();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `rl-experiences-${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Export failed:', err);
    }
  };

  return (
    <Layout>
      <Container fluid className="py-2">
        {/* Page Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem', marginBottom: '1.25rem' }}>
          <div>
            <h2 style={{ fontSize: '1.75rem', fontWeight: 700, color: 'var(--color-foreground)', letterSpacing: '-0.02em', margin: 0 }}>
              {t('title')}
            </h2>
            <p style={{ color: 'var(--color-muted)', fontSize: '0.95rem', margin: '0.25rem 0 0 0' }}>
              {t('subtitle')}
            </p>
          </div>
          <Button variant="outline-secondary" size="sm" onClick={handleExport} style={{ fontSize: '0.8rem' }}>
            Export CSV
          </Button>
        </div>

        {/* Tabs */}
        <div style={tabsBarStyle}>
          {TABS.map((tab) => (
            <button
              key={tab}
              style={activeTab === tab ? tabBtnActive : tabBtnBase}
              onClick={() => setActiveTab(tab)}
            >
              {TAB_LABELS[tab] || t(`tabs.${tab}`, tab)}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        {activeTab === 'overview' && <OverviewTab t={t} />}
        {activeTab === 'platformPerformance' && <PlatformPerformanceTab t={t} />}
        {activeTab === 'decisionPoints' && <DecisionPointsTab t={t} />}
        {activeTab === 'experiments' && <ExperimentsTab t={t} />}
        {activeTab === 'reviews' && <ReviewsTab t={t} />}
        {activeTab === 'settings' && <SettingsTab t={t} />}
      </Container>
    </Layout>
  );
}

export default LearningPage;
