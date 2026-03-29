import React from 'react';

const TIER_CONFIG = {
  autonomous: { label: 'Auto', color: 'var(--color-accent)', icon: '\u26A1' },
  supervised: { label: 'Supervised', color: '#f59e0b', icon: '\uD83D\uDC41' },
  recommend_only: { label: 'Suggest', color: 'var(--color-text-muted)', icon: '\uD83D\uDCAC' },
};

export default function TrustBadge({ trust }) {
  if (!trust) return null;

  const tier = TIER_CONFIG[trust.autonomy_tier] || TIER_CONFIG.recommend_only;
  const score = Math.round((trust.trust_score || 0) * 100);

  return (
    <div className="trust-badge" title={`Trust: ${score}% \u2014 ${tier.label} mode`}>
      <span className="trust-icon">{tier.icon}</span>
      <span className="trust-label" style={{ color: tier.color }}>{tier.label}</span>
    </div>
  );
}
