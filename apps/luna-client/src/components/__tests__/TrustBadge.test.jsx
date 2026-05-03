import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import TrustBadge from '../TrustBadge';

describe('TrustBadge', () => {
  it('renders nothing when no trust prop is supplied', () => {
    const { container } = render(<TrustBadge />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders Auto tier for autonomous trust', () => {
    const { getByText, container } = render(
      <TrustBadge trust={{ autonomy_tier: 'autonomous', trust_score: 0.92 }} />
    );
    expect(getByText('Auto')).toBeInTheDocument();
    const badge = container.querySelector('.trust-badge');
    expect(badge.title).toContain('92%');
    expect(badge.title).toContain('Auto');
  });

  it('renders Supervised tier with correct icon', () => {
    const { getByText } = render(
      <TrustBadge trust={{ autonomy_tier: 'supervised', trust_score: 0.5 }} />
    );
    expect(getByText('Supervised')).toBeInTheDocument();
  });

  it('falls back to Suggest tier on unknown autonomy_tier', () => {
    const { getByText } = render(
      <TrustBadge trust={{ autonomy_tier: 'mystery_tier', trust_score: 0.1 }} />
    );
    expect(getByText('Suggest')).toBeInTheDocument();
  });

  it('handles missing trust_score by rendering 0%', () => {
    const { container } = render(<TrustBadge trust={{ autonomy_tier: 'autonomous' }} />);
    expect(container.querySelector('.trust-badge').title).toContain('0%');
  });
});
