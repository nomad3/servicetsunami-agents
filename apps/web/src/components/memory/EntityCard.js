import React from 'react';
import { FaChevronRight } from 'react-icons/fa';
import { getCategoryConfig, getStatusConfig } from './constants';
import EntityDetail from './EntityDetail';

const EntityCard = ({
  entity,
  isExpanded,
  isSelected,
  onToggleExpand,
  onToggleSelect,
  onUpdate,
  onDelete,
  onScore,
  onStatusChange,
}) => {
  const catCfg = getCategoryConfig(entity.category);
  const statusCfg = getStatusConfig(entity.status);
  const CatIcon = catCfg.icon;

  const description = entity.description
    || entity.attributes?.description
    || '';

  return (
    <div className={`entity-card ${isExpanded ? 'expanded' : ''}`}>
      <div className="entity-card-header" onClick={() => onToggleExpand(entity.id)}>
        <div className="entity-card-select" onClick={(e) => e.stopPropagation()}>
          <input
            type="checkbox"
            checked={isSelected}
            onChange={() => onToggleSelect(entity.id)}
            className="entity-checkbox"
          />
        </div>

        <div className="entity-card-icon" style={{ background: catCfg.bg, color: catCfg.color }}>
          <CatIcon size={16} />
        </div>

        <div className="entity-card-info">
          <div className="entity-card-name">{entity.name}</div>
          <div className="entity-card-meta">
            <span className="entity-badge" style={{ background: catCfg.bg, color: catCfg.color, borderColor: catCfg.color + '40' }}>
              {catCfg.label}
            </span>
            <span className="entity-badge" style={{ background: statusCfg.bg, color: statusCfg.color, borderColor: statusCfg.color + '40' }}>
              {statusCfg.label}
            </span>
            {entity.score != null && (
              <span className={`entity-badge entity-score ${entity.score >= 61 ? 'high' : entity.score >= 31 ? 'mid' : 'low'}`}>
                {entity.score}
              </span>
            )}
          </div>
        </div>

        <div className="entity-card-confidence">
          <div className="confidence-bar">
            <div className="confidence-fill" style={{ width: `${(entity.confidence || 0) * 100}%` }} />
          </div>
          <span className="confidence-label">{((entity.confidence || 0) * 100).toFixed(0)}%</span>
        </div>

        <FaChevronRight className={`entity-card-chevron ${isExpanded ? 'open' : ''}`} size={12} />
      </div>

      {description && !isExpanded && (
        <div className="entity-card-description">{description}</div>
      )}

      {isExpanded && (
        <EntityDetail
          entity={entity}
          onUpdate={onUpdate}
          onDelete={onDelete}
          onScore={onScore}
          onStatusChange={onStatusChange}
        />
      )}
    </div>
  );
};

export default EntityCard;
