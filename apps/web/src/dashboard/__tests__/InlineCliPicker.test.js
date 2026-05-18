import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import InlineCliPicker from '../InlineCliPicker';

jest.mock('../../services/branding', () => ({
  brandingService: {
    getFeatures: jest.fn(),
    updateFeatures: jest.fn(),
  },
}));

jest.mock('../../services/integrationConfigService', () => ({
  __esModule: true,
  default: {
    listConnectedClis: jest.fn(),
  },
}));

const { brandingService } = require('../../services/branding');
const integrationConfigService = require('../../services/integrationConfigService').default;

describe('InlineCliPicker', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    brandingService.getFeatures.mockResolvedValue({ default_cli_platform: null });
    brandingService.updateFeatures.mockResolvedValue({});
    // Default: every CLI connected so existing tests behave the way
    // they did before the filter shipped. Individual tests below
    // narrow this when they want to assert filtering behaviour.
    integrationConfigService.listConnectedClis.mockResolvedValue({
      data: { connected: ['claude_code', 'codex', 'gemini_cli', 'copilot_cli'] },
    });
  });

  test('renders nothing while getFeatures is pending', async () => {
    // Hold the promise un-resolved so the loaded state never flips.
    let resolveFeatures;
    brandingService.getFeatures.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFeatures = resolve;
      }),
    );
    render(<InlineCliPicker />);
    // Picker hides itself entirely until loaded — no select, no label.
    expect(screen.queryByLabelText(/Tenant default CLI platform/i)).not.toBeInTheDocument();
    // Resolve so test teardown doesn't leak a pending act.
    await act(async () => {
      resolveFeatures({ default_cli_platform: null });
    });
  });

  test('renders select after getFeatures resolves with Auto as the default', async () => {
    render(<InlineCliPicker />);
    const select = await screen.findByLabelText(/Tenant default CLI platform/i);
    expect(select).toBeInTheDocument();
    expect(select.value).toBe('__auto__');
    // Label reflects the tenant-wide scope.
    expect(screen.getByText('Tenant CLI')).toBeInTheDocument();
  });

  test('reflects the stored default from getFeatures', async () => {
    brandingService.getFeatures.mockResolvedValue({ default_cli_platform: 'codex' });
    render(<InlineCliPicker />);
    const select = await screen.findByLabelText(/Tenant default CLI platform/i);
    expect(select.value).toBe('codex');
  });

  test('change handler saves selection via updateFeatures', async () => {
    render(<InlineCliPicker />);
    const select = await screen.findByLabelText(/Tenant default CLI platform/i);
    fireEvent.change(select, { target: { value: 'codex' } });
    await waitFor(() =>
      expect(brandingService.updateFeatures).toHaveBeenCalledWith({
        default_cli_platform: 'codex',
      }),
    );
  });

  test('"Auto" sends null to clear tenant default', async () => {
    brandingService.getFeatures.mockResolvedValue({ default_cli_platform: 'codex' });
    render(<InlineCliPicker />);
    const select = await screen.findByLabelText(/Tenant default CLI platform/i);
    fireEvent.change(select, { target: { value: '__auto__' } });
    await waitFor(() =>
      expect(brandingService.updateFeatures).toHaveBeenCalledWith({
        default_cli_platform: null,
      }),
    );
  });

  test('shows saved affordance after successful save', async () => {
    render(<InlineCliPicker />);
    const select = await screen.findByLabelText(/Tenant default CLI platform/i);
    fireEvent.change(select, { target: { value: 'claude_code' } });
    // ✓ appears after the save resolves.
    expect(await screen.findByLabelText('Saved')).toBeInTheDocument();
  });

  test('unmount during pending getFeatures does not call setState (no warning)', async () => {
    let resolveFeatures;
    brandingService.getFeatures.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFeatures = resolve;
      }),
    );
    // Capture console.error so we'd see any "called setState on unmounted" warning.
    const errorSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
    const { unmount } = render(<InlineCliPicker />);
    unmount();
    await act(async () => {
      resolveFeatures({ default_cli_platform: 'codex' });
    });
    expect(errorSpy).not.toHaveBeenCalled();
    errorSpy.mockRestore();
  });

  test('surfaces error indicator when getFeatures rejects', async () => {
    const warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {});
    brandingService.getFeatures.mockRejectedValue(new Error('boom'));
    render(<InlineCliPicker />);
    // After load completes the picker should still mount (loaded=true)
    // and surface a visible error indicator.
    expect(
      await screen.findByText(/Could not load tenant default CLI/i),
    ).toBeInTheDocument();
    expect(warnSpy).toHaveBeenCalled();
    warnSpy.mockRestore();
  });

  // ──────────────────────────────────────────────────────────────────
  // Connected-CLI filter — backend returns a subset, dropdown shrinks.
  // (Auto is always offered; opencode is intentionally hidden — it's
  // the routing floor, not a user-pickable target.)
  // ──────────────────────────────────────────────────────────────────
  test('only Auto + connected CLIs are shown when API returns a subset', async () => {
    integrationConfigService.listConnectedClis.mockResolvedValue({
      data: { connected: ['codex', 'opencode'] },
    });
    render(<InlineCliPicker />);
    const select = await screen.findByLabelText(/Tenant default CLI platform/i);
    // The dropdown's <option> labels = Auto + Codex only. No Claude
    // Code, Gemini CLI, or Copilot CLI because they weren't returned.
    const labels = Array.from(select.querySelectorAll('option')).map((o) => o.textContent);
    expect(labels).toEqual(['Auto', 'Codex']);
  });

  test('falls back to all CLI options when listConnectedClis rejects', async () => {
    // Suppress the console.warn the picker emits on failure so the
    // test output stays clean.
    const warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {});
    integrationConfigService.listConnectedClis.mockRejectedValue(new Error('500'));
    render(<InlineCliPicker />);
    const select = await screen.findByLabelText(/Tenant default CLI platform/i);
    const labels = Array.from(select.querySelectorAll('option')).map((o) => o.textContent);
    // 5xx → graceful degrade to the pre-filter behaviour: every CLI
    // option still rendered so a user with a failing endpoint isn't
    // stuck on Auto-only.
    expect(labels).toEqual(['Auto', 'Claude Code', 'Codex', 'Gemini CLI', 'Copilot CLI', 'Qwen Code']);
    expect(warnSpy).toHaveBeenCalled();
    warnSpy.mockRestore();
  });

  // ──────────────────────────────────────────────────────────────────
  // Stale selection — tenant's stored CLI is no longer connected.
  // The select.value must still match the stored value so React state
  // and DOM stay in sync; otherwise the browser silently displays Auto
  // while state says claude_code and saves keep writing the old CLI.
  // ──────────────────────────────────────────────────────────────────
  test('selected CLI no longer connected still renders as an option', async () => {
    brandingService.getFeatures.mockResolvedValue({
      default_cli_platform: 'claude_code',
    });
    // Backend says only codex is connected — claude_code is the stale
    // tenant choice that should still appear (with a "(disconnected)"
    // suffix) so the <select> value resolves to a real <option>.
    integrationConfigService.listConnectedClis.mockResolvedValue({
      data: { connected: ['codex'] },
    });
    render(<InlineCliPicker />);
    const select = await screen.findByLabelText(/Tenant default CLI platform/i);
    // Direct DOM access mirrors the pattern used by the sibling tests
    // above — Testing Library has no first-class option-list helper, so
    // walking ``<option>`` nodes is the least-bad readout.
    // eslint-disable-next-line testing-library/no-node-access
    const labels = Array.from(select.querySelectorAll('option')).map((o) => o.textContent);
    // Order: Auto, then CLI_OPTIONS order (claude_code is first in
    // CLI_OPTIONS, codex second). claude_code is tagged "(disconnected)".
    expect(labels).toEqual(['Auto', 'Claude Code (disconnected)', 'Codex']);
    // Crucially: the stored value is preserved on the select element
    // so the user's choice isn't silently shadowed by Auto.
    expect(select.value).toBe('claude_code');
  });

  test('each instance gets a unique id (split-pane safe)', async () => {
    render(<InlineCliPicker />);
    render(<InlineCliPicker />);
    // Both pickers should mount and expose their selects via the aria-label.
    const selects = await screen.findAllByLabelText(/Tenant default CLI platform/i);
    expect(selects).toHaveLength(2);
    const id1 = selects[0].getAttribute('id');
    const id2 = selects[1].getAttribute('id');
    expect(id1).toBeTruthy();
    expect(id2).toBeTruthy();
    expect(id1).not.toEqual(id2);
  });
});
