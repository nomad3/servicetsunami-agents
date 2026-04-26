import React, { useMemo, useState } from 'react';
import { Alert, Badge, Button, Col, Form, Row, Spinner } from 'react-bootstrap';
import { FaCheck, FaPlug, FaSearch } from 'react-icons/fa';

import agentService from '../services/agent';

/**
 * Unified Hire flow (PR-D of the external-agents + A2A plan).
 *
 * Three steps:
 *   1. Capability search → /agents/discover (native + external).
 *   2. Source picker — when a capability has no matches, the user picks
 *      one of: register external endpoint, subscribe to marketplace
 *      listing, or import CrewAI / LangChain / AutoGen JSON. The
 *      existing AgentImporter parses the JSON server-side; we just
 *      forward it to /agents/import.
 *   3. Preview + Hire — for external endpoints, runs /test-task once;
 *      for marketplace, shows the listing + subscribes; for import,
 *      creates the native agent draft.
 *
 * Reuses the existing /external-agents, /agents/import, and
 * /agent-marketplace endpoints so the wizard is wiring only —
 * no backend duplication.
 */

const SOURCE_OPTIONS = [
  {
    key: 'mcp_sse',
    label: 'MCP-SSE endpoint',
    hint: 'Claude Code / Gemini / Cursor skills shipped as MCP-SSE servers.',
  },
  {
    key: 'openai_chat',
    label: 'OpenAI-compatible /chat',
    hint: 'OpenAI Assistants, locally-hosted Ollama servers, anything speaking the OpenAI chat protocol.',
  },
  {
    key: 'webhook',
    label: 'Webhook',
    hint: 'A plain HTTP endpoint that accepts POST /tasks {task, context} and returns JSON.',
  },
  {
    key: 'import',
    label: 'CrewAI / LangChain / AutoGen JSON',
    hint: 'Import an agent definition exported from another framework. AgentImporter detects the format.',
  },
  {
    key: 'marketplace',
    label: 'Marketplace listing',
    hint: 'Subscribe to a published agent from another tenant.',
  },
];

const HireAgentWizard = ({ onClose, onHired }) => {
  const [step, setStep] = useState(1);
  const [capability, setCapability] = useState('');
  const [searching, setSearching] = useState(false);
  const [discoverResults, setDiscoverResults] = useState([]);
  const [marketplace, setMarketplace] = useState([]);

  const [source, setSource] = useState('mcp_sse');
  const [form, setForm] = useState({
    name: '',
    description: '',
    endpoint_url: '',
    auth_type: 'bearer',
    capabilities: '',
    importContent: '',
    importFilename: '',
    listingId: '',
  });

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [hiredAgent, setHiredAgent] = useState(null);

  // ── Step 1 — capability search ─────────────────────────────────
  const handleSearch = async () => {
    if (!capability.trim()) return;
    setSearching(true);
    setError('');
    try {
      const [discover, listings] = await Promise.all([
        agentService.discover(capability.trim()),
        agentService.listMarketplace(capability.trim()).catch(() => ({ data: [] })),
      ]);
      setDiscoverResults(discover.data || []);
      setMarketplace(listings.data || []);
      setStep(2);
    } catch (err) {
      setError(err?.response?.data?.detail || 'Discovery failed.');
    } finally {
      setSearching(false);
    }
  };

  // ── Step 3 — actually hire ─────────────────────────────────────
  const submit = async () => {
    setSubmitting(true);
    setError('');
    try {
      let result;
      if (source === 'import') {
        if (!form.importContent.trim()) throw new Error('Paste or pick an agent JSON file first.');
        result = await agentService.importAgent(form.importContent, form.importFilename || 'agent.json');
        setHiredAgent({ kind: 'native', ...result.data });
      } else if (source === 'marketplace') {
        if (!form.listingId) throw new Error('Pick a marketplace listing.');
        result = await agentService.subscribeListing(form.listingId);
        setHiredAgent({ kind: 'subscription', ...result.data });
      } else {
        if (!form.name.trim() || !form.endpoint_url.trim()) {
          throw new Error('Name + endpoint URL are required.');
        }
        const caps = form.capabilities
          ? form.capabilities.split(',').map((s) => s.trim()).filter(Boolean)
          : [];
        const payload = {
          name: form.name.trim(),
          description: form.description.trim() || undefined,
          endpoint_url: form.endpoint_url.trim(),
          protocol: source,
          auth_type: form.auth_type,
          capabilities: caps,
        };
        result = await agentService.createExternal(payload);
        setHiredAgent({ kind: 'external', ...result.data });
      }
      onHired?.(result.data);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Hire failed.');
    } finally {
      setSubmitting(false);
    }
  };

  const setField = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  const handleFile = (evt) => {
    const file = evt.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      setField('importContent', String(e.target?.result || ''));
      setField('importFilename', file.name);
    };
    reader.readAsText(file);
  };

  const stepIndicator = useMemo(() => (
    <div className="d-flex gap-2 mb-3">
      {[1, 2, 3].map((n) => (
        <div
          key={n}
          style={{
            flex: 1, height: 4, borderRadius: 2,
            background: n <= step ? 'var(--ap-accent, #3b82f6)' : 'rgba(255,255,255,0.08)',
          }}
        />
      ))}
    </div>
  ), [step]);

  // ── Render by step ─────────────────────────────────────────────
  return (
    <div>
      {stepIndicator}
      {error && <Alert variant="danger" className="py-2">{error}</Alert>}

      {step === 1 && (
        <div>
          <h5 className="mb-3">What should this agent be able to do?</h5>
          <p className="text-muted small mb-3">
            Type a capability ("lead-scoring", "code-review", "data-analysis"). The wizard searches your
            tenant's native + external agents and any marketplace listings that declared it.
          </p>
          <Form.Group>
            <Form.Control
              type="text"
              placeholder="e.g. lead-scoring"
              value={capability}
              onChange={(e) => setCapability(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              autoFocus
            />
          </Form.Group>
          <div className="d-flex justify-content-end gap-2 mt-3">
            <Button variant="outline-secondary" onClick={onClose}>Cancel</Button>
            <Button variant="primary" onClick={handleSearch} disabled={searching || !capability.trim()}>
              {searching ? <Spinner size="sm" animation="border" /> : <><FaSearch className="me-2" />Search</>}
            </Button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div>
          <h5 className="mb-3">Pick a source</h5>
          {discoverResults.length > 0 && (
            <div className="mb-3">
              <div className="text-muted small mb-2">Already in your fleet ({discoverResults.length})</div>
              {discoverResults.map((r) => (
                <div key={r.id} className="d-flex justify-content-between align-items-center mb-2 p-2"
                     style={{ borderRadius: 6, background: 'rgba(255,255,255,0.04)' }}>
                  <div>
                    <strong>{r.name}</strong> <Badge bg={r.kind === 'native' ? 'primary' : 'info'} className="ms-2">{r.kind}</Badge>
                    <div className="small text-muted">{r.description}</div>
                  </div>
                  <Badge bg="secondary">{r.status}</Badge>
                </div>
              ))}
            </div>
          )}
          {marketplace.length > 0 && (
            <div className="mb-3">
              <div className="text-muted small mb-2">Marketplace ({marketplace.length})</div>
              {marketplace.map((m) => (
                <div key={m.id} className="d-flex justify-content-between align-items-center mb-2 p-2"
                     style={{ borderRadius: 6, background: 'rgba(255,255,255,0.04)' }}>
                  <div>
                    <strong>{m.name}</strong>
                    <div className="small text-muted">{m.description}</div>
                  </div>
                  <Button size="sm" variant="outline-primary"
                          onClick={() => { setSource('marketplace'); setField('listingId', m.id); setStep(3); }}>
                    Subscribe
                  </Button>
                </div>
              ))}
            </div>
          )}

          <div className="text-muted small mb-2">Or hire a new one</div>
          <Row className="g-2">
            {SOURCE_OPTIONS.filter((o) => o.key !== 'marketplace').map((o) => (
              <Col xs={12} md={6} key={o.key}>
                <button
                  type="button"
                  onClick={() => { setSource(o.key); setStep(3); }}
                  className="w-100 p-3 text-start"
                  style={{
                    borderRadius: 8,
                    background: source === o.key ? 'rgba(59,130,246,0.15)' : 'rgba(255,255,255,0.04)',
                    border: source === o.key ? '1px solid #3b82f6' : '1px solid transparent',
                  }}
                >
                  <div className="d-flex align-items-center gap-2">
                    <FaPlug />
                    <strong>{o.label}</strong>
                  </div>
                  <div className="small text-muted mt-1">{o.hint}</div>
                </button>
              </Col>
            ))}
          </Row>
          <div className="d-flex justify-content-between mt-3">
            <Button variant="outline-secondary" onClick={() => setStep(1)}>Back</Button>
            <Button variant="outline-secondary" onClick={onClose}>Cancel</Button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div>
          <h5 className="mb-3">Hire <Badge bg="info">{source}</Badge></h5>
          {source === 'import' && (
            <>
              <Form.Group className="mb-3">
                <Form.Label>Pick agent JSON file</Form.Label>
                <Form.Control type="file" accept=".json,application/json" onChange={handleFile} />
              </Form.Group>
              <Form.Group>
                <Form.Label>Or paste here</Form.Label>
                <Form.Control as="textarea" rows={10}
                              value={form.importContent}
                              onChange={(e) => setField('importContent', e.target.value)}
                              style={{ fontFamily: 'monospace', fontSize: '0.8rem' }} />
              </Form.Group>
            </>
          )}
          {source === 'marketplace' && (
            <Alert variant="info" className="py-2">
              You're subscribing to listing <code>{form.listingId}</code>. The publisher tenant must approve before the agent is usable.
            </Alert>
          )}
          {source !== 'import' && source !== 'marketplace' && (
            <>
              <Row className="g-2 mb-2">
                <Col xs={12} md={6}>
                  <Form.Group>
                    <Form.Label>Name</Form.Label>
                    <Form.Control value={form.name} onChange={(e) => setField('name', e.target.value)} />
                  </Form.Group>
                </Col>
                <Col xs={12} md={6}>
                  <Form.Group>
                    <Form.Label>Auth type</Form.Label>
                    <Form.Select value={form.auth_type} onChange={(e) => setField('auth_type', e.target.value)}>
                      <option value="bearer">Bearer</option>
                      <option value="api_key">API key</option>
                      <option value="hmac">HMAC</option>
                    </Form.Select>
                  </Form.Group>
                </Col>
              </Row>
              <Form.Group className="mb-2">
                <Form.Label>Endpoint URL</Form.Label>
                <Form.Control type="url" placeholder="https://example.com/sse"
                              value={form.endpoint_url}
                              onChange={(e) => setField('endpoint_url', e.target.value)} />
              </Form.Group>
              <Form.Group className="mb-2">
                <Form.Label>Description</Form.Label>
                <Form.Control as="textarea" rows={2}
                              value={form.description}
                              onChange={(e) => setField('description', e.target.value)} />
              </Form.Group>
              <Form.Group>
                <Form.Label>Capabilities (comma-separated)</Form.Label>
                <Form.Control placeholder="lead-scoring, code-review"
                              value={form.capabilities}
                              onChange={(e) => setField('capabilities', e.target.value)} />
              </Form.Group>
            </>
          )}
          {hiredAgent && (
            <Alert variant="success" className="mt-3">
              <FaCheck className="me-2" />Hired. <code>{hiredAgent.id || hiredAgent.subscription_id || '—'}</code>
            </Alert>
          )}
          <div className="d-flex justify-content-between mt-3">
            <Button variant="outline-secondary" onClick={() => setStep(2)}>Back</Button>
            <div className="d-flex gap-2">
              <Button variant="outline-secondary" onClick={onClose}>Close</Button>
              <Button variant="primary" onClick={submit} disabled={submitting || hiredAgent}>
                {submitting ? <Spinner size="sm" animation="border" /> : 'Hire'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default HireAgentWizard;
