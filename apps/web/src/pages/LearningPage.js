import { useEffect, useState } from 'react';
import { Alert, Badge, Col, Container, Form, Row, Spinner, Table } from 'react-bootstrap';
import { useTranslation } from 'react-i18next';
import Layout from '../components/Layout';
import learningService from '../services/learningService';

// ── Shared styles (use design tokens) ────────────────────────────

const sectionLabelClass = 'ap-section-label';

const tabsBarStyle = {
  display: 'flex',
  gap: 0,
  borderBottom: '1px solid var(--ap-border)',
  marginBottom: '1.25rem',
};

const tabBtnBase = {
  background: 'none',
  border: 'none',
  padding: '0.65rem 1.2rem',
  fontSize: 'var(--ap-fs-sm)',
  fontWeight: 600,
  color: 'var(--ap-text-muted)',
  cursor: 'pointer',
  borderBottom: '2px solid transparent',
  transition: 'color 0.2s, border-color 0.2s',
};

const tabBtnActive = {
  ...tabBtnBase,
  color: 'var(--ap-text)',
  borderBottomColor: 'var(--ap-primary)',
};

// Reward color helper — uses semantic tokens (success / danger / primary)
const rewardColor = (val) => {
  if (val == null) return 'var(--ap-text-muted)';
  if (val > 0) return 'var(--ap-success)';
  if (val < 0) return 'var(--ap-danger)';
  return 'var(--ap-primary)';
};

// ── Metric Tile ─────────────────────────────────────────────────

function MetricTile({ label, value, sub, accent }) {
  return (
    <article className="ap-card h-100" style={{ textAlign: 'center' }}>
      <div className="ap-card-body">
        <div
          style={{
            fontSize: 'var(--ap-fs-xl)',
            fontWeight: 700,
            color: accent || 'var(--ap-text)',
            lineHeight: 1.1,
            marginBottom: 4,
          }}
        >
          {value ?? '—'}
        </div>
        <div
          style={{
            fontSize: 'var(--ap-fs-sm)',
            fontWeight: 600,
            color: 'var(--ap-text)',
            marginBottom: 2,
          }}
        >
          {label}
        </div>
        {sub && (
          <div style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>{sub}</div>
        )}
      </div>
    </article>
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
        <p className="mt-3" style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)' }}>
          {t('loading')}
        </p>
      </div>
    );
  }

  if (error) {
    return <Alert variant="danger">{error}</Alert>;
  }

  const tiles = [
    {
      label: t('overview.totalExperiences'),
      value: data?.total_experiences?.toLocaleString() ?? 0,
      sub: t('overview.allTime'),
      accent: 'var(--ap-primary)',
    },
    {
      label: t('overview.avgReward'),
      value: data?.avg_reward_30d != null ? data.avg_reward_30d.toFixed(3) : '—',
      sub: t('overview.last30d'),
      accent: rewardColor(data?.avg_reward_30d),
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
      <div className={sectionLabelClass}>{t('overview.metricsLabel')}</div>
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
            <div className={sectionLabelClass}>{t('overview.topDecisionPoints', 'Top Decision Points')}</div>
            <article className="ap-card">
              <div className="ap-card-body">
                {data.top_decision_points.map((dp, idx) => (
                  <div
                    key={dp.id ?? idx}
                    style={{
                      padding: '10px 0',
                      borderBottom:
                        idx < data.top_decision_points.length - 1
                          ? '1px solid var(--ap-border)'
                          : 'none',
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                    }}
                  >
                    <div>
                      <div style={{ fontSize: 'var(--ap-fs-sm)', fontWeight: 500, color: 'var(--ap-text)' }}>
                        {dp.name?.replace(/_/g, ' ')}
                      </div>
                      <div style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>
                        {dp.experience_count ?? dp.count ?? 0} experiences
                      </div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: 'var(--ap-fs-sm)', fontWeight: 600, color: rewardColor(dp.avg_reward) }}>
                        {dp.avg_reward != null ? (dp.avg_reward > 0 ? `+${dp.avg_reward.toFixed(2)}` : dp.avg_reward.toFixed(2)) : 'unrated'}
                      </div>
                      <div style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>
                        avg reward
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </article>
          </Col>
        )}

        {data?.recent_activity?.length > 0 && (
          <Col md={6}>
            <div className={sectionLabelClass}>Recent Activity</div>
            <article className="ap-card">
              <div className="ap-card-body">
                {data.recent_activity.slice(0, 8).map((act, idx) => (
                  <div
                    key={act.id ?? idx}
                    style={{
                      padding: '8px 0',
                      borderBottom:
                        idx < Math.min(data.recent_activity.length, 8) - 1
                          ? '1px solid var(--ap-border)'
                          : 'none',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <Badge
                          bg={act.decision_point === 'agent_selection' ? 'primary' : act.decision_point === 'tool_selection' ? 'info' : 'secondary'}
                          style={{ fontSize: 'var(--ap-fs-xs)', fontWeight: 500, marginRight: 6 }}
                        >
                          {act.decision_point?.replace(/_/g, ' ')}
                        </Badge>
                        <span style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text)' }}>
                          {act.action_preview || act.state_preview?.substring(0, 60) || '—'}
                        </span>
                      </div>
                      <div style={{ flexShrink: 0, marginLeft: 8 }}>
                        {act.reward != null ? (
                          <span style={{ fontSize: 'var(--ap-fs-sm)', fontWeight: 600, color: rewardColor(act.reward) }}>
                            {act.reward > 0 ? '+' : ''}{act.reward.toFixed(1)}
                          </span>
                        ) : (
                          <span style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>unrated</span>
                        )}
                      </div>
                    </div>
                    {act.state_preview && (
                      <div style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {act.state_preview}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </article>
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
    return <Alert variant="danger">{error}</Alert>;
  }

  if (items.length === 0) {
    return (
      <div className="ap-empty">
        <div className="ap-empty-title">{t('decisionPoints.empty')}</div>
      </div>
    );
  }

  return (
    <article className="ap-card">
      <Table responsive hover className="ap-table mb-0">
        <thead>
          <tr>
            <th>{t('decisionPoints.colName')}</th>
            <th>{t('decisionPoints.colScoreVersion')}</th>
            <th>{t('decisionPoints.colExperienceCount')}</th>
            <th>{t('decisionPoints.colExplorationRate')}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((dp) => (
            <>
              <tr
                key={dp.id}
                style={{ cursor: 'pointer' }}
                onClick={() => setExpandedId(expandedId === dp.id ? null : dp.id)}
              >
                <td style={{ fontWeight: 500 }}>
                  {dp.name}
                </td>
                <td style={{ color: 'var(--ap-primary)' }}>
                  {dp.avg_reward != null ? dp.avg_reward.toFixed(3) : '—'}
                  {dp.version && (
                    <span style={{ marginLeft: 6, fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>
                      v{dp.version}
                    </span>
                  )}
                </td>
                <td>
                  {dp.experience_count?.toLocaleString() ?? 0}
                </td>
                <td>
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
                      background: 'var(--ap-code-bg)',
                      padding: 'var(--ap-space-4) var(--ap-space-5)',
                    }}
                  >
                    <div style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)' }}>
                      {dp.description
                        ? dp.description
                        : <span style={{ fontStyle: 'italic' }}>{t('decisionPoints.noDescription')}</span>}
                    </div>
                    {dp.actions && dp.actions.length > 0 && (
                      <div style={{ marginTop: 8, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        {dp.actions.map((action) => (
                          <Badge
                            key={action}
                            bg="secondary"
                            style={{ fontSize: 'var(--ap-fs-xs)', fontWeight: 500, padding: '3px 8px' }}
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
    </article>
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
    return <Alert variant="danger">{error}</Alert>;
  }

  if (items.length === 0) {
    return (
      <div className="ap-empty">
        <div className="ap-empty-title">{t('experiments.empty')}</div>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--ap-space-3)' }}>
      {items.map((exp, idx) => (
        <article key={exp.id ?? idx} className="ap-card">
          <div className="ap-card-body">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)', marginBottom: 2 }}>
                  {t('experiments.decisionPoint')}:{' '}
                  <span style={{ color: 'var(--ap-text)', fontWeight: 500 }}>
                    {exp.decision_point_name ?? exp.decision_point_id ?? '—'}
                  </span>
                </div>
                <div style={{ fontSize: 'var(--ap-fs-base)', fontWeight: 500, color: 'var(--ap-text)', marginBottom: 4 }}>
                  {exp.action ?? '—'}
                </div>
                {exp.context && (
                  <div style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)', lineHeight: 1.4 }}>
                    {exp.context}
                  </div>
                )}
              </div>
              <div style={{ textAlign: 'right', flexShrink: 0 }}>
                <div style={{ fontSize: 'var(--ap-fs-lg)', fontWeight: 700, color: rewardColor(exp.reward) }}>
                  {exp.reward != null ? (exp.reward > 0 ? `+${exp.reward.toFixed(3)}` : exp.reward.toFixed(3)) : '—'}
                </div>
                <div style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>
                  {t('experiments.reward')}
                </div>
                {exp.created_at && (
                  <div style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)', marginTop: 4 }}>
                    {new Date(exp.created_at).toLocaleDateString()}
                  </div>
                )}
              </div>
            </div>
          </div>
        </article>
      ))}
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
    return <Alert variant="danger">{error}</Alert>;
  }

  if (items.length === 0) {
    return (
      <div className="ap-empty">
        <div className="ap-empty-title">{t('reviews.empty')}</div>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--ap-space-3)' }}>
      {items.map((item, idx) => {
        const status = rating[item.id];
        return (
          <article key={item.id ?? idx} className="ap-card">
            <div className="ap-card-body">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)', marginBottom: 2 }}>
                    {t('reviews.decisionPoint')}:{' '}
                    <span style={{ color: 'var(--ap-text)', fontWeight: 500 }}>
                      {item.decision_point_name ?? item.decision_point_id ?? '—'}
                    </span>
                  </div>
                  <div style={{ fontSize: 'var(--ap-fs-base)', fontWeight: 500, color: 'var(--ap-text)', marginBottom: 4 }}>
                    {item.action ?? '—'}
                  </div>
                  {item.outcome && (
                    <div style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)', lineHeight: 1.4 }}>
                      {t('reviews.outcome')}: {item.outcome}
                    </div>
                  )}
                </div>
                <div style={{ display: 'flex', gap: 6, flexShrink: 0, alignItems: 'center' }}>
                  {status === 'submitting' ? (
                    <Spinner animation="border" size="sm" />
                  ) : status === 'done' ? (
                    <span style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-success)' }}>{t('reviews.rated')}</span>
                  ) : (
                    <>
                      <button
                        type="button"
                        className="ap-btn-secondary ap-btn-sm"
                        onClick={() => handleRate(item.id, 'good')}
                      >
                        {t('reviews.good')}
                      </button>
                      <button
                        type="button"
                        className="ap-btn-secondary ap-btn-sm"
                        onClick={() => handleRate(item.id, 'acceptable')}
                      >
                        {t('reviews.acceptable')}
                      </button>
                      <button
                        type="button"
                        className="ap-btn-danger ap-btn-sm"
                        onClick={() => handleRate(item.id, 'poor')}
                      >
                        {t('reviews.poor')}
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>
          </article>
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
        <Alert variant="danger" dismissible onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {saved && (
        <Alert variant="success">{t('settings.saved')}</Alert>
      )}

      {/* Exploration */}
      <article className="ap-card" style={{ marginBottom: 'var(--ap-space-4)' }}>
        <div className="ap-card-body">
          <h3 className="ap-card-title">{t('settings.explorationTitle')}</h3>

          <Form.Group className="mb-3">
            <Form.Label style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text)', fontWeight: 500 }}>
              {t('settings.explorationRate')}{' '}
              <span style={{ color: 'var(--ap-primary)', fontWeight: 700 }}>
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
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>
              <span>0%</span>
              <span>20%</span>
            </div>
          </Form.Group>
        </div>
      </article>

      {/* Toggles */}
      <article className="ap-card" style={{ marginBottom: 'var(--ap-space-4)' }}>
        <div className="ap-card-body">
          <h3 className="ap-card-title">{t('settings.behaviorTitle')}</h3>

          <Form.Check
            type="switch"
            id="use-global-baseline"
            label={
              <span style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text)' }}>
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
              <span style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text)' }}>
                {t('settings.contributeGlobal')}
              </span>
            }
            checked={settings.contribute_to_global}
            onChange={(e) =>
              setSettings((prev) => ({ ...prev, contribute_to_global: e.target.checked }))
            }
          />
        </div>
      </article>

      {/* Reward Weights */}
      <article className="ap-card" style={{ marginBottom: 'var(--ap-space-6)' }}>
        <div className="ap-card-body">
          <h3 className="ap-card-title">{t('settings.rewardWeightsTitle')}</h3>

          <Row className="g-3">
            {[
              { key: 'reward_weight_implicit', label: t('settings.weightImplicit') },
              { key: 'reward_weight_explicit', label: t('settings.weightExplicit') },
              { key: 'reward_weight_admin', label: t('settings.weightAdmin') },
            ].map(({ key, label }) => (
              <Col sm={4} key={key}>
                <Form.Group>
                  <Form.Label style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)', fontWeight: 500 }}>
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
                  />
                </Form.Group>
              </Col>
            ))}
          </Row>
        </div>
      </article>

      <button
        type="button"
        className="ap-btn-primary"
        onClick={handleSave}
        disabled={saving}
        style={{ minWidth: 100 }}
      >
        {saving ? <Spinner animation="border" size="sm" /> : t('settings.save')}
      </button>
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
    return <Alert variant="danger">{error}</Alert>;
  }
  if (data.length === 0) {
    return (
      <div className="ap-empty">
        <div className="ap-empty-title">Not enough data yet</div>
        <div className="ap-empty-text">Use different platforms and agents to see performance comparisons.</div>
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
      <div className={sectionLabelClass}>Platform Comparison</div>
      <Row className="g-3 mb-4">
        {Object.entries(platforms).map(([platform, info]) => {
          const avgReward = info.totalCount > 0 ? info.totalReward / info.totalCount : 0;
          const color = rewardColor(avgReward);
          return (
            <Col md={4} sm={6} key={platform}>
              <article className="ap-card h-100" style={{ borderLeft: `3px solid ${color}` }}>
                <div className="ap-card-body">
                  <div className="ap-section-label" style={{ marginBottom: 8 }}>
                    {platform.replace(/_/g, ' ')}
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                    <div>
                      <span style={{ fontSize: 'var(--ap-fs-lg)', fontWeight: 700, color }}>
                        {avgReward > 0 ? '+' : ''}{avgReward.toFixed(3)}
                      </span>
                      <span style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)', marginLeft: 6 }}>avg reward</span>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: 'var(--ap-fs-base)', fontWeight: 600, color: 'var(--ap-text)' }}>
                        {info.totalCount}
                      </div>
                      <div style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>tasks</div>
                    </div>
                  </div>
                </div>
              </article>
            </Col>
          );
        })}
      </Row>

      <div className={sectionLabelClass}>Breakdown by Agent & Task Type</div>
      <article className="ap-card">
        <Table responsive hover className="ap-table mb-0">
          <thead>
            <tr>
              {['Platform', 'Agent', 'Task Type', 'Count', 'Avg Reward', 'Positive %'].map((h) => (
                <th key={h}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map((item, idx) => (
              <tr key={idx}>
                <td style={{ fontWeight: 500 }}>
                  <Badge bg={item.platform === 'claude_code' ? 'primary' : item.platform === 'codex' ? 'dark' : item.platform === 'gemini_cli' ? 'success' : 'secondary'} style={{ fontSize: 'var(--ap-fs-xs)' }}>
                    {(item.platform || 'unknown').replace(/_/g, ' ')}
                  </Badge>
                </td>
                <td>{item.agent_slug || '—'}</td>
                <td>{item.task_type || '—'}</td>
                <td>{item.total || 0}</td>
                <td style={{ color: rewardColor(item.avg_reward), fontWeight: 600 }}>
                  {item.avg_reward != null ? ((item.avg_reward > 0 ? '+' : '') + item.avg_reward.toFixed(3)) : '—'}
                </td>
                <td>
                  {item.positive_pct != null ? `${item.positive_pct.toFixed(1)}%` : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      </article>
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
        <header className="ap-page-header">
          <div>
            <h1 className="ap-page-title">{t('title')}</h1>
            <p className="ap-page-subtitle">{t('subtitle')}</p>
          </div>
          <div className="ap-page-actions">
            <button type="button" className="ap-btn-secondary ap-btn-sm" onClick={handleExport}>
              Export CSV
            </button>
          </div>
        </header>

        {/* Tabs */}
        <div style={tabsBarStyle}>
          {TABS.map((tab) => (
            <button
              key={tab}
              type="button"
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
