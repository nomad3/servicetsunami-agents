import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Form,
  Modal,
  Spinner,
  Stack,
} from 'react-bootstrap';
import valuesService from '../../services/values';

// Operator-facing value-set editor for the Luna Value Layer (#647).
// Mounts inside AgentDetailPage as the "Values" tab.
//
// Three named lists (protect/pursue/avoid) edited in-place. Save writes a
// new append-only version. The "Open break-glass" modal opens a
// time-boxed override (1h default, 24h max) for incident response.
//
// Break-glass active banner: when the most-recent version has
// expires_at set + still in the future, surface a yellow banner with
// who opened it + when it expires.

const EMPTY = { slug: '', description: '' };

const ListEditor = ({ title, items, color, onChange }) => {
  const update = (idx, field, value) => {
    const next = items.slice();
    next[idx] = { ...next[idx], [field]: value };
    onChange(next);
  };
  const remove = (idx) => {
    const next = items.slice();
    next.splice(idx, 1);
    onChange(next);
  };
  const add = () => onChange([...items, { ...EMPTY }]);

  return (
    <Card className="mb-3">
      <Card.Header className="d-flex align-items-center justify-content-between">
        <strong>
          <Badge bg={color} className="me-2">{items.length}</Badge>
          {title}
        </strong>
        <Button size="sm" variant="outline-primary" onClick={add}>
          + Add
        </Button>
      </Card.Header>
      <Card.Body>
        {items.length === 0 && (
          <p className="text-muted small mb-0">No {title.toLowerCase()} items.</p>
        )}
        {items.map((item, idx) => (
          <Stack direction="horizontal" gap={2} key={idx} className="mb-2 align-items-start">
            <Form.Control
              placeholder="slug (e.g. production-main)"
              value={item.slug || ''}
              onChange={(e) => update(idx, 'slug', e.target.value)}
              style={{ maxWidth: '240px' }}
              maxLength={80}
            />
            <Form.Control
              placeholder="description (operator-visible reason)"
              value={item.description || ''}
              onChange={(e) => update(idx, 'description', e.target.value)}
              maxLength={400}
            />
            <Button
              size="sm"
              variant="outline-danger"
              onClick={() => remove(idx)}
              aria-label={`remove ${item.slug || 'item'}`}
            >
              ×
            </Button>
          </Stack>
        ))}
      </Card.Body>
    </Card>
  );
};

const BreakGlassBanner = ({ valueSet }) => {
  if (!valueSet?.expires_at) return null;
  const expiresAtMs = Date.parse(valueSet.expires_at);
  if (Number.isNaN(expiresAtMs)) return null;
  const stillActive = expiresAtMs > Date.now();
  if (!stillActive) return null;

  return (
    <Alert variant="warning" className="mb-3">
      <Alert.Heading className="h6">
        Break-glass override active
      </Alert.Heading>
      <div className="small">
        Opened by operator <code>{valueSet.break_glass_operator_id || '(unknown)'}</code>.
        Expires at <strong>{new Date(expiresAtMs).toLocaleString()}</strong>.
        {valueSet.break_glass_reason && (
          <>
            {' '}Reason: <em>{valueSet.break_glass_reason}</em>.
          </>
        )}
      </div>
      <div className="small text-muted mt-1">
        Until expiry, the protect/avoid items below are the relaxed set.
        After expiry, the prior ordinary version automatically takes over —
        no action needed.{' '}
        <strong>Saving here writes a new ordinary version and supersedes
        the active override</strong> (the next read will pick up your
        ordinary version, not this break-glass one).
      </div>
    </Alert>
  );
};

const BreakGlassModal = ({ show, onClose, valueSet, onSubmit, submitting }) => {
  const [reason, setReason] = useState('');
  const [hours, setHours] = useState(1);
  const [keepProtect, setKeepProtect] = useState({});
  const [keepAvoid, setKeepAvoid] = useState({});

  // Reset on open
  useEffect(() => {
    if (show) {
      setReason('');
      setHours(1);
      setKeepProtect({});
      setKeepAvoid({});
    }
  }, [show]);

  const submit = (e) => {
    e.preventDefault();
    const keepProtectSlugs = Object.keys(keepProtect).filter((k) => keepProtect[k]);
    const keepAvoidSlugs = Object.keys(keepAvoid).filter((k) => keepAvoid[k]);
    const durationSeconds = Math.max(60, Math.min(24 * 3600, Math.round(hours * 3600)));
    onSubmit({
      reason: reason.trim(),
      duration_seconds: durationSeconds,
      keep_protect_slugs: keepProtectSlugs,
      keep_avoid_slugs: keepAvoidSlugs,
    });
  };

  return (
    <Modal show={show} onHide={onClose} centered size="lg">
      <Form onSubmit={submit}>
        <Modal.Header closeButton>
          <Modal.Title>Open break-glass</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          <Alert variant="info" className="small">
            A break-glass override writes a new value-set version with the
            protect/avoid items you DROP. The override auto-expires after the
            chosen duration — no follow-up cleanup needed.
            One audit-log entry is recorded per use.
          </Alert>
          <Form.Group className="mb-3">
            <Form.Label>Reason <span className="text-danger">*</span></Form.Label>
            <Form.Control
              as="textarea"
              rows={2}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={500}
              required
              placeholder="e.g. production incident #1234"
            />
          </Form.Group>
          <Form.Group className="mb-3">
            <Form.Label>Duration (hours)</Form.Label>
            <Form.Control
              type="number"
              min={0.02} max={24} step={0.5}
              value={hours}
              onChange={(e) => setHours(Number(e.target.value))}
            />
            <Form.Text className="text-muted">
              Min ~1min, max 24h. Default 1h.
            </Form.Text>
          </Form.Group>
          {valueSet?.protect?.length > 0 && (
            <Form.Group className="mb-3">
              <Form.Label>Keep these protect items</Form.Label>
              <div className="small text-muted mb-1">
                Unchecked items are DROPPED for the duration.
              </div>
              {valueSet.protect.map((item) => (
                <Form.Check
                  key={item.slug}
                  type="checkbox"
                  id={`keep-protect-${item.slug}`}
                  label={<><code>{item.slug}</code> — {item.description}</>}
                  checked={!!keepProtect[item.slug]}
                  onChange={(e) =>
                    setKeepProtect({ ...keepProtect, [item.slug]: e.target.checked })
                  }
                />
              ))}
            </Form.Group>
          )}
          {valueSet?.avoid?.length > 0 && (
            <Form.Group className="mb-3">
              <Form.Label>Keep these avoid items</Form.Label>
              {valueSet.avoid.map((item) => (
                <Form.Check
                  key={item.slug}
                  type="checkbox"
                  id={`keep-avoid-${item.slug}`}
                  label={<><code>{item.slug}</code> — {item.description}</>}
                  checked={!!keepAvoid[item.slug]}
                  onChange={(e) =>
                    setKeepAvoid({ ...keepAvoid, [item.slug]: e.target.checked })
                  }
                />
              ))}
            </Form.Group>
          )}
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button
            variant="warning"
            type="submit"
            disabled={!reason.trim() || submitting}
          >
            {submitting ? 'Opening…' : 'Open break-glass'}
          </Button>
        </Modal.Footer>
      </Form>
    </Modal>
  );
};

const ValueSetTabSection = ({ agentId }) => {
  const [valueSet, setValueSet] = useState(null);
  const [draft, setDraft] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [info, setInfo] = useState(null);
  const [showBreakGlass, setShowBreakGlass] = useState(false);
  const [breakGlassSubmitting, setBreakGlassSubmitting] = useState(false);
  const [killSwitchEnabled, setKillSwitchEnabled] = useState(null); // null = loading
  const [killSwitchFlipping, setKillSwitchFlipping] = useState(false);

  // (Review NIT-3) Friendlier error mapping. Surface short status-coded
  // messages instead of forwarding raw backend prose; the FastAPI 503
  // detail string is operator-noisy.
  const friendlyError = (err, fallback) => {
    const status = err?.response?.status;
    const detail = err?.response?.data?.detail;
    if (status === 401) return 'Session expired — please sign in again.';
    if (status === 403) return "You don't have permission to change this agent's values.";
    if (status === 404) return 'Agent not found (or in another tenant).';
    if (status === 422) return typeof detail === 'string' ? detail : 'Request was rejected as invalid.';
    if (status === 503) return 'The server is busy — please retry in a moment.';
    if (typeof detail === 'string' && detail.length <= 200) return detail;
    return fallback || err?.message || 'Request failed.';
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await valuesService.getForAgent(agentId);
      setValueSet(res.data);
      setDraft({
        protect: res.data.protect.map((i) => ({ slug: i.slug, description: i.description })),
        pursue: res.data.pursue.map((i) => ({ slug: i.slug, description: i.description })),
        avoid: res.data.avoid.map((i) => ({ slug: i.slug, description: i.description })),
      });
    } catch (err) {
      setError(friendlyError(err, 'Failed to load values.'));
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    if (agentId) load();
  }, [agentId, load]);

  // Read the tenant-wide kill-switch. Independent of the value-set
  // read so a 5xx on /features doesn't block the editor.
  // (Review NIT-1) Distinguish 401 (session expired) from other errors.
  // A 401 leaving the switch as `false` would invite an immediate flip
  // that re-fails; clearer to surface the session issue and leave the
  // switch in the loading/unknown state until the next render.
  const loadKillSwitch = useCallback(async () => {
    try {
      const res = await valuesService.getFeatures();
      setKillSwitchEnabled(!!res.data?.value_layer_enabled);
    } catch (err) {
      if (err?.response?.status === 401) {
        setError('Session expired — please sign in again.');
        // Leave killSwitchEnabled as-is (null on first load, prior
        // value on a re-read). The switch stays disabled because
        // null short-circuits in the render.
      } else {
        // Non-auth failure — default the UI to OFF so the editor stays
        // usable. Operators can still edit values; the kill-switch
        // shape is independent.
        setKillSwitchEnabled(false);
      }
    }
  }, []);

  useEffect(() => {
    loadKillSwitch();
  }, [loadKillSwitch]);

  const handleKillSwitchFlip = async (nextEnabled) => {
    setKillSwitchFlipping(true);
    setError(null);
    setInfo(null);
    try {
      const res = await valuesService.setValueLayerEnabled(nextEnabled);
      const persisted = !!res.data?.value_layer_enabled;
      setKillSwitchEnabled(persisted);
      if (persisted !== nextEnabled) {
        // Backend silently dropped the field — caller isn't superuser.
        setError(
          'Only a superuser can change the tenant kill-switch. ' +
            'Your request was dropped server-side.'
        );
      } else {
        setInfo(
          nextEnabled
            ? 'Value layer is now ACTIVE for this tenant.'
            : 'Value layer is now OFF (inert) for this tenant.'
        );
      }
    } catch (err) {
      setError(friendlyError(err, 'Could not flip kill-switch.'));
      loadKillSwitch();
    } finally {
      setKillSwitchFlipping(false);
    }
  };

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    setError(null);
    setInfo(null);
    try {
      // Drop blank-slug items before posting; the API rejects them but
      // surfacing locally is faster feedback.
      const clean = {
        protect: draft.protect.filter((i) => i.slug?.trim()),
        pursue: draft.pursue.filter((i) => i.slug?.trim()),
        avoid: draft.avoid.filter((i) => i.slug?.trim()),
      };
      const res = await valuesService.putForAgent(agentId, clean);
      setValueSet(res.data);
      setInfo(`Saved version ${res.data.version}`);
    } catch (err) {
      setError(friendlyError(err, 'Save failed.'));
    } finally {
      setSaving(false);
    }
  };

  const handleBreakGlass = async (body) => {
    setBreakGlassSubmitting(true);
    setError(null);
    setInfo(null);
    try {
      const res = await valuesService.breakGlassForAgent(agentId, body);
      setValueSet(res.data);
      setDraft({
        protect: res.data.protect.map((i) => ({ slug: i.slug, description: i.description })),
        pursue: res.data.pursue.map((i) => ({ slug: i.slug, description: i.description })),
        avoid: res.data.avoid.map((i) => ({ slug: i.slug, description: i.description })),
      });
      setShowBreakGlass(false);
      setInfo(
        `Break-glass opened — version ${res.data.version}, expires ` +
          new Date(res.data.expires_at).toLocaleString()
      );
    } catch (err) {
      setError(friendlyError(err, 'Break-glass failed.'));
    } finally {
      setBreakGlassSubmitting(false);
    }
  };

  const hasChanges = useMemo(() => {
    if (!valueSet || !draft) return false;
    // (Review NIT-1) Sort by slug before serializing. The backend doesn't
    // promise stable item ordering across writes; a raw JSON.stringify
    // would flag a no-op save as dirty if the API ever returned items in
    // a different order. Slug is a stable canonical key per item.
    const canonicalize = (items) =>
      items
        .map((i) => ({ slug: (i.slug || '').trim().toLowerCase(), description: i.description || '' }))
        .sort((a, b) => a.slug.localeCompare(b.slug));
    const draftCanonical = {
      protect: canonicalize(draft.protect),
      pursue: canonicalize(draft.pursue),
      avoid: canonicalize(draft.avoid),
    };
    const persistedCanonical = {
      protect: canonicalize(valueSet.protect),
      pursue: canonicalize(valueSet.pursue),
      avoid: canonicalize(valueSet.avoid),
    };
    return JSON.stringify(draftCanonical) !== JSON.stringify(persistedCanonical);
  }, [valueSet, draft]);

  if (loading) {
    return (
      <div className="p-4 text-center">
        <Spinner animation="border" size="sm" /> Loading value set…
      </div>
    );
  }

  return (
    <div>
      {/* Tenant-wide kill-switch row. value_layer_enabled gates whether
          ANY of the 5 consultation points (routing/tool/reflection/
          user_signal/synthesis) actually fire — default OFF. */}
      <Alert variant={killSwitchEnabled ? 'success' : 'secondary'} className="d-flex align-items-center justify-content-between mb-3">
        <div>
          <strong>Tenant kill-switch</strong>:{' '}
          {killSwitchEnabled === null ? (
            <span className="text-muted">loading…</span>
          ) : killSwitchEnabled ? (
            <span><Badge bg="success">ACTIVE</Badge> blocks + warnings fire on chat turns matching your protect/avoid slugs.</span>
          ) : (
            <span><Badge bg="secondary">OFF</Badge> code is shipped but inert. Flip ON to start enforcing the value set.</span>
          )}
        </div>
        <div className="d-flex align-items-center">
          {killSwitchFlipping && (
            <Spinner
              animation="border"
              size="sm"
              className="me-2"
              aria-label="flipping kill-switch"
            />
          )}
          <Form.Check
            type="switch"
            id="value-layer-killswitch"
            checked={!!killSwitchEnabled}
            onChange={(e) => handleKillSwitchFlip(e.target.checked)}
            disabled={killSwitchFlipping || killSwitchEnabled === null}
            aria-label="toggle value layer kill-switch"
          />
        </div>
      </Alert>

      <BreakGlassBanner valueSet={valueSet} />

      <div className="d-flex align-items-center justify-content-between mb-3">
        <div>
          <strong>Value set</strong>{' '}
          <Badge bg="secondary">version {valueSet?.version ?? '—'}</Badge>{' '}
          <small className="text-muted">
            updated {valueSet?.updated_at ? new Date(valueSet.updated_at).toLocaleString() : 'never'}
          </small>
        </div>
        <div>
          <Button
            variant="outline-warning"
            size="sm"
            className="me-2"
            onClick={() => setShowBreakGlass(true)}
            disabled={saving || breakGlassSubmitting || killSwitchFlipping}
            title={
              saving
                ? 'Save in flight — wait for it to finish'
                : 'Open a time-boxed override'
            }
          >
            Open break-glass
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={handleSave}
            disabled={!hasChanges || saving || breakGlassSubmitting || killSwitchFlipping}
            title={
              breakGlassSubmitting
                ? 'Break-glass in flight — wait for it to finish'
                : undefined
            }
          >
            {saving ? 'Saving…' : 'Save (new version)'}
          </Button>
        </div>
      </div>

      {error && <Alert variant="danger">{error}</Alert>}
      {info && <Alert variant="success">{info}</Alert>}

      <Alert variant="light" className="small mb-3">
        <strong>protect</strong> — actions on these get blocked (mutation) or warned (mention).{' '}
        <strong>pursue</strong> — surfacing these in chat amplifies positive affect (1.5x).{' '}
        <strong>avoid</strong> — Luna gets a soft warning, no block.{' '}
        Empty value set = nothing is enforced. Kill-switch is per-tenant on the backend.
      </Alert>

      {draft && (
        <>
          <ListEditor
            title="Protect"
            color="danger"
            items={draft.protect}
            onChange={(items) => setDraft({ ...draft, protect: items })}
          />
          <ListEditor
            title="Pursue"
            color="success"
            items={draft.pursue}
            onChange={(items) => setDraft({ ...draft, pursue: items })}
          />
          <ListEditor
            title="Avoid"
            color="warning"
            items={draft.avoid}
            onChange={(items) => setDraft({ ...draft, avoid: items })}
          />
        </>
      )}

      <BreakGlassModal
        show={showBreakGlass}
        onClose={() => setShowBreakGlass(false)}
        valueSet={valueSet}
        onSubmit={handleBreakGlass}
        submitting={breakGlassSubmitting}
      />
    </div>
  );
};

export default ValueSetTabSection;
