import React from 'react';
import { FaArrowRight, FaArrowLeft } from 'react-icons/fa';

const RelationsList = ({ relations, currentEntityId }) => {
  if (!relations || relations.length === 0) {
    return <p className="text-muted small mb-0">No relations found.</p>;
  }

  return (
    <div className="relations-list">
      {relations.map(rel => {
        const isOutgoing = rel.from_entity_id === currentEntityId;
        const targetName = isOutgoing
          ? (rel.to_entity?.name || rel.to_entity_id)
          : (rel.from_entity?.name || rel.from_entity_id);

        return (
          <div key={rel.id} className="relation-item">
            <div className="relation-direction">
              {isOutgoing ? (
                <FaArrowRight size={10} className="text-primary" />
              ) : (
                <FaArrowLeft size={10} className="text-muted" />
              )}
            </div>
            <span className="relation-type">{rel.relation_type}</span>
            <span className="relation-target">{targetName}</span>
            <div className="relation-strength">
              <div className="relation-strength-bar">
                <div
                  className="relation-strength-fill"
                  style={{ width: `${(rel.strength || 1) * 100}%` }}
                />
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default RelationsList;
