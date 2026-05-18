/*
 * InlineCliPicker — compact CLI-platform switcher for the chat thread
 * header.
 *
 * Surfaces the same `tenant_features.default_cli_platform` knob that the
 * Integrations page exposes via DefaultCliSelector, but as a small
 * inline select so users don't have to leave the active chat session
 * just to swap routers. Keeps the chat in flow.
 *
 * Scope is tenant-wide, not per-chat: the underlying knob is
 * `tenant_features.default_cli_platform`. The label + tooltip make
 * that explicit so a user toggling it from inside one chat doesn't
 * think they're only affecting the current thread.
 *
 * Dropdown filtering — we ask the backend on mount which CLIs the
 * tenant actually has connected (GET /integrations/connected-clis,
 * resolver-aligned), and only render those + Auto. The earlier
 * "no filtering" stance led to a UX cliff: a tenant who'd only
 * connected GitHub would still see Claude Code / Codex / Gemini
 * in the chat-header picker, pick one, then watch every turn
 * silently fall back to Auto via the resolver. The chat surface
 * doesn't have integration-polling context like DefaultCliSelector
 * does, so we lean on a dedicated endpoint instead of replicating
 * deriveConnectedClis here. On endpoint failure we fall back to
 * showing all four CLI options — better than locking the user out.
 */
import { useEffect, useId, useState } from 'react';
import { brandingService } from '../services/branding';
import integrationConfigService from '../services/integrationConfigService';
import './InlineCliPicker.css';

const AUTO_VALUE = '__auto__';
const CLI_OPTIONS = [
  { value: 'claude_code', label: 'Claude Code' },
  { value: 'codex', label: 'Codex' },
  { value: 'gemini_cli', label: 'Gemini CLI' },
  { value: 'copilot_cli', label: 'Copilot CLI' },
  { value: 'qwen_code', label: 'Qwen Code' },
  { value: 'kimi_k2', label: 'Kimi K2' },
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'glm', label: 'GLM (Zhipu)' },
];

// How long the "saved ✓" affordance stays on screen after a successful
// write. Matches the pattern used by DefaultCliSelector — short enough
// not to clutter the header but long enough for the user to notice.
const SAVED_FLASH_MS = 2000;

const InlineCliPicker = () => {
  const [current, setCurrent] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState(null);
  const [error, setError] = useState(null);
  // null = endpoint hasn't returned yet OR returned an error → show all
  // CLI_OPTIONS (the pre-PR-#517 behaviour). Empty array is a legit
  // "connected nothing yet" response and produces an Auto-only dropdown.
  const [connectedClis, setConnectedClis] = useState(null);

  // useId() gives a stable, unique id per mounted instance. Required
  // because the dashboard renders one InlineCliPicker per <ChatTab>,
  // and split-pane layouts mount multiple ChatTabs side-by-side. A
  // hardcoded id duplicated across instances breaks <label for=...>
  // click-to-focus on every instance after the first.
  const selectId = useId();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // Fire both requests in parallel — they're independent (features
      // = which CLI is selected, connected-clis = which to even offer).
      // Promise.allSettled so a 5xx on connected-clis doesn't take
      // out the picker entirely; we just fall back to all options.
      const [featuresResult, connectedResult] = await Promise.allSettled([
        brandingService.getFeatures(),
        integrationConfigService.listConnectedClis(),
      ]);
      if (cancelled) return;

      if (featuresResult.status === 'fulfilled') {
        setCurrent(featuresResult.value?.default_cli_platform || null);
      } else {
        // Surface the failure instead of swallowing it. Mirrors the
        // DefaultCliSelector pattern so the user has a recoverable
        // hint that something's off rather than a silently-stuck Auto.
        // eslint-disable-next-line no-console
        console.warn('InlineCliPicker: failed to load features', featuresResult.reason);
        setError('Could not load tenant default CLI');
      }

      if (connectedResult.status === 'fulfilled') {
        const list = connectedResult.value?.data?.connected;
        // Defensive: if the shape is unexpected, treat it like a
        // failure and keep null so the dropdown still shows everything.
        setConnectedClis(Array.isArray(list) ? list : null);
      } else {
        // eslint-disable-next-line no-console
        console.warn(
          'InlineCliPicker: failed to load connected CLIs, showing all options',
          connectedResult.reason,
        );
        setConnectedClis(null);
      }
      setLoaded(true);
    })();
    return () => { cancelled = true; };
  }, []);

  // Auto-expire the "Saved" flash so it doesn't linger past its welcome.
  useEffect(() => {
    if (!savedAt) return undefined;
    const t = setTimeout(() => setSavedAt(null), SAVED_FLASH_MS);
    return () => clearTimeout(t);
  }, [savedAt]);

  const handleChange = async (e) => {
    const value = e.target.value;
    const next = value === AUTO_VALUE ? null : value;
    setSaving(true);
    setError(null);
    try {
      await brandingService.updateFeatures({ default_cli_platform: next });
      setCurrent(next);
      setSavedAt(Date.now());
    } catch (_err) {
      setError('Save failed');
    } finally {
      setSaving(false);
    }
  };

  // Hide until we know the current default — avoids the select
  // flickering from Auto to the actual value on mount.
  if (!loaded) return null;

  const selectValue = current || AUTO_VALUE;
  // null → endpoint failed/pending: show every option so a 5xx never
  // strands the user. Array → filter to just the connected CLIs the
  // backend confirmed (opencode is intentionally excluded from the API
  // response — it's the routing floor, not a user-pickable target).
  //
  // ALWAYS include the tenant's stored ``current`` value even if it's
  // no longer in connectedClis. Otherwise the <select> value points at
  // a non-existent <option> and the browser silently displays the first
  // option (Auto) while React state still says claude_code — a hidden
  // mismatch where the user sees "Auto" but saves still write the old
  // CLI. The stale option is tagged "(disconnected)" so the user has a
  // visible hint that the choice is no longer wired up.
  const staleSet = new Set();
  const visibleOptions = connectedClis === null
    ? CLI_OPTIONS
    : CLI_OPTIONS.filter((opt) => {
        if (connectedClis.includes(opt.value)) return true;
        if (opt.value === current) {
          staleSet.add(opt.value);
          return true;
        }
        return false;
      });

  return (
    <div
      className="inline-cli-picker"
      title="Tenant default CLI — applies to every chat. Falls back to Auto if the picked CLI isn't connected."
    >
      <label htmlFor={selectId} className="inline-cli-picker-label">Tenant CLI</label>
      <select
        id={selectId}
        className="inline-cli-picker-select"
        value={selectValue}
        onChange={handleChange}
        disabled={saving}
        aria-label="Tenant default CLI platform"
      >
        <option value={AUTO_VALUE}>Auto</option>
        {visibleOptions.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {staleSet.has(opt.value) ? `${opt.label} (disconnected)` : opt.label}
          </option>
        ))}
      </select>
      {saving && <span className="inline-cli-picker-saving">…</span>}
      {!saving && savedAt && (
        <span className="inline-cli-picker-saved" aria-label="Saved">✓</span>
      )}
      {error && (
        <span className="inline-cli-picker-error" title={error}>
          {error}
        </span>
      )}
    </div>
  );
};

export default InlineCliPicker;
