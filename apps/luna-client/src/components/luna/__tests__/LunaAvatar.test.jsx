import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import LunaAvatar from '../LunaAvatar';

describe('LunaAvatar', () => {
  it('renders default idle state with the idle emote', () => {
    const { container } = render(<LunaAvatar />);
    const avatar = container.querySelector('.luna-avatar');
    expect(avatar).toBeInTheDocument();
    expect(avatar).toHaveClass('luna-state-idle');
    expect(avatar).toHaveAttribute('title', 'Luna: idle');
    expect(container.querySelector('.luna-emote')).toHaveTextContent('~');
  });

  it.each([
    ['listening', '((*))'],
    ['thinking', '? ...'],
    ['responding', '> _ <'],
    ['alert', '!! △ !!'],
    ['error', '#!@%&'],
    ['handoff', '→'],
  ])('renders %s emote correctly', (state, emote) => {
    const { container } = render(<LunaAvatar state={state} />);
    expect(container.querySelector('.luna-emote')).toHaveTextContent(emote);
    expect(container.querySelector('.luna-avatar')).toHaveClass(`luna-state-${state}`);
  });

  it('falls back to the idle emote on unknown states', () => {
    const { container } = render(<LunaAvatar state="not-a-real-state" />);
    expect(container.querySelector('.luna-emote')).toHaveTextContent('~');
  });

  it('hides the emote on xs/sm sizes', () => {
    const { container, rerender } = render(<LunaAvatar size="xs" />);
    expect(container.querySelector('.luna-emote')).toBeNull();
    rerender(<LunaAvatar size="sm" />);
    expect(container.querySelector('.luna-emote')).toBeNull();
  });

  it('forwards click handler when supplied', () => {
    const onClick = vi.fn();
    const { container } = render(<LunaAvatar onClick={onClick} />);
    fireEvent.click(container.querySelector('.luna-avatar'));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(container.querySelector('.luna-avatar')).toHaveStyle({ cursor: 'pointer' });
  });

  it('uses default cursor when no click handler', () => {
    const { container } = render(<LunaAvatar />);
    expect(container.querySelector('.luna-avatar')).toHaveStyle({ cursor: 'default' });
  });
});
