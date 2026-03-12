import React, { useState, useEffect, useMemo } from 'react';
import { Card, Form, Badge, Spinner, Alert } from 'react-bootstrap';
import { getFileSkills } from '../../services/skills';

const CATEGORY_COLORS = {
  sales: '#28a745', marketing: '#17a2b8', data: '#6f42c1',
  coding: '#fd7e14', communication: '#e83e8c', automation: '#20c997',
  general: '#6c757d',
};

const SkillCard = ({ skill, isSelected, onToggle }) => (
  <Card className="mb-2" style={{
    border: isSelected ? '2px solid #4dabf7' : '1px solid rgba(255,255,255,0.1)',
    background: isSelected ? 'rgba(77,171,247,0.08)' : 'rgba(255,255,255,0.03)',
    cursor: 'pointer',
  }} onClick={onToggle}>
    <Card.Body className="py-2 px-3">
      <div className="d-flex align-items-center justify-content-between">
        <div className="flex-grow-1">
          <div className="d-flex align-items-center gap-2">
            <Form.Check type="checkbox" checked={isSelected} onChange={onToggle}
              onClick={e => e.stopPropagation()} aria-label={skill.name} />
            <strong style={{ fontSize: '0.95rem' }}>{skill.name}</strong>
            <Badge bg="none" style={{
              backgroundColor: CATEGORY_COLORS[skill.category] || '#6c757d',
              fontSize: '0.7rem',
            }}>{skill.category}</Badge>
          </div>
          <small className="text-muted d-block mt-1" style={{ marginLeft: '2rem' }}>
            {skill.description?.substring(0, 120)}
          </small>
        </div>
        <small className="text-muted">{skill.engine}</small>
      </div>
    </Card.Body>
  </Card>
);

const SkillsDataStep = ({ data, onChange, templateName }) => {
  const [allSkills, setAllSkills] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('all');

  useEffect(() => {
    getFileSkills({ tier: 'native' })
      .then(res => setAllSkills(res.data?.skills || res.data || []))
      .catch(() => setAllSkills([]))
      .finally(() => setLoading(false));
  }, []);

  const selectedSlugs = useMemo(() => new Set(
    Object.entries(data.skills || {}).filter(([, v]) => v).map(([k]) => k)
  ), [data.skills]);

  const filtered = useMemo(() => {
    let list = allSkills;
    if (categoryFilter !== 'all') list = list.filter(s => s.category === categoryFilter);
    if (search) list = list.filter(s =>
      s.name.toLowerCase().includes(search.toLowerCase()) ||
      (s.description || '').toLowerCase().includes(search.toLowerCase())
    );
    return list;
  }, [allSkills, categoryFilter, search]);

  const categories = useMemo(() =>
    [...new Set(allSkills.map(s => s.category))].sort(), [allSkills]);

  const handleToggle = (slug) => {
    const updated = { ...data.skills, [slug]: !data.skills?.[slug] };
    onChange({ ...data, skills: updated });
  };

  if (loading) return <div className="text-center py-5"><Spinner animation="border" /></div>;

  return (
    <div className="skills-data-step">
      <h3 className="mb-2">What can your agent do?</h3>
      <p className="text-muted mb-3">Select skills from the marketplace</p>

      {templateName && (
        <Alert variant="success" className="mb-3">
          <small>Based on your <strong>{templateName}</strong> template, we've pre-selected recommended skills.</small>
        </Alert>
      )}

      <Form.Control type="text" placeholder="Search skills..." value={search}
        onChange={e => setSearch(e.target.value)} className="mb-3"
        style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.15)', color: '#fff' }} />

      <div className="d-flex gap-2 flex-wrap mb-3">
        <Badge bg={categoryFilter === 'all' ? 'primary' : 'secondary'} role="button"
          onClick={() => setCategoryFilter('all')}>All</Badge>
        {categories.map(c => (
          <Badge key={c} bg={categoryFilter === c ? 'primary' : 'secondary'} role="button"
            onClick={() => setCategoryFilter(c)} style={{ textTransform: 'capitalize' }}>{c}</Badge>
        ))}
      </div>

      <small className="text-muted mb-2 d-block">
        {selectedSlugs.size} skill{selectedSlugs.size !== 1 ? 's' : ''} selected
      </small>

      {filtered.map(skill => (
        <SkillCard key={skill.slug || skill.name} skill={skill}
          isSelected={!!selectedSlugs.has(skill.slug || skill.name)}
          onToggle={() => handleToggle(skill.slug || skill.name)} />
      ))}

      {filtered.length === 0 && (
        <p className="text-muted text-center py-4">No skills match your search.</p>
      )}
    </div>
  );
};

export default SkillsDataStep;
