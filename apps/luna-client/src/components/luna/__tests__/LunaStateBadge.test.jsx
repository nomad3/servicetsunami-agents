import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import LunaStateBadge from '../LunaStateBadge';

describe('LunaStateBadge', () => {
  it('renders the supplied state label with underscore-to-space conversion', () => {
    const { getByText } = render(<LunaStateBadge state="private_mode" />);
    expect(getByText('private mode')).toBeInTheDocument();
  });

  it('falls back to idle label when state missing', () => {
    const { getByText } = render(<LunaStateBadge />);
    expect(getByText('idle')).toBeInTheDocument();
  });

  it('renders a colour dot for each known state', () => {
    const states = ['idle', 'thinking', 'happy', 'error'];
    states.forEach((state) => {
      const { container } = render(<LunaStateBadge state={state} />);
      // First inner span is the dot — must have backgroundColor styling.
      const dot = container.querySelector('span span');
      expect(dot).toBeTruthy();
      expect(dot.style.backgroundColor).not.toBe('');
    });
  });
});
