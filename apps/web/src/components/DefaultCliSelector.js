import { useEffect, useMemo, useState } from 'react';
import { Alert, Form, Spinner } from 'react-bootstrap';
import { FaInfoCircle } from 'react-icons/fa';

import { brandingService } from '../services/branding';

// Map CLI platform → integration_names whose presence proves the CLI
// can authenticate. Mirrors `_CLI_TO_INTEGRATIONS` in
// apps/api/app/services/cli_platform_resolver.py — keep them in sync.
const CLI_TO_INTEGRATIONS = {
  claude_code: ['claude_code'],
  copilot_cli: ['github'],
  codex: ['codex'],
  gemini_cli: ['gemini_cli', 'gmail', 'google_drive', 'google_calendar'],
};

const CLI_LABELS = {
  claude_code: 'Claude Code',
  copilot_cli: 'GitHub Copilot CLI',
  codex: 'Codex',
  gemini_cli: 'Gemini CLI',
};

// "Auto" sentinel value for the <select>. Sending null on PUT clears the
// tenant default so the backend resolver autodetects per chat turn.
const AUTO_VALUE = '__auto__';


/**
 * Derives which CLIs the tenant has connected based on the integration
 * config rows already loaded by the parent panel. Mirrors the backend's
 * `_connected_clis` logic so the UI doesn't need an extra API call.
 *
 * A config is considered "connected" if it has stored credentials OR an
 * `account_email` (OAuth-style integrations track presence by email
 * even when the token lives in the credential vault).
 */
function deriveConnectedClis(configs, credentialStatuses) {
  const connectedNames = new Set();
  (configs || []).forEach((cfg) => {
    if (!cfg || !cfg.enabled) return;
    const hasCreds = (credentialStatuses?.[cfg.integration_name] || []).length > 0;
    const hasAccount = !!cfg.account_email;
    if (hasCreds || hasAccount) connectedNames.add(cfg.integration_name);
  });

  const available = new Set();
  Object.entries(CLI_TO_INTEGRATIONS).forEach(([cli, integrations]) => {
    if (integrations.some((name) => connectedNames.has(name))) {
      available.add(cli);
    }
  });
  return available;
}


/**
 * Default-CLI selector. Renders ONLY when ≥2 CLIs are connected — when
 * 0 or 1 are connected, the backend autodetect handles routing without
 * any user choice to make. Hidden state is the right UX for the common
 * single-CLI tenant.
 */
const DefaultCliSelector = ({ configs, credentialStatuses }) => {
  const [currentDefault, setCurrentDefault] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [savedAt, setSavedAt] = useState(null);

  const connectedClis = useMemo(
    () => deriveConnectedClis(configs, credentialStatuses),
    [configs, credentialStatuses],
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const features = await brandingService.getFeatures();
        if (!cancelled) {
          setCurrentDefault(features?.default_cli_platform || null);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError('Could not load tenant default CLI');
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Don't render at all when fewer than 2 CLIs are connected — the
  // backend autodetect picks the only one available, no choice needed.
  if (loading) return null;
  if (connectedClis.size < 2) return null;

  const handleChange = async (e) => {
    const value = e.target.value;
    const newDefault = value === AUTO_VALUE ? null : value;
    setSaving(true);
    setError(null);
    try {
      await brandingService.updateFeatures({ default_cli_platform: newDefault });
      setCurrentDefault(newDefault);
      setSavedAt(Date.now());
    } catch (err) {
      setError('Could not save default CLI. Please retry.');
    } finally {
      setSaving(false);
    }
  };

  // Build options in the order the backend's chain priority uses, but
  // filtered to what's actually connected.
  const orderedOptions = ['claude_code', 'copilot_cli', 'gemini_cli', 'codex']
    .filter((cli) => connectedClis.has(cli));

  const selectValue = currentDefault && connectedClis.has(currentDefault)
    ? currentDefault
    : AUTO_VALUE;

  return (
    <Alert
      variant="info"
      className="mb-3 d-flex align-items-center justify-content-between flex-wrap gap-2"
      style={{ fontSize: '0.85rem' }}
    >
      <div className="d-flex align-items-center gap-2 flex-grow-1">
        <FaInfoCircle />
        <span>
          <strong>Default CLI</strong> — when multiple CLIs are connected,
          this is the one we route chats to first. Quota or auth failures
          fall over to the next available CLI automatically.
        </span>
      </div>
      <div className="d-flex align-items-center gap-2">
        <Form.Select
          size="sm"
          value={selectValue}
          onChange={handleChange}
          disabled={saving}
          style={{ minWidth: 200 }}
          aria-label="Default CLI platform"
        >
          <option value={AUTO_VALUE}>Auto (recommended)</option>
          {orderedOptions.map((cli) => (
            <option key={cli} value={cli}>{CLI_LABELS[cli]}</option>
          ))}
        </Form.Select>
        {saving && <Spinner animation="border" size="sm" />}
        {!saving && savedAt && (
          <small className="text-success">Saved</small>
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

export default DefaultCliSelector;
