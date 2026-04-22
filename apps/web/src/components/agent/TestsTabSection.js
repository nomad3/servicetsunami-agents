import { useEffect, useState } from 'react';
import { Spinner } from 'react-bootstrap';
import api from '../../services/api';

const TestsTabSection = ({ agentId }) => {
  const [cases, setCases] = useState([]);
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    name: '',
    input: '',
    expected_output_contains: '',
    expected_output_excludes: '',
    min_quality_score: 0.6,
    max_latency_ms: 10000,
  });
  const [error, setError] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const [cRes, rRes] = await Promise.all([
        api.get(`/agents/${agentId}/test-cases`),
        api.get(`/agents/${agentId}/test-runs`),
      ]);
      setCases(cRes.data || []);
      setRuns(rRes.data || []);
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load tests');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [agentId]); // eslint-disable-line react-hooks/exhaustive-deps

  const createCase = async (e) => {
    e.preventDefault();
    setError(null);
    const toList = (s) => s.split(',').map((t) => t.trim()).filter(Boolean);
    try {
      await api.post(`/agents/${agentId}/test-cases`, {
        name: form.name,
        input: form.input,
        expected_output_contains: toList(form.expected_output_contains),
        expected_output_excludes: toList(form.expected_output_excludes),
        min_quality_score: Number(form.min_quality_score),
        max_latency_ms: Number(form.max_latency_ms),
      });
      setShowForm(false);
      setForm({ name: '', input: '', expected_output_contains: '', expected_output_excludes: '', min_quality_score: 0.6, max_latency_ms: 10000 });
      load();
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to create test case');
    }
  };

  const deleteCase = async (caseId) => {
    if (!window.confirm('Delete this test case?')) return;
    await api.delete(`/agents/${agentId}/test-cases/${caseId}`);
    load();
  };

  const runAll = async () => {
    setRunning(true);
    setError(null);
    try {
      await api.post(`/agents/${agentId}/test`);
      await load();
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to run tests');
    } finally {
      setRunning(false);
    }
  };

  if (loading) {
    return (
      <div className="text-center py-4">
        <Spinner animation="border" size="sm" variant="primary" />
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <div className="ap-section-label">REGRESSION TESTS</div>
          <p style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)', margin: 0 }}>
            Promotion to production is blocked if any enabled case fails.
          </p>
        </div>
        <div className="d-flex gap-2">
          <button type="button" className="ap-btn-secondary ap-btn-sm" onClick={() => setShowForm((s) => !s)}>
            {showForm ? 'Cancel' : '+ Add Case'}
          </button>
          <button type="button" className="ap-btn-primary ap-btn-sm" onClick={runAll} disabled={running || cases.length === 0}>
            {running ? 'Running…' : 'Run All'}
          </button>
        </div>
      </div>

      {error && (
        <div className="ap-card" style={{ padding: 12, marginBottom: 12, color: 'var(--ap-danger)' }}>
          {String(error)}
        </div>
      )}

      {showForm && (
        <article className="ap-card" style={{ padding: 16, marginBottom: 16 }}>
          <form onSubmit={createCase}>
            <div className="row g-2">
              <div className="col-md-6">
                <label style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>Name</label>
                <input className="form-control form-control-sm" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
              </div>
              <div className="col-md-3">
                <label style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>Min quality</label>
                <input type="number" step="0.05" min="0" max="1" className="form-control form-control-sm" value={form.min_quality_score} onChange={(e) => setForm({ ...form, min_quality_score: e.target.value })} />
              </div>
              <div className="col-md-3">
                <label style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>Max latency (ms)</label>
                <input type="number" className="form-control form-control-sm" value={form.max_latency_ms} onChange={(e) => setForm({ ...form, max_latency_ms: e.target.value })} />
              </div>
              <div className="col-12">
                <label style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>Input prompt</label>
                <textarea className="form-control form-control-sm" rows={3} value={form.input} onChange={(e) => setForm({ ...form, input: e.target.value })} required />
              </div>
              <div className="col-md-6">
                <label style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>Must contain (comma-separated)</label>
                <input className="form-control form-control-sm" value={form.expected_output_contains} onChange={(e) => setForm({ ...form, expected_output_contains: e.target.value })} />
              </div>
              <div className="col-md-6">
                <label style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-muted)' }}>Must NOT contain (comma-separated)</label>
                <input className="form-control form-control-sm" value={form.expected_output_excludes} onChange={(e) => setForm({ ...form, expected_output_excludes: e.target.value })} />
              </div>
            </div>
            <div className="d-flex justify-content-end gap-2 mt-3">
              <button type="submit" className="ap-btn-primary ap-btn-sm">Create</button>
            </div>
          </form>
        </article>
      )}

      {cases.length === 0 ? (
        <div className="ap-empty">
          <h3 className="ap-empty-title">No test cases yet</h3>
          <p className="ap-empty-text">Add a case to set up a regression gate before promotion.</p>
        </div>
      ) : (
        <article className="ap-card" style={{ marginBottom: 24 }}>
          <table className="table table-sm mb-0" style={{ color: 'var(--ap-text)' }}>
            <thead>
              <tr style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-subtle)' }}>
                <th>Name</th>
                <th>Prompt</th>
                <th style={{ width: 90 }}>Min quality</th>
                <th style={{ width: 90 }}>Max ms</th>
                <th style={{ width: 60 }}></th>
              </tr>
            </thead>
            <tbody style={{ fontSize: 'var(--ap-fs-sm)' }}>
              {cases.map((c) => (
                <tr key={c.id}>
                  <td>{c.name}</td>
                  <td style={{ maxWidth: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.input}</td>
                  <td>{Number(c.min_quality_score).toFixed(2)}</td>
                  <td>{c.max_latency_ms}</td>
                  <td><button type="button" className="ap-btn-ghost ap-btn-sm" onClick={() => deleteCase(c.id)}>✕</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </article>
      )}

      <div className="ap-section-label">RECENT RUNS</div>
      {runs.length === 0 ? (
        <p style={{ fontSize: 'var(--ap-fs-sm)', color: 'var(--ap-text-muted)' }}>No runs yet.</p>
      ) : (
        <article className="ap-card">
          <table className="table table-sm mb-0" style={{ color: 'var(--ap-text)' }}>
            <thead>
              <tr style={{ fontSize: 'var(--ap-fs-xs)', color: 'var(--ap-text-subtle)' }}>
                <th>Created</th>
                <th>Type</th>
                <th>Status</th>
                <th>Passed</th>
                <th>Failed</th>
                <th>Version</th>
              </tr>
            </thead>
            <tbody style={{ fontSize: 'var(--ap-fs-sm)' }}>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td>{r.created_at ? new Date(r.created_at).toLocaleString() : ''}</td>
                  <td>{r.run_type}</td>
                  <td>
                    <span
                      className="ap-badge-solid"
                      style={{
                        background:
                          r.status === 'passed' ? 'var(--ap-success-tint)' :
                          r.status === 'failed' ? 'var(--ap-danger-tint)' :
                          r.status === 'running' ? 'var(--ap-warning-tint)' :
                          'var(--ap-border)',
                        color:
                          r.status === 'passed' ? 'var(--ap-success)' :
                          r.status === 'failed' ? 'var(--ap-danger)' :
                          r.status === 'running' ? 'var(--ap-warning)' :
                          'var(--ap-text-muted)',
                      }}
                    >
                      {r.status}
                    </span>
                  </td>
                  <td>{r.passed_count}</td>
                  <td>{r.failed_count}</td>
                  <td>v{r.agent_version || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </article>
      )}
    </div>
  );
};

export default TestsTabSection;
