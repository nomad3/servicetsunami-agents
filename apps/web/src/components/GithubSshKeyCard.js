import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Form, Spinner } from 'react-bootstrap';
import { FaCheckCircle, FaKey, FaSignOutAlt, FaSave } from 'react-icons/fa';
import integrationConfigService from '../services/integrationConfigService';

/**
 * SSH key section for the GitHub integration card (PR3, plan 2026-05-31).
 *
 * For repos behind orgs that block OAuth apps (e.g. NFL, SAML-SSO orgs) where
 * the OAuth/HTTPS path can't reach. The worker uses the key to
 * `git clone git@github.com:org/repo`.
 *
 * Security UX (per Luna, lead): a dedicated READ-ONLY deploy key is the
 * recommended path; a personal key is allowed with a clear warning. The private
 * key is sent once and never returned — status shows only a fingerprint.
 */
export default function GithubSshKeyCard() {
  const [status, setStatus] = useState({ present: false, fingerprint: null });
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [keyText, setKeyText] = useState('');
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await integrationConfigService.githubSshKeyStatus();
      setStatus(res.data || { present: false, fingerprint: null });
    } catch {
      setStatus({ present: false, fingerprint: null });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const handleSave = async () => {
    setError(null);
    if (!keyText.trim()) {
      setError('Paste an OpenSSH private key.');
      return;
    }
    setSaving(true);
    try {
      await integrationConfigService.githubSshKeySave(keyText);
      setKeyText('');
      setOpen(false);
      await refresh();
    } catch (e) {
      // The API rejects passphrase-protected / invalid keys with a 400 + detail.
      setError(e?.response?.data?.detail || 'Could not save the SSH key.');
    } finally {
      setSaving(false);
    }
  };

  const handleRemove = async () => {
    setRemoving(true);
    setError(null);
    try {
      await integrationConfigService.githubSshKeyDelete();
      await refresh();
    } catch {
      setError('Could not remove the SSH key.');
    } finally {
      setRemoving(false);
    }
  };

  const labelStyle = { fontSize: '0.85rem', fontWeight: 600, color: 'var(--color-foreground)' };
  const mutedStyle = { fontSize: '0.75rem', color: 'var(--text-muted)' };

  return (
    <div className="mt-3 pt-3" style={{ borderTop: '1px solid var(--color-border)' }}>
      <div className="d-flex align-items-center justify-content-between">
        <div className="d-flex align-items-center gap-2">
          <FaKey size={14} style={{ color: '#6e7681' }} />
          <div>
            <div style={labelStyle}>SSH key</div>
            <div style={mutedStyle}>For private repos in orgs that block OAuth apps.</div>
          </div>
        </div>
        {loading ? (
          <Spinner animation="border" size="sm" style={{ width: 14, height: 14, borderWidth: 1.5 }} />
        ) : status.present ? (
          <Button
            variant="outline-danger"
            size="sm"
            onClick={handleRemove}
            disabled={removing}
            style={{ fontSize: '0.78rem', padding: '4px 12px' }}
          >
            {removing
              ? <Spinner animation="border" size="sm" style={{ width: 12, height: 12, borderWidth: 1.5 }} className="me-1" />
              : <FaSignOutAlt className="me-1" size={10} />}
            Remove
          </Button>
        ) : (
          <Button
            size="sm"
            onClick={() => { setOpen((v) => !v); setError(null); }}
            style={{ fontSize: '0.82rem', padding: '5px 14px', background: '#24292e', color: '#fff', border: 'none', borderRadius: 6 }}
          >
            <FaKey className="me-1" size={11} /> Add SSH key
          </Button>
        )}
      </div>

      {status.present && !loading && (
        <div className="d-flex align-items-center gap-1 mt-2" style={{ fontSize: '0.74rem', color: '#2d9d78' }}>
          <FaCheckCircle size={9} />
          <span style={{ fontFamily: 'monospace' }}>{status.fingerprint || 'key configured'}</span>
        </div>
      )}

      {open && !status.present && (
        <div className="mt-3">
          <Alert variant="warning" style={{ fontSize: '0.76rem', padding: '8px 12px' }}>
            <strong>Use a dedicated, read-only deploy key</strong> for the target repo where possible.
            A personal key grants the worker the same repo access you have. The key must be
            <strong> passphrase-less</strong> (the worker runs non-interactively). It's encrypted at
            rest and only ever used to clone over SSH — it's never shown back to you.
          </Alert>
          <Form.Control
            as="textarea"
            rows={6}
            value={keyText}
            onChange={(e) => setKeyText(e.target.value)}
            placeholder={'-----BEGIN OPENSSH PRIVATE KEY-----\n...\n-----END OPENSSH PRIVATE KEY-----'}
            spellCheck={false}
            autoComplete="off"
            style={{ fontFamily: 'monospace', fontSize: '0.74rem' }}
          />
          {error && (
            <div className="mt-2" style={{ fontSize: '0.76rem', color: '#d9534f' }}>{error}</div>
          )}
          <div className="d-flex gap-2 mt-2">
            <Button size="sm" onClick={handleSave} disabled={saving} style={{ fontSize: '0.82rem' }}>
              {saving
                ? <Spinner animation="border" size="sm" style={{ width: 12, height: 12, borderWidth: 1.5 }} className="me-1" />
                : <FaSave className="me-1" size={11} />}
              Save key
            </Button>
            <Button variant="outline-secondary" size="sm" onClick={() => { setOpen(false); setKeyText(''); setError(null); }} style={{ fontSize: '0.82rem' }}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {error && !open && (
        <div className="mt-2" style={{ fontSize: '0.76rem', color: '#d9534f' }}>{error}</div>
      )}
    </div>
  );
}
