import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const invokeMock = vi.fn();

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args) => invokeMock(...args),
}));

import CommandPalette from '../CommandPalette';

beforeEach(() => {
  invokeMock.mockReset();
  invokeMock.mockResolvedValue({ app: '', title: '' });
});

describe('CommandPalette', () => {
  it('renders nothing when not visible', () => {
    const { container } = render(
      <CommandPalette visible={false} onClose={() => {}} onSend={() => {}} />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the input when visible and clears it on each open', () => {
    const { rerender } = render(
      <CommandPalette visible={true} onClose={() => {}} onSend={() => {}} />
    );
    const input = screen.getByPlaceholderText(/ask luna anything/i);
    expect(input).toBeInTheDocument();

    fireEvent.change(input, { target: { value: 'remember the milk' } });
    expect(input.value).toBe('remember the milk');

    // Re-open: query should reset.
    rerender(<CommandPalette visible={false} onClose={() => {}} onSend={() => {}} />);
    rerender(<CommandPalette visible={true} onClose={() => {}} onSend={() => {}} />);
    expect(screen.getByPlaceholderText(/ask luna anything/i).value).toBe('');
  });

  it('does nothing on submit when query is blank', () => {
    const onSend = vi.fn();
    const onClose = vi.fn();
    const { container } = render(
      <CommandPalette visible={true} onClose={onClose} onSend={onSend} />
    );
    const form = container.querySelector('form');
    fireEvent.submit(form);
    expect(onSend).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('sends the query prefixed with active app context and closes', async () => {
    invokeMock.mockResolvedValue({ app: 'Code', title: 'project — file.js' });
    const onSend = vi.fn();
    const onClose = vi.fn();

    const { container } = render(
      <CommandPalette visible={true} onClose={onClose} onSend={onSend} />
    );
    fireEvent.change(screen.getByPlaceholderText(/ask luna anything/i), {
      target: { value: 'summarise this file' },
    });
    fireEvent.submit(container.querySelector('form'));

    await waitFor(() => {
      expect(onSend).toHaveBeenCalledTimes(1);
    });
    expect(onSend.mock.calls[0][0]).toContain('summarise this file');
    expect(onSend.mock.calls[0][0]).toContain('Code');
    expect(onClose).toHaveBeenCalled();
  });

  it('still sends the query when get_active_app rejects (Tauri unavailable)', async () => {
    invokeMock.mockRejectedValue(new Error('not in tauri'));
    const onSend = vi.fn();
    const onClose = vi.fn();
    const { container } = render(
      <CommandPalette visible={true} onClose={onClose} onSend={onSend} />
    );
    fireEvent.change(screen.getByPlaceholderText(/ask luna anything/i), {
      target: { value: 'hello' },
    });
    fireEvent.submit(container.querySelector('form'));

    await waitFor(() => {
      expect(onSend).toHaveBeenCalled();
    });
    expect(onSend.mock.calls[0][0]).toContain('hello');
    expect(onClose).toHaveBeenCalled();
  });

  it('closes on Escape key', () => {
    const onClose = vi.fn();
    render(<CommandPalette visible={true} onClose={onClose} onSend={() => {}} />);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });
});
