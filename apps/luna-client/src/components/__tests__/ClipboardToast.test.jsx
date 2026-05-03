import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

const apiJsonMock = vi.fn();
let listenHandler = null;
const unlistenMock = vi.fn();

vi.mock('../../api', () => ({
  apiJson: (...args) => apiJsonMock(...args),
}));

vi.mock('@tauri-apps/api/event', () => ({
  listen: vi.fn((event, cb) => {
    listenHandler = cb;
    return Promise.resolve(unlistenMock);
  }),
}));

import ClipboardToast from '../ClipboardToast';

beforeEach(() => {
  apiJsonMock.mockReset();
  unlistenMock.mockReset();
  listenHandler = null;
});

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('ClipboardToast', () => {
  it('renders nothing initially', () => {
    const { container } = render(<ClipboardToast />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows a toast when knowledge graph returns a hit', async () => {
    apiJsonMock.mockResolvedValue([
      { name: 'Luna', category: 'agent', description: 'AI assistant for Simon' },
    ]);

    render(<ClipboardToast />);
    await flush();
    expect(listenHandler).toBeTruthy();

    listenHandler({ payload: 'Luna' });

    await waitFor(() => {
      expect(screen.getByText('Luna')).toBeInTheDocument();
      expect(screen.getByText('agent')).toBeInTheDocument();
    });
  });

  it('skips long sentences (>4 words)', async () => {
    render(<ClipboardToast />);
    await flush();
    listenHandler({ payload: 'one two three four five six' });
    await flush();
    expect(apiJsonMock).not.toHaveBeenCalled();
  });

  it('skips text that looks like a URL', async () => {
    render(<ClipboardToast />);
    await flush();
    listenHandler({ payload: 'https://example.com' });
    await flush();
    expect(apiJsonMock).not.toHaveBeenCalled();
  });

  it('skips very short or very long content', async () => {
    render(<ClipboardToast />);
    await flush();
    listenHandler({ payload: 'ab' });
    listenHandler({ payload: 'x'.repeat(250) });
    await flush();
    expect(apiJsonMock).not.toHaveBeenCalled();
  });

  it('skips text containing code-like characters', async () => {
    render(<ClipboardToast />);
    await flush();
    listenHandler({ payload: '{foo: bar}' });
    listenHandler({ payload: 'a/b/c' });
    await flush();
    expect(apiJsonMock).not.toHaveBeenCalled();
  });
});
