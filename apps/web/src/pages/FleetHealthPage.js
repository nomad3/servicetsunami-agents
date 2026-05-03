/*
 * FleetHealthPage — Tier 3 of the visibility roadmap.
 *
 * Shows tenants which agents in their fleet are alive vs zombies vs
 * erroring, with source / owner / team filters. Backed by the new
 * `GET /agents/fleet-health` endpoint (curated lean schema, cursor
 * pagination, audit_log aggregations).
 *
 * Lives at `/insights/fleet-health`. The same INSIGHTS bucket where
 * Tier 2 cost dashboard will land — keeps "fleet observability"
 * surfaces clustered.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Badge, Button, Form, Spinner, Table } from 'react-bootstrap';
import {
  FaCloudDownloadAlt,
  FaExclamationTriangle,
  FaFilter,
  FaGithub,
  FaMicrosoft,
  FaRobot,
  FaServer,
} from 'react-icons/fa';
import { useNavigate } from 'react-router-dom';

import Layout from '../components/Layout';
import api from '../services/api';
import './FleetHealthPage.css';


// Source label + icon map. Keep in sync with the importer values
// written to `agent.config.metadata.source` (apps/api/app/services/
// agent_importer.py). Used both for the filter chips and the per-row
// badge.
const SOURCE_BADGE = {
  copilot_studio: { label: 'Copilot Studio', Icon: FaMicrosoft, color: 'info' },
  ai_foundry:     { label: 'AI Foundry',     Icon: FaMicrosoft, color: 'info' },
  crewai:         { label: 'CrewAI',         Icon: FaRobot,     color: 'warning' },
  langchain:      { label: 'LangChain',      Icon: FaRobot,     color: 'warning' },
  autogen:        { label: 'AutoGen',        Icon: FaRobot,     color: 'warning' },
  github:         { label: 'GitHub',         Icon: FaGithub,    color: 'secondary' },
  native:         { label: 'Native',         Icon: FaServer,    color: 'success' },
};


function _relativeTime(iso) {
  if (!iso) return 'never';
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000) return 'just now';
  if (ms < 3600_000) return `${Math.floor(ms / 60_000)}m ago`;
  if (ms < 86400_000) return `${Math.floor(ms / 3600_000)}h ago`;
  return `${Math.floor(ms / 86400_000)}d ago`;
}


const FleetHealthPage = () => {
  const navigate = useNavigate();
  const [rows, setRows] = useState([]);
  const [cursor, setCursor] = useState(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(null);

  // Filters
  const [filterMode, setFilterMode] = useState('all'); // all | zombies
  const [sourceFilter, setSourceFilter] = useState(''); // '' | one of SOURCE_BADGE keys

  const fetchPage = useCallback(async ({ append = false, cursorParam = null } = {}) => {
    if (append) setLoadingMore(true); else setLoading(true);
    setError(null);
    try {
      const params = { limit: 50 };
      if (cursorParam) params.cursor = cursorParam;
      if (filterMode === 'zombies') params.zombies = 'true';
      if (sourceFilter) params.source = sourceFilter;
      const resp = await api.get('/agents/fleet-health', { params });
      const data = resp.data || {};
      setRows(prev => (append ? [...prev, ...(data.rows || [])] : (data.rows || [])));
      setCursor(data.next_cursor || null);
      setHasMore(!!data.has_more);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to load fleet health.');
    } finally {
      if (append) setLoadingMore(false); else setLoading(false);
    }
  }, [filterMode, sourceFilter]);

  // Refetch from page 1 whenever a filter changes
  useEffect(() => {
    fetchPage({ append: false, cursorParam: null });
  }, [fetchPage]);

  const handleLoadMore = () => {
    if (cursor) fetchPage({ append: true, cursorParam: cursor });
  };

  // Stat tiles at the top of the page derived from currently-loaded rows.
  // These are local to the page (not a separate aggregate endpoint) so a
  // tenant with thousands of agents sees the totals for what's on screen,
  // not the whole tenant — matches "what you see is what you can act on"
  // and avoids a second network round-trip.
  const headerStats = useMemo(() => ({
    total: rows.length,
    zombies: rows.filter(r => {
      if (!r.last_invoked_at) return true;
      const days = (Date.now() - new Date(r.last_invoked_at).getTime()) / 86400_000;
      return days > 14;
    }).length,
    erroring: rows.filter(r => !!r.latest_error).length,
  }), [rows]);

  const renderSourceBadge = (source) => {
    const b = SOURCE_BADGE[source] || SOURCE_BADGE.native;
    const Icon = b.Icon;
    return (
      <Badge bg={b.color} style={{ fontWeight: 500 }}>
        <Icon size={9} className="me-1" /> {b.label}
      </Badge>
    );
  };

  return (
    <Layout>
      <div className="fleet-health-page">
        <header className="ap-page-header">
          <div>
            <h1 className="ap-page-title">
              <FaServer className="me-2" /> Fleet Health
            </h1>
            <p className="ap-page-subtitle">
              Imported agents across your tenant — last invoked, recent
              activity, errors, ownership.
            </p>
          </div>
        </header>

        {error && (
          <Alert variant="danger" onClose={() => setError(null)} dismissible>
            {error}
          </Alert>
        )}

        {/* Stat tiles for the loaded slice */}
        <div className="fleet-stats-grid mb-4">
          <div className="fleet-stat-tile">
            <div className="fleet-stat-value">{headerStats.total}</div>
            <div className="fleet-stat-label">Loaded</div>
          </div>
          <div className="fleet-stat-tile fleet-stat-warning">
            <div className="fleet-stat-value">{headerStats.zombies}</div>
            <div className="fleet-stat-label">
              <FaCloudDownloadAlt size={11} className="me-1" /> Idle &gt;14 days
            </div>
          </div>
          <div className="fleet-stat-tile fleet-stat-danger">
            <div className="fleet-stat-value">{headerStats.erroring}</div>
            <div className="fleet-stat-label">
              <FaExclamationTriangle size={11} className="me-1" /> With recent errors
            </div>
          </div>
        </div>

        {/* Filter chips */}
        <div className="ap-chip-row mb-3" role="tablist">
          <button
            type="button"
            className={`ap-chip-filter ${filterMode === 'all' ? 'active' : ''}`}
            onClick={() => setFilterMode('all')}
          >
            <FaFilter size={11} /> All
          </button>
          <button
            type="button"
            className={`ap-chip-filter ${filterMode === 'zombies' ? 'active' : ''}`}
            onClick={() => setFilterMode('zombies')}
          >
            <FaCloudDownloadAlt size={11} /> Zombies
          </button>
        </div>

        <div className="d-flex align-items-center gap-2 mb-3" style={{ fontSize: '0.85rem' }}>
          <strong>Source:</strong>
          <Form.Select
            size="sm"
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
            style={{ maxWidth: 220 }}
          >
            <option value="">All sources</option>
            {Object.entries(SOURCE_BADGE).map(([key, b]) => (
              <option key={key} value={key}>{b.label}</option>
            ))}
          </Form.Select>
        </div>

        {loading ? (
          <div className="text-center py-5">
            <Spinner animation="border" variant="primary" />
          </div>
        ) : !rows.length ? (
          <Alert variant="info">
            No agents match the current filter. {filterMode === 'zombies'
              ? 'Looks like everyone\'s been busy — no idle agents in the past 14 days.'
              : 'Connect Microsoft and import via /agents/microsoft/discover to populate the fleet.'}
          </Alert>
        ) : (
          <>
            <Table hover responsive className="fleet-health-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Source</th>
                  <th>Status</th>
                  <th>Owner</th>
                  <th className="text-nowrap">Last invoked</th>
                  <th className="text-end">7d calls</th>
                  <th className="text-end">7d cost</th>
                  <th>Last error</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.id}
                    onClick={() => navigate(`/agents/${r.id}`)}
                    style={{ cursor: 'pointer' }}
                  >
                    <td><strong>{r.name}</strong></td>
                    <td>{renderSourceBadge(r.source)}</td>
                    <td>
                      <Badge bg={r.status === 'production' ? 'success' : 'secondary'}>
                        {r.status}
                      </Badge>
                    </td>
                    <td className="text-truncate" style={{ maxWidth: 180 }}>
                      {r.owner_email || <span className="text-muted">—</span>}
                    </td>
                    <td className="text-nowrap text-muted">
                      {_relativeTime(r.last_invoked_at)}
                    </td>
                    <td className="text-end">
                      {r.invocations_7d > 0 ? r.invocations_7d.toLocaleString('en-US') : '—'}
                    </td>
                    <td className="text-end">
                      {r.cost_usd_7d > 0 ? `$${r.cost_usd_7d.toFixed(2)}` : '—'}
                    </td>
                    <td className="text-truncate" style={{ maxWidth: 240 }} title={r.latest_error || ''}>
                      {r.latest_error
                        ? <span className="text-danger" style={{ fontSize: '0.8rem' }}>
                            <FaExclamationTriangle size={10} className="me-1" />
                            {r.latest_error}
                          </span>
                        : <span className="text-muted">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>

            {hasMore && (
              <div className="text-center mt-3">
                <Button
                  variant="outline-primary"
                  onClick={handleLoadMore}
                  disabled={loadingMore}
                >
                  {loadingMore
                    ? <><Spinner animation="border" size="sm" /> Loading…</>
                    : 'Load more'}
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </Layout>
  );
};

export default FleetHealthPage;
