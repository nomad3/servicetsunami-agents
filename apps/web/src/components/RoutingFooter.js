import { Badge, OverlayTrigger, Tooltip } from 'react-bootstrap';
import { FaExchangeAlt, FaServer } from 'react-icons/fa';

/**
 * One-line footer rendered under each assistant message showing what
 * actually served the response — "Served by GitHub Copilot CLI · 891
 * tokens · 14s". When a fallback fired, surfaces the original CLI and
 * a human-readable reason ("Routed to Copilot CLI after Claude Code
 * returned rate limit / quota exceeded").
 *
 * Backed by the curated ``routing_summary`` field on
 * ``ChatMessage.context`` — distinct from the raw ``cli_chain_attempted``
 * telemetry which deliberately stays in operator logs only (PR #245
 * review concern about exposing internal routing decisions).
 *
 * Renders nothing when ``routing_summary`` is absent (e.g. legacy
 * messages from before this feature, or local-path responses that
 * didn't stamp the field).
 */
const RoutingFooter = ({ context }) => {
  const summary = context?.routing_summary;
  if (!summary || !summary.served_by) return null;

  const tokens = context?.tokens_used;
  const cost = context?.cost_usd;
  const apiMs = context?.api_duration_ms ?? context?.session_duration_ms;
  const fallbackFired = !!summary.fallback_reason;

  // Compose the metric tail (· tokens · cost · time) — only show
  // pieces that are actually present so we don't print "—" placeholders.
  const metrics = [];
  if (typeof tokens === 'number' && tokens > 0) {
    metrics.push(`${tokens.toLocaleString()} tokens`);
  }
  if (typeof cost === 'number' && cost > 0) {
    // 4 decimal places — typical Copilot turn is $0.01-0.05; rounding
    // to cents would always show $0.00 which is misleading.
    metrics.push(`$${cost.toFixed(4)}`);
  }
  if (typeof apiMs === 'number' && apiMs > 0) {
    metrics.push(`${(apiMs / 1000).toFixed(1)}s`);
  }
  const metricTail = metrics.length ? ` · ${metrics.join(' · ')}` : '';

  // Tooltip shows the full chain length so curious users can see
  // "the resolver tried 2 CLIs to serve this turn" without it
  // cluttering the inline footer.
  const tooltipText = fallbackFired
    ? `Resolver tried ${summary.chain_length} CLI(s). ` +
      `${summary.requested} returned ${summary.fallback_explanation}; ` +
      `${summary.served_by} served the response.`
    : `Served by ${summary.served_by} on the first attempt. ` +
      `chain_length=${summary.chain_length}.`;

  const tip = (
    <Tooltip id={`routing-tip-${summary.served_by_platform}`}>{tooltipText}</Tooltip>
  );

  return (
    <OverlayTrigger placement="top" overlay={tip}>
      <div
        className="routing-footer mt-2 d-flex align-items-center gap-2"
        style={{
          fontSize: '0.72rem',
          color: 'var(--color-soft, #6b7785)',
          opacity: 0.85,
        }}
      >
        {fallbackFired ? (
          <>
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
          </>
        ) : (
          <>
            <FaServer size={10} aria-hidden="true" />
            <span>
              Served by <strong>{summary.served_by}</strong>
              {metricTail}
            </span>
          </>
        )}
      </div>
    </OverlayTrigger>
  );
};

export default RoutingFooter;
