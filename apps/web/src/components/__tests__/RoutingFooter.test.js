import { render, screen } from '@testing-library/react';
import RoutingFooter from '../RoutingFooter';


describe('RoutingFooter', () => {
  test('renders nothing when context is missing', () => {
    const { container } = render(<RoutingFooter context={null} />);
    expect(container.firstChild).toBeNull();
  });

  test('renders nothing when routing_summary is absent', () => {
    const { container } = render(
      <RoutingFooter context={{ tokens_used: 891, platform: 'copilot_cli' }} />,
    );
    expect(container.firstChild).toBeNull();
  });

  test('renders the simple "Served by" line on the happy path', () => {
    const ctx = {
      tokens_used: 891,
      cost_usd: 0.0123,
      api_duration_ms: 14200,
      routing_summary: {
        served_by: 'GitHub Copilot CLI',
        served_by_platform: 'copilot_cli',
        chain_length: 1,
      },
    };
    render(<RoutingFooter context={ctx} />);
    expect(screen.getByText(/Served by/)).toBeInTheDocument();
    expect(screen.getByText('GitHub Copilot CLI')).toBeInTheDocument();
    expect(screen.getByText(/891 tokens/)).toBeInTheDocument();
    expect(screen.getByText(/\$0\.0123/)).toBeInTheDocument();
    expect(screen.getByText(/14\.2s/)).toBeInTheDocument();
  });

  test('renders the fallback line with reason when fallback fired', () => {
    const ctx = {
      tokens_used: 1268,
      cost_usd: 0.0185,
      api_duration_ms: 16100,
      routing_summary: {
        served_by: 'GitHub Copilot CLI',
        served_by_platform: 'copilot_cli',
        requested: 'Claude Code',
        requested_platform: 'claude_code',
        fallback_reason: 'quota',
        fallback_explanation: 'rate limit / quota exceeded',
        chain_length: 2,
      },
    };
    render(<RoutingFooter context={ctx} />);
    expect(screen.getByText(/Routed to/)).toBeInTheDocument();
    expect(screen.getByText('GitHub Copilot CLI')).toBeInTheDocument();
    expect(screen.getByText('Claude Code')).toBeInTheDocument();
    expect(screen.getByText(/rate limit \/ quota exceeded/)).toBeInTheDocument();
    expect(screen.getByText('fallback')).toBeInTheDocument();
  });

  test('omits zero or missing metrics gracefully', () => {
    const ctx = {
      // No tokens, no cost, no time — only the served_by part
      routing_summary: {
        served_by: 'OpenCode (local)',
        served_by_platform: 'opencode',
        chain_length: 1,
      },
    };
    render(<RoutingFooter context={ctx} />);
    expect(screen.getByText('OpenCode (local)')).toBeInTheDocument();
    // No "·" separators visible since metrics array was empty
    expect(screen.queryByText(/tokens/)).toBeNull();
    expect(screen.queryByText(/\$/)).toBeNull();
    expect(screen.queryByText(/[\d.]+s$/)).toBeNull();
  });

  test('does not render raw chain even if backend wrongly sends it', () => {
    // I3 frontend defense: if a future regression dumps `cli_chain_attempted`
    // into context (the PR #245 leak the backend tests guard against),
    // the footer must NOT echo it. Forbidden keys passed in context
    // should be ignored — we only render fields explicitly read from
    // routing_summary.
    const ctx = {
      tokens_used: 891,
      cli_chain_attempted: ['claude_code', 'codex', 'copilot_cli'],
      attempted: ['internal-leak'],
      chain: 'should-not-render',
      routing_summary: {
        served_by: 'GitHub Copilot CLI',
        served_by_platform: 'copilot_cli',
        chain_length: 1,
      },
    };
    render(<RoutingFooter context={ctx} />);
    // Forbidden values from the leak attempt must not appear anywhere
    expect(screen.queryByText(/claude_code, codex/)).toBeNull();
    expect(screen.queryByText('internal-leak')).toBeNull();
    expect(screen.queryByText(/should-not-render/)).toBeNull();
  });

  test('renders chain-exhausted footer when no CLI served', () => {
    // C2 frontend rendering: when error_state="exhausted" the footer
    // should surface the failure with attribution rather than silently
    // disappear (which is what happened pre-fix and left customers
    // staring at an unattributed error).
    const ctx = {
      routing_summary: {
        served_by: '—',
        served_by_platform: null,
        chain_length: 3,
        error_state: 'exhausted',
        last_attempted_platform: 'opencode',
        last_attempted: 'OpenCode (local)',
        fallback_reason: 'quota',
        fallback_explanation: 'rate limit / quota exceeded',
      },
    };
    render(<RoutingFooter context={ctx} />);
    expect(screen.getByText(/Tried/)).toBeInTheDocument();
    expect(screen.getByText('OpenCode (local)')).toBeInTheDocument();
    expect(screen.getByText('chain exhausted')).toBeInTheDocument();
    expect(screen.getByText(/rate limit \/ quota exceeded/)).toBeInTheDocument();
  });

  test('uses correct CLI/CLIs grammar in tooltip aria-label', () => {
    // M2: chain_length=1 → "CLI", chain_length>1 → "CLIs". The
    // aria-label mirrors the tooltip text, which is the easiest
    // surface to assert against.
    const single = {
      routing_summary: {
        served_by: 'Claude Code',
        served_by_platform: 'claude_code',
        chain_length: 1,
      },
    };
    const { rerender } = render(<RoutingFooter context={single} />);
    expect(screen.getByRole('group')).toHaveAttribute(
      'aria-label',
      expect.stringMatching(/chain_length=1\.$/),
    );

    const multi = {
      routing_summary: {
        served_by: 'Claude Code',
        served_by_platform: 'claude_code',
        requested: 'GitHub Copilot CLI',
        requested_platform: 'copilot_cli',
        fallback_reason: 'quota',
        fallback_explanation: 'rate limit / quota exceeded',
        chain_length: 2,
      },
    };
    rerender(<RoutingFooter context={multi} />);
    expect(screen.getByRole('group')).toHaveAttribute(
      'aria-label',
      expect.stringMatching(/2 CLIs/),
    );
  });

  test('respects per-message context — different messages render different footers', () => {
    const fallbackCtx = {
      tokens_used: 1000,
      routing_summary: {
        served_by: 'Codex CLI',
        served_by_platform: 'codex',
        requested: 'GitHub Copilot CLI',
        requested_platform: 'copilot_cli',
        fallback_reason: 'auth',
        fallback_explanation: 'authentication failed',
        chain_length: 2,
      },
    };
    const { rerender } = render(<RoutingFooter context={fallbackCtx} />);
    expect(screen.getByText(/Routed to/)).toBeInTheDocument();
    expect(screen.getByText('Codex CLI')).toBeInTheDocument();

    const happyCtx = {
      tokens_used: 500,
      routing_summary: {
        served_by: 'Gemini CLI',
        served_by_platform: 'gemini_cli',
        chain_length: 1,
      },
    };
    rerender(<RoutingFooter context={happyCtx} />);
    expect(screen.getByText(/Served by/)).toBeInTheDocument();
    expect(screen.getByText('Gemini CLI')).toBeInTheDocument();
  });
});


// ── Platform Safety Floor branch (PR 2 of safety floor sequence) ─────

describe('RoutingFooter / Platform Safety Floor refusal', () => {
  test('renders shield + category label when served_by is platform_safety_block', () => {
    const ctx = {
      routing_summary: {
        served_by: 'platform_safety_block',
        requested: null,
        chain_length: 0,
        fallback_reason: 'category:mass_harm_synthesis tier:1',
      },
      safety_verdict: {
        decision: 'block',
        category: 'mass_harm_synthesis',
        detection_tier: 1,
        // NOTE: deliberately NO trigger_id here — PR 1 scrubbed it
        // from the client-visible to_dict(). The next test
        // enforces that a leaked trigger_id never renders.
      },
    };
    render(<RoutingFooter context={ctx} />);
    // The discriminator testid is the load-bearing assertion. The
    // i18n-resolved labels depend on locale loading which isn't
    // mocked in this test setup (matches existing tests like the
    // happy-path one above which check raw `served_by` strings, not
    // translated labels — that's the convention here).
    const banner = screen.getByTestId('routing-platform-safety-block');
    expect(banner).toBeInTheDocument();
    // Category surfaces somewhere in the banner (key passthrough
    // when i18next returns the key; production loads the
    // translation).
    expect(banner.textContent).toMatch(
      /mass_harm_synthesis|mass-harm/,
    );
  });

  test('trigger_id never renders even if backend leaks it in safety_verdict', () => {
    // Regression guard for the PR 1 IMPORTANT-1 fix. If a future
    // backend change accidentally puts trigger_id into the
    // client-visible to_dict(), this test catches it: the
    // RoutingFooter must NOT include the trigger string anywhere in
    // its DOM output.
    const leakedTriggerId = 'mh-001-bioweapon-synthesis-verb';
    const ctx = {
      routing_summary: {
        served_by: 'platform_safety_block',
        chain_length: 0,
        fallback_reason: 'category:mass_harm_synthesis tier:1',
      },
      safety_verdict: {
        decision: 'block',
        category: 'mass_harm_synthesis',
        detection_tier: 1,
        trigger_id: leakedTriggerId,  // hostile backend
      },
    };
    const { container } = render(<RoutingFooter context={ctx} />);
    expect(container.textContent).not.toContain(leakedTriggerId);
    // Defensive: no DOM attribute carries it either
    expect(container.innerHTML).not.toContain(leakedTriggerId);
  });

  test('falls back to raw category key when locale entry is missing', () => {
    const ctx = {
      routing_summary: {
        served_by: 'platform_safety_block',
        chain_length: 0,
      },
      safety_verdict: {
        decision: 'block',
        category: 'totally_unknown_category',
        detection_tier: 1,
      },
    };
    render(<RoutingFooter context={ctx} />);
    // Renders the raw key as the fallback (still coarse-grained,
    // never a trigger phrase)
    expect(screen.getByText(/totally_unknown_category/)).toBeInTheDocument();
  });
});
