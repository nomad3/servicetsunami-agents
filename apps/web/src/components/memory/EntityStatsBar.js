import React from 'react';
import { getCategoryConfig } from './constants';

const EntityStatsBar = ({ entities }) => {
  const total = entities.length;

  // Count by category
  const byCat = {};
  entities.forEach(e => {
    const cat = (e.category || 'concept').toLowerCase();
    byCat[cat] = (byCat[cat] || 0) + 1;
  });

  // Sort by count descending
  const sorted = Object.entries(byCat).sort((a, b) => b[1] - a[1]);

  return (
    <div className="memory-stats-bar">
      <div className="stats-total">
        <span className="stats-total-number">{total}</span>
        <span className="stats-total-label">entities</span>
      </div>
      <div className="stats-categories">
        {sorted.map(([cat, count]) => {
          const cfg = getCategoryConfig(cat);
          const Icon = cfg.icon;
          return (
            <div
              key={cat}
              className="stats-chip"
              style={{ background: cfg.bg, borderColor: cfg.color + '40' }}
            >
              <Icon size={12} style={{ color: cfg.color }} />
              <span className="stats-chip-label">{cfg.label}</span>
              <span className="stats-chip-count" style={{ color: cfg.color }}>{count}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default EntityStatsBar;
