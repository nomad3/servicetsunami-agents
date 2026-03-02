import React, { useState } from 'react';
import { Modal, Form, Button, Spinner } from 'react-bootstrap';
import { ALL_CATEGORIES, ENTITY_TYPES, getCategoryConfig } from './constants';

const EntityCreateModal = ({ show, onHide, onCreate }) => {
  const [form, setForm] = useState({
    name: '',
    category: 'concept',
    entity_type: 'concept',
    description: '',
  });
  const [creating, setCreating] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!form.name.trim()) return;
    setCreating(true);
    try {
      await onCreate({
        name: form.name.trim(),
        category: form.category,
        entity_type: form.entity_type,
        description: form.description.trim() || null,
      });
      // Reset form
      setForm({ name: '', category: 'concept', entity_type: 'concept', description: '' });
      onHide();
    } finally {
      setCreating(false);
    }
  };

  return (
    <Modal show={show} onHide={onHide} centered className="memory-modal">
      <Modal.Header closeButton>
        <Modal.Title className="fs-6 fw-semibold">Add Entity</Modal.Title>
      </Modal.Header>
      <Form onSubmit={handleSubmit}>
        <Modal.Body>
          <Form.Group className="mb-3">
            <Form.Label className="small fw-semibold">Name *</Form.Label>
            <Form.Control
              value={form.name}
              onChange={e => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. John Smith, Acme Corp"
              autoFocus
              className="entity-input"
            />
          </Form.Group>
          <div className="d-flex gap-3 mb-3">
            <Form.Group className="flex-fill">
              <Form.Label className="small fw-semibold">Category</Form.Label>
              <Form.Select
                value={form.category}
                onChange={e => setForm({ ...form, category: e.target.value })}
                className="entity-input"
              >
                {ALL_CATEGORIES.map(c => {
                  const cfg = getCategoryConfig(c);
                  return <option key={c} value={c}>{cfg.label}</option>;
                })}
              </Form.Select>
            </Form.Group>
            <Form.Group className="flex-fill">
              <Form.Label className="small fw-semibold">Type</Form.Label>
              <Form.Select
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
          <Form.Group>
            <Form.Label className="small fw-semibold">Description</Form.Label>
            <Form.Control
              as="textarea"
              rows={3}
              value={form.description}
              onChange={e => setForm({ ...form, description: e.target.value })}
              placeholder="Brief description of this entity..."
              className="entity-input"
            />
          </Form.Group>
        </Modal.Body>
        <Modal.Footer>
          <Button variant="outline-secondary" size="sm" onClick={onHide}>Cancel</Button>
          <Button variant="primary" size="sm" type="submit" disabled={creating || !form.name.trim()}>
            {creating ? <Spinner size="sm" animation="border" /> : 'Create Entity'}
          </Button>
        </Modal.Footer>
      </Form>
    </Modal>
  );
};

export default EntityCreateModal;
