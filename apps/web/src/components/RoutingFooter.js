import { Badge, OverlayTrigger, Tooltip } from 'react-bootstrap';
import { FaExchangeAlt, FaExclamationTriangle, FaServer, FaShieldAlt } from 'react-icons/fa';
import { useTranslation } from 'react-i18next';

/**
 * Phase 3 commit 6 — resolve summary.actionable_hint via i18n with
 * fallback chain: specific platform key → generic key → English literal.
 * The actionable_hint is an i18n key shaped ``cli.errors.<status>[.<platform>]``
 * (see packages/cli_orchestrator/policy.py:_hint_key) — so we look up the
 * full key first, then fall back to the generic ``cli.errors.<status>``,
 * then to a hard-coded English literal. The cliErrors namespace uses
 * dot-keyed flat strings; we pass keySeparator: false on the t() call.
 */
const _GENERIC_HINT_FOR_KEY = (key) => {
  // key shape: cli.errors.<status>[.<platform>]
  // Drop the trailing platform segment if present.
  if (!key) return null;
  const parts = key.split('.');
  if (parts.length <= 3) return key; // already generic
  return parts.slice(0, 3).join('.');
};

const _ENGLISH_LITERAL_FOR_KEY = (key) => {
  if (!key) return null;
  if (key.includes('needs_auth')) return 'Reconnect this CLI in Settings → Integrations to keep using it.';
  if (key.includes('workspace_untrusted')) return 'This workspace isn\'t marked as trusted for the selected CLI.';
  if (key.includes('api_disabled')) return 'The selected CLI\'s cloud API isn\'t enabled for your account.';
  if (key.includes('quota_exhausted')) return 'Your CLI subscription hit its rate limit; we tried other CLIs in your chain.';
  if (key.includes('recursion_depth_exceeded')) return 'Agent fallback chain too deep — refusing to fan out further.';
  if (key.includes('recursion_cycle')) return 'Agent fallback chain has a cycle — refusing to repeat the same agent.';
  if (key.includes('unknown_failure')) return 'The CLI returned an unrecognised error.';
  return null;
};

/**
 * Resolve the actionable_hint key with platform → generic → literal fallback.
 * Exported for testing.
 */
export const resolveActionableHint = (t, hintKey) => {
  if (!hintKey) return null;
  const literal = _ENGLISH_LITERAL_FOR_KEY(hintKey);
  const genericKey = _GENERIC_HINT_FOR_KEY(hintKey);
  // Try platform-specific first, then generic, then English literal.
  // i18next: passing defaultValue cascades naturally; nest two t() calls.
  const opts = { keySeparator: false, ns: 'cliErrors' };
  const specific = t(hintKey, { ...opts, defaultValue: '__missing__' });
  if (specific !== '__missing__') return specific;
  if (genericKey && genericKey !== hintKey) {
    const generic = t(genericKey, { ...opts, defaultValue: '__missing__' });
    if (generic !== '__missing__') return generic;
  }
  return literal;
};

/**
 * One-line footer rendered under each assistant message showing what
 * actually served the response — "Served by GitHub Copilot CLI · 891
 * tokens · 14s". When a fallback fired, surfaces the original CLI and
 * a human-readable reason. When the chain exhausted entirely (no CLI
 * served), surfaces the failure with attribution so the customer
 * isn't left guessing what was tried.
 *
 * Backed by the curated ``routing_summary`` field on
 * ``ChatMessage.context`` — distinct from the raw ``cli_chain_attempted``
 * telemetry which deliberately stays in operator logs only (PR #245
 * review concern about exposing internal routing decisions).
 *
 * Renders nothing when ``routing_summary`` is absent (e.g. legacy
 * messages from before this feature).
 */
const RoutingFooter = ({ context }) => {
  const { t } = useTranslation('cliErrors');
  const { t: tChat } = useTranslation('chat');
  const summary = context?.routing_summary;
  if (!summary) return null;

  // Platform Safety Floor refusal (#665 PR 2). When served_by is the
  // platform_safety_block sentinel, render a distinct shield-badge
  // surface — NOT the generic "Served by X" footer. The category
  // label is read from i18n; the trigger_id is NEVER shown (it's
  // platform-admin only per the design § 9).
  if (summary.served_by === 'platform_safety_block') {
    const category = context?.safety_verdict?.category;
    const categoryLabel = category
      ? tChat(`platformSafetyBlock.category.${category}`, {
          defaultValue: category,
        })
      : tChat('platformSafetyBlock.subtitle');
    const title = tChat('platformSafetyBlock.title');
    const mistake = tChat('platformSafetyBlock.mistake');
    // (Review NIT-4) Fuller aria-label so screen-reader users get the
    // same context as hearing users — title + category + mistake hint.
    const safetyAriaLabel = `${title} — ${categoryLabel}. ${mistake}`;
    const safetyTip = (
      <Tooltip id="routing-tip-platform-safety">{mistake}</Tooltip>
    );
    return (
      <OverlayTrigger placement="top" overlay={safetyTip}>
        <div
          className="routing-footer mt-2 d-flex align-items-center gap-2"
          style={{
            fontSize: '0.72rem',
            color: 'var(--color-warn, #c98a16)',
            opacity: 0.95,
          }}
          tabIndex={0}
          role="group"
          aria-label={safetyAriaLabel}
          data-testid="routing-platform-safety-block"
        >
          <FaShieldAlt size={10} aria-hidden="true" />
          <span>
            {title} — <em>{categoryLabel}</em>
          </span>
          <Badge bg="warning" pill style={{ fontSize: '0.6rem' }}>
            {tChat('platformSafetyBlock.badge')}
          </Badge>
        </div>
      </OverlayTrigger>
    );
  }

  // Phase 3 commit 6 — surface the actionable_hint to the user.
  // hintKey is e.g. ``cli.errors.needs_auth.claude_code``; we resolve
  // with platform → generic → English-literal fallback.
  const actionableHintMsg = resolveActionableHint(
    t, summary?.actionable_hint || context?.actionable_hint,
  );

  const tokens = context?.tokens_used;
  const cost = context?.cost_usd;
  const apiMs = context?.api_duration_ms ?? context?.session_duration_ms;
  const fallbackFired = !!summary.fallback_reason && !!summary.requested && !summary.error_state;
  const exhausted = summary.error_state === 'exhausted';

  // Compose the metric tail (· tokens · cost · time) — only show
  // pieces that are actually present so we don't print "—" placeholders.
  // Forced en-US locale on toLocaleString so non-en-US CI runners don't
  // produce locale-dependent number formatting (M8 from review).
  const metrics = [];
  if (typeof tokens === 'number' && tokens > 0) {
    metrics.push(`${tokens.toLocaleString('en-US')} tokens`);
  }
  if (typeof cost === 'number' && cost > 0) {
    // 4 decimal places — typical Copilot turn is $0.01-0.05; rounding
    // to cents would always show $0.00.
    metrics.push(`$${cost.toFixed(4)}`);
  }
  if (typeof apiMs === 'number' && apiMs > 0) {
    metrics.push(`${(apiMs / 1000).toFixed(1)}s`);
  }
  const metricTail = metrics.length ? ` · ${metrics.join(' · ')}` : '';

  // M2: CLI / CLIs grammar branch.
  const cliWord = summary.chain_length === 1 ? 'CLI' : 'CLIs';

  // Tooltip shows the full chain length so curious users can see "the
  // resolver tried 2 CLIs to serve this turn" without it cluttering
  // the inline footer.
  let tooltipText;
  if (exhausted) {
    tooltipText =
      `Resolver tried ${summary.chain_length} ${cliWord} and none returned ` +
      `a successful response. Last attempted: ${summary.last_attempted || '—'}.`;
  } else if (fallbackFired) {
    tooltipText =
      `Resolver tried ${summary.chain_length} ${cliWord}. ` +
      `${summary.requested} returned ${summary.fallback_explanation}; ` +
      `${summary.served_by} served the response.`;
  } else {
    tooltipText =
      `Served by ${summary.served_by} on the first attempt. ` +
      `chain_length=${summary.chain_length}.`;
  }

  // Stable id derived from served_by_platform (or last_attempted_platform
  // for the exhausted state — no served platform). Fallback to "unknown"
  // so React-Bootstrap's OverlayTrigger always gets a defined id.
  const tipId = `routing-tip-${
    summary.served_by_platform || summary.last_attempted_platform || 'unknown'
  }`;
  const tip = <Tooltip id={tipId}>{tooltipText}</Tooltip>;

  // M1: tabIndex + role makes the footer keyboard-focusable so
  // screen-reader / keyboard-only users can reach the tooltip.
  const wrapperProps = {
    className: 'routing-footer mt-2 d-flex align-items-center gap-2',
    style: {
      fontSize: '0.72rem',
      color: 'var(--color-soft, #6b7785)',
      opacity: 0.85,
    },
    tabIndex: 0,
    role: 'group',
    'aria-label': tooltipText,
  };

  // Actionable hint — rendered as a soft annotation under the main
  // routing line on any branch where a hint is set. Phase 3 commit 6.
  const actionableNode = actionableHintMsg ? (
    <div
      data-testid="routing-actionable-hint"
      style={{
        fontSize: '0.7rem',
        color: 'var(--color-soft, #6b7785)',
        opacity: 0.9,
        marginTop: '0.15rem',
      }}
    >
      {actionableHintMsg}
    </div>
  ) : null;

  if (exhausted) {
    return (
      <OverlayTrigger placement="top" overlay={tip}>
        <div {...wrapperProps}>
          <FaExclamationTriangle size={10} aria-hidden="true" />
          <span>
            Tried <strong>{summary.last_attempted || summary.requested || '—'}</strong>
            {summary.chain_length > 1 ? ` (${summary.chain_length} ${cliWord})` : ''}
            {summary.fallback_explanation
              ? <> — last error: <em>{summary.fallback_explanation}</em></>
              : <> — no CLI served the response</>}
          </span>
          <Badge bg="danger" pill style={{ fontSize: '0.6rem' }}>
            chain exhausted
          </Badge>
          {actionableNode}
        </div>
      </OverlayTrigger>
    );
  }

  if (fallbackFired) {
    return (
      <OverlayTrigger placement="top" overlay={tip}>
        <div {...wrapperProps}>
          <FaExchangeAlt size={10} aria-hidden="true" />
          <span>
            Routed to <strong>{summary.served_by}</strong> after{' '}
            <span style={{ textDecoration: 'line-through', opacity: 0.7 }}>
              {summary.requested}
            </span>{' '}
            returned <em>{summary.fallback_explanation}</em>
            {metricTail}
          </span>
          <Badge bg="warning" pill style={{ fontSize: '0.6rem' }}>
            fallback
          </Badge>
          {actionableNode}
        </div>
      </OverlayTrigger>
    );
  }

  // Happy path
  return (
    <OverlayTrigger placement="top" overlay={tip}>
      <div {...wrapperProps}>
        <FaServer size={10} aria-hidden="true" />
        <span>
          Served by <strong>{summary.served_by}</strong>
          {metricTail}
        </span>
        {actionableNode}
      </div>
    </OverlayTrigger>
  );
};

export default RoutingFooter;
