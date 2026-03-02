import React, { useState, useEffect } from 'react';
import { Button, Form, Spinner } from 'react-bootstrap';
import { FaSave, FaTrash, FaStar, FaEdit } from 'react-icons/fa';
import { memoryService } from '../../services/memory';
import { ALL_CATEGORIES, ALL_STATUSES, ENTITY_TYPES, getCategoryConfig, getStatusConfig } from './constants';
import RelationsList from './RelationsList';

const EntityDetail = ({ entity, onUpdate, onDelete, onScore, onStatusChange }) => {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    name: entity.name,
    category: entity.category || '',
    entity_type: entity.entity_type || 'concept',
    description: entity.description || entity.attributes?.description || '',
    status: entity.status || 'draft',
  });
  const [relations, setRelations] = useState([]);
  const [relLoading, setRelLoading] = useState(true);

  useEffect(() => {
    loadRelations();
  }, [entity.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadRelations = async () => {
    setRelLoading(true);
    try {
      const data = await memoryService.getRelations(entity.id);
      setRelations(data || []);
    } catch {
      setRelations([]);
    } finally {
      setRelLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onUpdate(entity.id, {
        name: form.name,
        category: form.category,
        entity_type: form.entity_type,
        description: form.description,
      });
      setEditing(false);
    } finally {
      setSaving(false);
    }
  };

  // Attributes (email, phone, company, etc.)
  const attrs = entity.attributes || {};
  const attrKeys = Object.keys(attrs).filter(k => k !== 'description');

  return (
    <div className="entity-detail">
      <div className="entity-detail-section">
        <div className="entity-detail-header">
          <h6 className="entity-detail-title">Details</h6>
          {!editing ? (
            <Button variant="link" size="sm" className="p-0 text-muted" onClick={() => setEditing(true)}>
              <FaEdit size={13} className="me-1" /> Edit
            </Button>
          ) : (
            <div className="d-flex gap-2">
              <Button variant="link" size="sm" className="p-0 text-muted" onClick={() => setEditing(false)}>Cancel</Button>
              <Button variant="link" size="sm" className="p-0 text-primary" onClick={handleSave} disabled={saving}>
                {saving ? <Spinner size="sm" animation="border" /> : <><FaSave size={12} className="me-1" /> Save</>}
              </Button>
            </div>
          )}
        </div>

        {editing ? (
          <div className="entity-edit-form">
            <Form.Group className="mb-2">
              <Form.Label className="small text-muted mb-1">Name</Form.Label>
              <Form.Control
                size="sm"
                value={form.name}
                onChange={e => setForm({ ...form, name: e.target.value })}
                className="entity-input"
              />
            </Form.Group>
            <div className="d-flex gap-2 mb-2">
              <Form.Group className="flex-fill">
                <Form.Label className="small text-muted mb-1">Category</Form.Label>
                <Form.Select
                  size="sm"
                  value={form.category}
                  onChange={e => setForm({ ...form, category: e.target.value })}
                  className="entity-input"
                >
                  <option value="">Select...</option>
                  {ALL_CATEGORIES.map(c => {
                    const cfg = getCategoryConfig(c);
                    return <option key={c} value={c}>{cfg.label}</option>;
                  })}
                </Form.Select>
              </Form.Group>
              <Form.Group className="flex-fill">
                <Form.Label className="small text-muted mb-1">Type</Form.Label>
                <Form.Select
                  size="sm"
                  value={form.entity_type}
                  onChange={e => setForm({ ...form, entity_type: e.target.value })}
                  className="entity-input"
                >
                  {ENTITY_TYPES.map(t => (
                    <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>
                  ))}
                </Form.Select>
              </Form.Group>
            </div>
            <Form.Group className="mb-2">
              <Form.Label className="small text-muted mb-1">Description</Form.Label>
              <Form.Control
                as="textarea"
                rows={2}
                size="sm"
                value={form.description}
                onChange={e => setForm({ ...form, description: e.target.value })}
                className="entity-input"
              />
            </Form.Group>
          </div>
        ) : (
          <div className="entity-detail-fields">
            {(entity.description || attrs.description) && (
              <div className="entity-field">
                <span className="entity-field-label">Description</span>
                <span className="entity-field-value">{entity.description || attrs.description}</span>
              </div>
            )}
            <div className="entity-field">
              <span className="entity-field-label">Type</span>
              <span className="entity-field-value">{entity.entity_type}</span>
            </div>
            {entity.source_url && (
              <div className="entity-field">
                <span className="entity-field-label">Source</span>
                <a href={entity.source_url} target="_blank" rel="noreferrer" className="entity-field-value text-primary">
                  {entity.source_url}
                </a>
              </div>
            )}
            {entity.created_at && (
              <div className="entity-field">
                <span className="entity-field-label">Created</span>
                <span className="entity-field-value">{new Date(entity.created_at).toLocaleString()}</span>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Attributes */}
      {attrKeys.length > 0 && (
        <div className="entity-detail-section">
          <h6 className="entity-detail-title">Attributes</h6>
          <div className="entity-detail-fields">
            {attrKeys.map(key => (
              <div className="entity-field" key={key}>
                <span className="entity-field-label">{key}</span>
                <span className="entity-field-value">
                  {typeof attrs[key] === 'object' ? JSON.stringify(attrs[key]) : String(attrs[key])}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Relations */}
      <div className="entity-detail-section">
        <h6 className="entity-detail-title">Relations</h6>
        {relLoading ? (
          <div className="text-center py-2"><Spinner size="sm" animation="border" className="text-muted" /></div>
        ) : (
          <RelationsList relations={relations} currentEntityId={entity.id} />
        )}
      </div>

      {/* Actions */}
      <div className="entity-detail-actions">
        <div className="d-flex gap-2 flex-wrap">
          <Form.Select
            size="sm"
            value={entity.status || 'draft'}
            onChange={e => onStatusChange(entity.id, e.target.value)}
            className="entity-input"
            style={{ width: 'auto', minWidth: '120px' }}
          >
            {ALL_STATUSES.map(s => {
              const cfg = getStatusConfig(s);
              return <option key={s} value={s}>{cfg.label}</option>;
            })}
          </Form.Select>
          <Button
            variant="outline-primary"
            size="sm"
            onClick={() => onScore(entity.id)}
            className="entity-action-btn"
          >
            <FaStar size={11} className="me-1" /> Score
          </Button>
          <Button
            variant="outline-danger"
            size="sm"
            onClick={() => onDelete(entity.id)}
            className="entity-action-btn"
          >
            <FaTrash size={11} className="me-1" /> Delete
          </Button>
        </div>
      </div>
    </div>
  );
};

export default EntityDetail;
