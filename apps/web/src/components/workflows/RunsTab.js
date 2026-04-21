import React, { useState, useEffect, useCallback } from 'react';
import { Table, Badge, Form, Spinner } from 'react-bootstrap';
import { FiRefreshCw, FiPlay } from 'react-icons/fi';
import { useTranslation } from 'react-i18next';
import dynamicWorkflowService from '../../services/dynamicWorkflowService';

const STATUS_COLORS = {
  running: 'primary', completed: 'success', failed: 'danger', cancelled: 'secondary',
};

export default function RunsTab({ workflows = [] }) {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState('all');
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [runDetail, setRunDetail] = useState(null);
  const { t } = useTranslation('workflows');

  const loadRuns = useCallback(async () => {
    setLoading(true);
    try {
      const allRuns = [];
      for (const wf of workflows) {
        const wfRuns = await dynamicWorkflowService.listRuns(wf.id, 20).catch(() => []);
        allRuns.push(...(wfRuns || []).map((r) => ({ ...r, workflow_name: wf.name })));
      }
      allRuns.sort((a, b) => new Date(b.started_at) - new Date(a.started_at));
      setRuns(allRuns);
    } catch (err) {
      console.error('Failed to load runs:', err);
    }
    setLoading(false);
  }, [workflows]);

  useEffect(() => { loadRuns(); }, [loadRuns]);

  const handleRunClick = async (runId) => {
    setSelectedRunId(runId);
    try {
      const resp = await dynamicWorkflowService.getRun(runId);
      setRunDetail({ ...resp.run, step_logs: resp.steps });
    } catch (err) {
      console.error('Failed to load run detail:', err);
    }
  };

  const filtered = statusFilter === 'all'
    ? runs
    : runs.filter((r) => r.status === statusFilter);

  if (selectedRunId && runDetail) {
    return (
      <div>
        <button
          type="button"
          className="ap-btn-ghost runs-back-btn"
          onClick={() => { setSelectedRunId(null); setRunDetail(null); }}
        >
          {t('runs.backToRuns')}
        </button>
        <div className="runs-detail-panel">
          <div className="d-flex align-items-center gap-2 mb-3">
            <Badge bg={STATUS_COLORS[runDetail.status]}>{runDetail.status}</Badge>
            <span className="stat-label">
              {runDetail.duration_ms ? `${(runDetail.duration_ms / 1000).toFixed(1)}s` : `${t('runs.running')}...`}
            </span>
            {runDetail.total_cost_usd > 0 && (
              <span className="stat-label">${runDetail.total_cost_usd.toFixed(4)}</span>
            )}
          </div>

          <h6 className="step-logs-title">{t('runs.stepLogs')}</h6>
          <Table size="sm" className="runs-table">
            <thead>
              <tr>
                <th>{t('runs.table.step')}</th>
                <th>{t('runs.table.type')}</th>
                <th>{t('runs.table.status')}</th>
                <th>{t('runs.table.duration')}</th>
                <th>{t('runs.table.tokens')}</th>
              </tr>
            </thead>
            <tbody>
              {(runDetail.step_logs || []).map((log, i) => (
                <tr key={i}>
                  <td>{log.step_id}</td>
                  <td>{log.step_type}</td>
                  <td><Badge bg={STATUS_COLORS[log.status] || 'secondary'}>{log.status}</Badge></td>
                  <td>{log.duration_ms ? `${log.duration_ms}ms` : '-'}</td>
                  <td>{log.tokens_used || '-'}</td>
                </tr>
              ))}
            </tbody>
          </Table>

          {runDetail.error && (
            <div style={{ marginTop: 12 }}>
              <h6 className="error-title">{t('runs.error')}</h6>
              <pre>{runDetail.error}</pre>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="d-flex gap-2 mb-3">
        <Form.Select size="sm" style={{ width: 150 }} value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="all">{t('runs.allStatus')}</option>
          <option value="running">{t('runs.running')}</option>
          <option value="completed">{t('runs.completed')}</option>
          <option value="failed">{t('runs.failed')}</option>
        </Form.Select>
        <button type="button" className="ap-btn-secondary ap-btn-sm" onClick={loadRuns}>
          <FiRefreshCw size={12} /> {t('runs.refresh')}
        </button>
      </div>

      {loading ? (
        <div className="text-center p-4"><Spinner /></div>
      ) : runs.length === 0 ? (
        <div className="ap-empty">
          <div className="ap-empty-text">{t('runs.noRuns')}</div>
        </div>
      ) : (
        <Table hover size="sm" className="runs-table">
          <thead>
            <tr>
              <th>{t('runs.table.workflow')}</th>
              <th>{t('runs.table.status')}</th>
              <th>{t('runs.table.trigger')}</th>
              <th>{t('runs.table.duration')}</th>
              <th>{t('runs.table.cost')}</th>
              <th>{t('runs.table.started')}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((run) => (
              <tr key={run.id} onClick={() => handleRunClick(run.id)}>
                <td>{run.workflow_name || '-'}</td>
                <td><Badge bg={STATUS_COLORS[run.status]}>{run.status}</Badge></td>
                <td>{run.trigger_type || '-'}</td>
                <td>{run.duration_ms ? `${(run.duration_ms / 1000).toFixed(1)}s` : '-'}</td>
                <td>{run.total_cost_usd ? `$${run.total_cost_usd.toFixed(4)}` : '-'}</td>
                <td>{run.started_at ? new Date(run.started_at).toLocaleString() : '-'}</td>
                <td>
                  <button
                    type="button"
                    className="ap-btn-ghost"
                    style={{ padding: 0 }}
                    onClick={(e) => { e.stopPropagation(); }}
                  >
                    <FiPlay size={10} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </>
  );
}
