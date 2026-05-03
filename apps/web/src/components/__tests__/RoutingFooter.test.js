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
