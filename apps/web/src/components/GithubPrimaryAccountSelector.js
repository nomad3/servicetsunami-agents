import { useEffect, useMemo, useState } from 'react';
import { Alert, Form, Spinner } from 'react-bootstrap';
import { FaGithub } from 'react-icons/fa';

import { brandingService } from '../services/branding';

// Sentinel for the "Auto" option — sends null to the backend, which
// disables the pin and lets the MCP github tools fan out across all
// connected accounts.
const AUTO_VALUE = '__auto__';


/**
 * Pin one of the connected GitHub accounts as the default for
 * MCP repo operations. Renders only when the tenant has ≥2 GitHub
 * accounts connected — single-account tenants don't have a choice
 * to make.
 *
 * Backend: ``tenant_features.github_primary_account`` (migration 113).
 * When set, the resolver returns ONLY that account; useful for
 * tenants with one personal account + one Enterprise Managed User
 * account that has no repo visibility under enterprise OAuth-app
 * policy. The pin saves a wasted Graph round-trip per chat turn.
 */
const GithubPrimaryAccountSelector = ({ configs, credentialStatuses }) => {
  const [currentPrimary, setCurrentPrimary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [savedAt, setSavedAt] = useState(null);

  // Compute the list of connected GitHub accounts from the parent's
  // already-loaded configs — no extra API call. Mirrors the backend's
  // "connected" heuristic (enabled config with creds OR account_email).
  const githubAccounts = useMemo(() => {
    const accounts = [];
    (configs || []).forEach((cfg) => {
      if (!cfg || !cfg.enabled) return;
      if (cfg.integration_name !== 'github') return;
      const hasCreds = (credentialStatuses?.[cfg.integration_name] || []).length > 0;
      const hasAccount = !!cfg.account_email;
      if (hasCreds || hasAccount) {
        accounts.push(cfg.account_email);
      }
    });
    return accounts;
  }, [configs, credentialStatuses]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const features = await brandingService.getFeatures();
        if (!cancelled) {
          setCurrentPrimary(features?.github_primary_account || null);
          setLoading(false);
        }
      } catch {
        if (!cancelled) {
          setError('Could not load GitHub primary account preference.');
          setLoading(false);
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Don't render when fewer than 2 github accounts are connected —
  // the autodetect handles single-account tenants without any
  // user choice to make.
  if (loading) return null;
  if (githubAccounts.length < 2) return null;

  const handleChange = async (e) => {
    const value = e.target.value;
    const newPrimary = value === AUTO_VALUE ? null : value;
    setSaving(true);
    setError(null);
    try {
      await brandingService.updateFeatures({ github_primary_account: newPrimary });
      setCurrentPrimary(newPrimary);
      setSavedAt(Date.now());
    } catch (err) {
      // 403 here is the new admin-gated PUT /features (PR #249 review fix).
      // Surface a clear "admin only" message instead of the raw 403 text.
      if (err.response?.status === 403) {
        setError('Only admins can change tenant-wide GitHub preferences.');
      } else {
        setError('Could not save GitHub primary account. Please retry.');
      }
    } finally {
      setSaving(false);
    }
  };

  // Stale-pin guard: if `currentPrimary` references an account that's
  // no longer connected (admin disconnected after pinning), select
  // "Auto" rather than show a stale option.
  const selectValue = currentPrimary && githubAccounts.includes(currentPrimary)
    ? currentPrimary
    : AUTO_VALUE;

  return (
    <Alert
      variant="info"
      className="mb-3 d-flex align-items-center justify-content-between flex-wrap gap-2"
      style={{ fontSize: '0.85rem' }}
    >
      <div className="d-flex align-items-center gap-2 flex-grow-1">
        <FaGithub />
        <span>
          <strong>GitHub primary account</strong> — when multiple GitHub
          accounts are connected, this one is used by default for repo
          listings, file reads, and PR operations. Leave on Auto to fan
          out across all connected accounts.
        </span>
      </div>
      <div className="d-flex align-items-center gap-2">
        <Form.Select
          size="sm"
          value={selectValue}
          onChange={handleChange}
          disabled={saving}
          style={{ minWidth: 240 }}
          aria-label="GitHub primary account"
        >
          <option value={AUTO_VALUE}>Auto (fan-out across all)</option>
          {githubAccounts.map((email) => (
            <option key={email} value={email}>{email}</option>
          ))}
        </Form.Select>
        {saving && <Spinner animation="border" size="sm" />}
        {!saving && savedAt && !error && (
          <small className="text-success" role="status" aria-live="polite">Saved</small>
        )}
      </div>
      {error && (
        <div className="w-100 mt-2 text-danger">
          <small>{error}</small>
        </div>
      )}
    </Alert>
  );
};

export default GithubPrimaryAccountSelector;
