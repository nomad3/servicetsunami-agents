import { Badge, OverlayTrigger, Tooltip } from 'react-bootstrap';
import { FaExchangeAlt, FaExclamationTriangle, FaServer } from 'react-icons/fa';

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
  const summary = context?.routing_summary;
  if (!summary) return null;

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
      </div>
    </OverlayTrigger>
  );
};

export default RoutingFooter;
