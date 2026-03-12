import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Alert, Badge, Button, Card, Col, Dropdown, Form,
  InputGroup, Modal, Nav, Row, Spinner,
} from 'react-bootstrap';
import {
  FaCode, FaCodeBranch, FaChevronDown, FaChevronUp, FaEdit,
  FaEllipsisV, FaFileAlt, FaGithub, FaHistory, FaPlay, FaPlus,
  FaRocket, FaSearch, FaTerminal, FaTimes, FaTrash,
} from 'react-icons/fa';
import { useTranslation } from 'react-i18next';
import Layout from '../components/Layout';
import {
  getFileSkills, createFileSkill, updateFileSkill, forkFileSkill,
  deleteFileSkill, executeFileSkill, getSkillVersions, importFromGithub,
} from '../services/skills';

const CATEGORY_COLORS = {
  sales: '#4ecdc4',
  marketing: '#ff6b6b',
  data: '#45b7d1',
  coding: '#96ceb4',
  communication: '#dda0dd',
  automation: '#ffd93d',
  general: '#95a5a6',
};

const CATEGORIES = ['all', 'sales', 'marketing', 'data', 'coding', 'communication', 'automation', 'general'];

const ENGINE_ICONS = { python: FaCode, shell: FaTerminal, markdown: FaFileAlt };

const ENGINE_DEFAULTS = {
  python: 'def execute(inputs):\n    # Your skill logic here\n    return {"result": "done"}',
  shell: '#!/bin/bash\n# Inputs are available as SKILL_INPUT_<NAME> env vars\necho "Hello from skill"\n',
  markdown: '# Prompt Template\n\nUse {{input_name}} for placeholders.\n\nInstructions for the agent go here.\n',
};

const inputStyle = {
  background: 'var(--surface-contrast, rgba(0,0,0,0.2))',
  border: '1px solid var(--color-border)',
  color: 'var(--color-foreground)',
  borderRadius: 8,
};

const modalSectionStyle = { background: 'var(--surface-elevated)' };

const EMPTY_SKILL = { name: '', description: '', engine: 'python', category: 'general', auto_trigger: '', tags: '', chain_to: [], script: ENGINE_DEFAULTS.python, inputs: [] };

const SkillsPage = () => {
  const { t } = useTranslation('skills');
  const [skills, setSkills] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);
  const [activeTab, setActiveTab] = useState('native');
  const [activeCategory, setActiveCategory] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [executeSkill, setExecuteSkill] = useState(null);
  const [executeInputs, setExecuteInputs] = useState({});
  const [executing, setExecuting] = useState(false);
  const [executeResult, setExecuteResult] = useState(null);
  const [editSkill, setEditSkill] = useState(null);
  const [expandedSkill, setExpandedSkill] = useState(null);
  const [creating, setCreating] = useState(false);
  const [importUrl, setImportUrl] = useState('');
  const [importing, setImporting] = useState(false);
  const [newSkill, setNewSkill] = useState({ ...EMPTY_SKILL });
  const debounceRef = useRef(null);

  const fetchSkills = useCallback(async (search) => {
    try {
      setLoading(true);
      const params = {};
      if (search && search.length > 2) params.search = search;
      const response = await getFileSkills(params);
      setSkills(response.data || []);
    } catch (err) {
      console.error('Error fetching skills:', err);
      setError(t('errors.load'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { fetchSkills(); }, [fetchSkills]);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (searchQuery.length > 2) {
      debounceRef.current = setTimeout(() => fetchSkills(searchQuery), 300);
    }
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [searchQuery, fetchSkills]);

  const filtered = skills
    .filter(s => activeTab === 'native' ? s.tier === 'native' : activeTab === 'my' ? s.tier === 'custom' : s.tier === 'community')
    .filter(s => activeCategory === 'all' || s.category === activeCategory)
    .filter(s => !searchQuery || s.name.toLowerCase().includes(searchQuery.toLowerCase()) || (s.description || '').toLowerCase().includes(searchQuery.toLowerCase()));

  const flash = (setter, msg, ms = 4000) => { setter(msg); setTimeout(() => setter(null), ms); };

  // --- Execute ---
  const handleOpenExecute = (skill) => {
    setExecuteSkill(skill);
    const defaults = {};
    (skill.inputs || []).forEach(inp => { defaults[inp.name] = ''; });
    setExecuteInputs(defaults);
    setExecuteResult(null);
  };

  const handleExecute = async () => {
    if (!executeSkill) return;
    try {
      setExecuting(true);
      setExecuteResult(null);
      const response = await executeFileSkill(executeSkill.name, executeInputs);
      setExecuteResult({ success: true, data: response.data });
    } catch (err) {
      setExecuteResult({ success: false, error: err.response?.data?.detail || t('errors.execute') });
    } finally {
      setExecuting(false);
    }
  };

  // --- Create / Edit ---
  const openCreate = () => {
    setEditSkill(null);
    setNewSkill({ ...EMPTY_SKILL });
    setShowCreate(true);
  };

  const openEdit = (skill) => {
    setEditSkill(skill);
    setNewSkill({
      name: skill.name, description: skill.description || '', engine: skill.engine || 'python',
      category: skill.category || 'general', auto_trigger: skill.auto_trigger || '',
      tags: (skill.tags || []).join(', '), chain_to: skill.chain_to || [],
      script: skill.script || ENGINE_DEFAULTS[skill.engine || 'python'],
      inputs: skill.inputs || [],
    });
    setShowCreate(true);
  };

  const handleSaveSkill = async () => {
    if (!newSkill.name.trim()) return;
    try {
      setCreating(true);
      const payload = {
        name: newSkill.name.trim(), description: newSkill.description.trim(),
        engine: newSkill.engine, category: newSkill.category,
        auto_trigger: newSkill.auto_trigger.trim() || null,
        tags: newSkill.tags ? newSkill.tags.split(',').map(t => t.trim()).filter(Boolean) : [],
        chain_to: newSkill.chain_to, script: newSkill.script,
        inputs: newSkill.inputs.filter(i => i.name.trim()),
      };
      if (editSkill) {
        await updateFileSkill(editSkill.slug, payload);
        flash(setSuccess, t('updated'));
      } else {
        await createFileSkill(payload);
        flash(setSuccess, t('skillCreated'));
      }
      setShowCreate(false);
      setNewSkill({ ...EMPTY_SKILL });
      await fetchSkills();
    } catch (err) {
      flash(setError, err.response?.data?.detail || (editSkill ? t('errors.update') : t('errors.create')), 5000);
    } finally {
      setCreating(false);
    }
  };

  // --- Fork / Delete ---
  const handleFork = async (skill) => {
    try {
      await forkFileSkill(skill.slug);
      flash(setSuccess, t('forked'));
      await fetchSkills();
    } catch (err) {
      flash(setError, err.response?.data?.detail || t('errors.fork'), 5000);
    }
  };

  const handleDelete = async (skill) => {
    if (!window.confirm(`Delete "${skill.name}"?`)) return;
    try {
      await deleteFileSkill(skill.slug);
      flash(setSuccess, t('deleted'));
      await fetchSkills();
    } catch (err) {
      flash(setError, err.response?.data?.detail || t('errors.delete'), 5000);
    }
  };

  // --- Import ---
  const handleImportGithub = async () => {
    if (!importUrl.trim()) return;
    try {
      setImporting(true);
      await importFromGithub(importUrl.trim());
      flash(setSuccess, t('imported'));
      setShowImport(false);
      setImportUrl('');
      await fetchSkills();
    } catch (err) {
      flash(setError, err.response?.data?.detail || t('errors.import'), 5000);
    } finally {
      setImporting(false);
    }
  };

  // --- Input builder helpers ---
  const handleAddInput = () => setNewSkill({ ...newSkill, inputs: [...newSkill.inputs, { name: '', type: 'string', description: '', required: false }] });
  const handleRemoveInput = (i) => setNewSkill({ ...newSkill, inputs: newSkill.inputs.filter((_, idx) => idx !== i) });
  const handleInputChange = (i, field, val) => { const u = [...newSkill.inputs]; u[i] = { ...u[i], [field]: val }; setNewSkill({ ...newSkill, inputs: u }); };

  // --- Skill Card ---
  const SkillCard = ({ skill }) => {
    const EngineIcon = ENGINE_ICONS[skill.engine] || FaCode;
    const isExpanded = expandedSkill === skill.slug;
    const catColor = CATEGORY_COLORS[skill.category] || CATEGORY_COLORS.general;

    return (
      <Card
        style={{
          background: 'rgba(255,255,255,0.05)', backdropFilter: 'blur(20px)',
          border: '1px solid rgba(255,255,255,0.1)', borderRadius: 16,
          transition: 'all 0.2s ease', boxShadow: '0 2px 12px rgba(100,130,170,0.08)',
        }}
        onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-4px)'; e.currentTarget.style.boxShadow = '0 8px 24px rgba(100,130,170,0.18)'; }}
        onMouseLeave={e => { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.boxShadow = '0 2px 12px rgba(100,130,170,0.08)'; }}
      >
        <Card.Body className="d-flex flex-column" style={{ padding: '1.25rem', cursor: 'pointer' }} onClick={() => setExpandedSkill(isExpanded ? null : skill.slug)}>
          {/* Header row */}
          <div className="d-flex align-items-start justify-content-between mb-2">
            <div className="d-flex align-items-center gap-2" style={{ minWidth: 0, flex: 1 }}>
              <div style={{ width: 36, height: 36, borderRadius: 8, background: 'rgba(99,102,241,0.12)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                <EngineIcon size={16} style={{ color: 'var(--color-primary, #6366f1)' }} />
              </div>
              <h6 className="mb-0" style={{ color: 'var(--color-foreground)', fontSize: '0.95rem', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {skill.name}
              </h6>
            </div>
            <div className="d-flex align-items-center gap-1" style={{ flexShrink: 0 }}>
              <Badge bg="dark" style={{ fontSize: '0.65rem', borderRadius: 6, padding: '3px 7px', textTransform: 'capitalize' }}>{skill.engine}</Badge>
              {skill.version && <Badge bg="secondary" style={{ fontSize: '0.62rem', borderRadius: 6, padding: '3px 6px' }}>{t('version', { version: skill.version })}</Badge>}
            </div>
          </div>

          {/* Category + Tier badges */}
          <div className="d-flex align-items-center gap-2 mb-2">
            <Badge style={{ background: catColor + '22', color: catColor, fontSize: '0.68rem', borderRadius: 6, padding: '3px 8px', border: `1px solid ${catColor}44` }}>
              {t(`categories.${skill.category}`) || skill.category}
            </Badge>
            <Badge bg={skill.tier === 'native' ? 'info' : skill.tier === 'custom' ? 'success' : 'warning'} style={{ fontSize: '0.62rem', borderRadius: 6, padding: '3px 6px' }}>
              {t(`tabs.${skill.tier === 'custom' ? 'mySkills' : skill.tier}`)}
            </Badge>
          </div>

          {/* Description */}
          {skill.description && (
            <p className="mb-2" style={{ fontSize: '0.82rem', color: 'var(--color-foreground-muted)', lineHeight: 1.5, display: isExpanded ? 'block' : '-webkit-box', WebkitLineClamp: isExpanded ? 'unset' : 2, WebkitBoxOrient: 'vertical', overflow: isExpanded ? 'visible' : 'hidden' }}>
              {skill.description}
            </p>
          )}

          {/* Auto-trigger / Chain indicators */}
          {skill.auto_trigger && <p className="mb-1" style={{ fontSize: '0.72rem', color: 'var(--color-foreground-muted)' }}><FaRocket size={10} className="me-1" />Auto-triggers on: {skill.auto_trigger}</p>}
          {skill.chain_to && skill.chain_to.length > 0 && <p className="mb-1" style={{ fontSize: '0.72rem', color: 'var(--color-foreground-muted)' }}><FaCodeBranch size={10} className="me-1" />Chains to: {skill.chain_to.join(', ')}</p>}

          {/* Expanded details */}
          {isExpanded && (
            <div className="mt-2 pt-2" style={{ borderTop: '1px solid rgba(255,255,255,0.08)' }}>
              {skill.inputs && skill.inputs.length > 0 && (
                <div className="mb-2">
                  <small className="text-muted fw-semibold d-block mb-1" style={{ fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.5px' }}>{t('inputs')}</small>
                  {skill.inputs.map(inp => (
                    <div key={inp.name} className="d-flex align-items-center gap-2 mb-1">
                      <code style={{ fontSize: '0.76rem', color: 'var(--color-primary, #6366f1)', background: 'rgba(99,102,241,0.08)', padding: '1px 6px', borderRadius: 4 }}>{inp.name}</code>
                      <span className="text-muted" style={{ fontSize: '0.7rem' }}>({inp.type || 'string'})</span>
                      <Badge bg={inp.required ? 'primary' : 'secondary'} style={{ fontSize: '0.6rem' }}>{inp.required ? t('required') : t('optional')}</Badge>
                    </div>
                  ))}
                </div>
              )}
              {skill.sub_prompts && skill.sub_prompts.length > 0 && (
                <div className="mb-2">
                  <small className="text-muted fw-semibold d-block mb-1" style={{ fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Sub-prompts</small>
                  {skill.sub_prompts.map((sp, i) => <div key={i} className="text-muted" style={{ fontSize: '0.75rem' }}>- {sp.name || sp}</div>)}
                </div>
              )}
              {skill.tags && skill.tags.length > 0 && (
                <div className="d-flex gap-1 flex-wrap">
                  {skill.tags.map(tag => <Badge key={tag} bg="dark" style={{ fontSize: '0.62rem', fontWeight: 400 }}>#{tag}</Badge>)}
                </div>
              )}
            </div>
          )}
        </Card.Body>

        {/* Footer */}
        <div className="d-flex align-items-center justify-content-between px-3 pb-3 pt-0">
          <Button variant="primary" size="sm" onClick={e => { e.stopPropagation(); handleOpenExecute(skill); }} style={{ borderRadius: 8, fontSize: '0.82rem' }}>
            <FaPlay className="me-1" size={10} /> {t('tryIt')}
          </Button>
          <div className="d-flex align-items-center gap-1">
            <span style={{ cursor: 'pointer', color: 'var(--color-foreground-muted)', padding: 4 }} onClick={e => { e.stopPropagation(); setExpandedSkill(isExpanded ? null : skill.slug); }}>
              {isExpanded ? <FaChevronUp size={12} /> : <FaChevronDown size={12} />}
            </span>
            <Dropdown onClick={e => e.stopPropagation()}>
              <Dropdown.Toggle as="span" style={{ cursor: 'pointer', color: 'var(--color-foreground-muted)', padding: 4 }}><FaEllipsisV size={12} /></Dropdown.Toggle>
              <Dropdown.Menu align="end" style={{ background: 'var(--surface-elevated)', border: '1px solid var(--color-border)', borderRadius: 10, minWidth: 180 }}>
                {skill.tier === 'native' && (
                  <>
                    <Dropdown.Item onClick={() => handleFork(skill)}><FaCodeBranch className="me-2" size={12} />{t('actions.fork')}</Dropdown.Item>
                    <Dropdown.Item onClick={() => setExpandedSkill(skill.slug)}><FaCode className="me-2" size={12} />{t('viewSource')}</Dropdown.Item>
                  </>
                )}
                {skill.tier === 'custom' && (
                  <>
                    <Dropdown.Item onClick={() => openEdit(skill)}><FaEdit className="me-2" size={12} />{t('actions.edit')}</Dropdown.Item>
                    <Dropdown.Item onClick={() => {}}><FaHistory className="me-2" size={12} />{t('actions.versions')}</Dropdown.Item>
                    <Dropdown.Divider />
                    <Dropdown.Item onClick={() => handleDelete(skill)} className="text-danger"><FaTrash className="me-2" size={12} />{t('actions.delete')}</Dropdown.Item>
                  </>
                )}
                {skill.tier === 'community' && (
                  <>
                    <Dropdown.Item onClick={() => handleFork(skill)}><FaCodeBranch className="me-2" size={12} />{t('actions.fork')}</Dropdown.Item>
                    <Dropdown.Item onClick={() => setExpandedSkill(skill.slug)}><FaCode className="me-2" size={12} />{t('viewSource')}</Dropdown.Item>
                    {skill.source_repo && <Dropdown.Item href={skill.source_repo} target="_blank" rel="noopener noreferrer"><FaGithub className="me-2" size={12} />{t('actions.viewGithub')}</Dropdown.Item>}
                  </>
                )}
              </Dropdown.Menu>
            </Dropdown>
          </div>
        </div>
      </Card>
    );
  };

  return (
    <Layout>
      <div className="py-4 px-3" style={{ maxWidth: 1200, margin: '0 auto' }}>
        {/* Header */}
        <div className="d-flex align-items-center justify-content-between mb-4 flex-wrap gap-2">
          <div className="d-flex align-items-center gap-3">
            <div style={{ width: 48, height: 48, borderRadius: 12, background: 'linear-gradient(135deg, rgba(99,102,241,0.2), rgba(139,92,246,0.2))', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <FaRocket size={22} style={{ color: 'var(--color-primary, #6366f1)' }} />
            </div>
            <div>
              <h2 className="mb-0" style={{ color: 'var(--color-foreground)', fontSize: '1.5rem' }}>{t('title')}</h2>
              <p className="text-muted mb-0 small">{t('subtitle')}</p>
            </div>
          </div>
          <div className="d-flex gap-2">
            <Button variant="outline-secondary" onClick={() => setShowImport(true)} style={{ borderRadius: 8 }}>
              <FaGithub className="me-2" size={14} />{t('actions.import')}
            </Button>
            <Button variant="primary" onClick={openCreate} style={{ borderRadius: 8 }}>
              <FaPlus className="me-2" size={12} />{t('createSkill')}
            </Button>
          </div>
        </div>

        {/* Search */}
        <InputGroup className="mb-3" style={{ maxWidth: 480 }}>
          <InputGroup.Text style={{ ...inputStyle, borderRight: 'none', borderTopRightRadius: 0, borderBottomRightRadius: 0 }}>
            <FaSearch size={14} style={{ color: 'var(--color-foreground-muted)' }} />
          </InputGroup.Text>
          <Form.Control
            placeholder={t('search.placeholder')}
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            style={{ ...inputStyle, borderLeft: 'none', borderTopLeftRadius: 0, borderBottomLeftRadius: 0 }}
          />
        </InputGroup>

        {/* Tabs */}
        <Nav variant="pills" className="mb-3 gap-1">
          {['native', 'my', 'community'].map(tab => (
            <Nav.Item key={tab}>
              <Nav.Link
                active={activeTab === tab}
                onClick={() => setActiveTab(tab)}
                style={{
                  borderRadius: 8, fontSize: '0.85rem', fontWeight: 500, cursor: 'pointer',
                  background: activeTab === tab ? 'var(--color-primary, #6366f1)' : 'rgba(255,255,255,0.05)',
                  color: activeTab === tab ? '#fff' : 'var(--color-foreground-muted)',
                  border: '1px solid ' + (activeTab === tab ? 'transparent' : 'rgba(255,255,255,0.1)'),
                }}
              >
                {tab === 'my' ? t('tabs.mySkills') : t(`tabs.${tab}`)}
              </Nav.Link>
            </Nav.Item>
          ))}
        </Nav>

        {/* Category chips */}
        <div className="d-flex gap-2 mb-4 flex-wrap">
          {CATEGORIES.map(cat => {
            const isActive = activeCategory === cat;
            const color = cat === 'all' ? '#6366f1' : (CATEGORY_COLORS[cat] || '#95a5a6');
            return (
              <Badge
                key={cat}
                role="button"
                onClick={() => setActiveCategory(cat)}
                style={{
                  fontSize: '0.78rem', fontWeight: 500, padding: '6px 14px', borderRadius: 20, cursor: 'pointer',
                  background: isActive ? color : 'rgba(255,255,255,0.06)',
                  color: isActive ? '#fff' : 'var(--color-foreground-muted)',
                  border: `1px solid ${isActive ? color : 'rgba(255,255,255,0.1)'}`,
                  transition: 'all 0.15s ease',
                }}
              >
                {t(`categories.${cat}`)}
              </Badge>
            );
          })}
        </div>

        {/* Alerts */}
        {error && <Alert variant="danger" onClose={() => setError(null)} dismissible className="mb-3">{error}</Alert>}
        {success && <Alert variant="success" onClose={() => setSuccess(null)} dismissible className="mb-3">{success}</Alert>}

        {/* Loading */}
        {loading && (
          <div className="text-center py-5">
            <Spinner animation="border" variant="primary" />
            <p className="text-muted mt-3 mb-0">{t('loading')}</p>
          </div>
        )}

        {/* Empty */}
        {!loading && filtered.length === 0 && (
          <Card className="text-center py-5" style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 16 }}>
            <Card.Body>
              <FaRocket size={48} className="mb-3" style={{ color: 'var(--color-foreground-muted)', opacity: 0.4 }} />
              <h5 style={{ color: 'var(--color-foreground)' }}>{t('noSkills')}</h5>
              <p className="text-muted mb-3">{t('noSkillsDesc')}</p>
              <Button variant="primary" onClick={openCreate}><FaPlus className="me-2" size={12} />{t('createSkill')}</Button>
            </Card.Body>
          </Card>
        )}

        {/* Grid */}
        {!loading && filtered.length > 0 && (
          <>
            <div className="mb-3">
              <Badge bg="secondary" style={{ fontSize: '0.8rem', fontWeight: 500, padding: '6px 12px', borderRadius: 8 }}>
                {filtered.length} {t('totalSkills')}
              </Badge>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(350px, 1fr))', gap: '1.25rem' }}>
              {filtered.map(skill => <SkillCard key={skill.slug || skill.name} skill={skill} />)}
            </div>
          </>
        )}

        {/* ── Execute Modal ── */}
        <Modal show={!!executeSkill} onHide={() => { setExecuteSkill(null); setExecuteResult(null); }} size="lg" centered>
          <Modal.Header closeButton style={{ ...modalSectionStyle, borderBottom: '1px solid var(--color-border)' }}>
            <Modal.Title style={{ fontSize: '1.1rem' }}><FaPlay className="me-2" size={14} />{t('execute.title')}: {executeSkill?.name}</Modal.Title>
          </Modal.Header>
          <Modal.Body style={modalSectionStyle}>
            {executeSkill?.inputs?.length > 0 ? (
              executeSkill.inputs.map(inp => (
                <Form.Group key={inp.name} className="mb-3">
                  <Form.Label style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>
                    {inp.name}{inp.required && <span className="text-danger ms-1">*</span>}
                    {inp.description && <span className="text-muted ms-2" style={{ fontSize: '0.75rem' }}> - {inp.description}</span>}
                  </Form.Label>
                  <Form.Control type="text" placeholder={`${t('execute.inputValue')}...`} value={executeInputs[inp.name] || ''} onChange={e => setExecuteInputs({ ...executeInputs, [inp.name]: e.target.value })} style={inputStyle} />
                </Form.Group>
              ))
            ) : (
              <p className="text-muted">{t('noSkillsDesc')}</p>
            )}
            {executeResult && (
              <div className="mt-3">
                <Alert variant={executeResult.success ? 'success' : 'danger'}><strong>{executeResult.success ? t('execute.success') : t('execute.error')}</strong></Alert>
                {executeResult.data && (
                  <pre style={{ background: 'rgba(0,0,0,0.3)', color: '#e2e8f0', padding: '1rem', borderRadius: 8, fontSize: '0.82rem', maxHeight: 300, overflow: 'auto' }}>
                    {JSON.stringify(executeResult.data, null, 2)}
                  </pre>
                )}
                {executeResult.error && typeof executeResult.error === 'string' && (
                  <pre style={{ background: 'rgba(220,53,69,0.1)', color: '#f87171', padding: '1rem', borderRadius: 8, fontSize: '0.82rem' }}>{executeResult.error}</pre>
                )}
              </div>
            )}
          </Modal.Body>
          <Modal.Footer style={{ ...modalSectionStyle, borderTop: '1px solid var(--color-border)' }}>
            <Button variant="outline-secondary" onClick={() => { setExecuteSkill(null); setExecuteResult(null); }} style={{ borderRadius: 8 }}>{t('close')}</Button>
            <Button variant="primary" onClick={handleExecute} disabled={executing} style={{ borderRadius: 8 }}>
              {executing ? <><Spinner animation="border" size="sm" className="me-2" style={{ width: 14, height: 14, borderWidth: 1.5 }} />{t('running')}</> : <><FaPlay className="me-2" size={10} />{t('execute.submit')}</>}
            </Button>
          </Modal.Footer>
        </Modal>

        {/* ── Create / Edit Modal ── */}
        <Modal show={showCreate} onHide={() => setShowCreate(false)} size="lg" centered>
          <Modal.Header closeButton style={{ ...modalSectionStyle, borderBottom: '1px solid var(--color-border)' }}>
            <Modal.Title style={{ fontSize: '1.1rem' }}>
              {editSkill ? <><FaEdit className="me-2" size={14} />{t('actions.edit')}</> : <><FaPlus className="me-2" size={14} />{t('createSkill')}</>}
            </Modal.Title>
          </Modal.Header>
          <Modal.Body style={modalSectionStyle}>
            <Row>
              <Col md={6}>
                <Form.Group className="mb-3">
                  <Form.Label style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>{t('form.name')} <span className="text-danger">*</span></Form.Label>
                  <Form.Control type="text" placeholder={t('form.namePlaceholder')} value={newSkill.name} onChange={e => setNewSkill({ ...newSkill, name: e.target.value })} style={inputStyle} />
                </Form.Group>
              </Col>
              <Col md={3}>
                <Form.Group className="mb-3">
                  <Form.Label style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>{t('form.engine')}</Form.Label>
                  <div className="d-flex gap-2 mt-1">
                    {['python', 'shell', 'markdown'].map(eng => {
                      const Icon = ENGINE_ICONS[eng];
                      return (
                        <div
                          key={eng}
                          role="button"
                          onClick={() => setNewSkill({ ...newSkill, engine: eng, script: newSkill.script === ENGINE_DEFAULTS[newSkill.engine] ? ENGINE_DEFAULTS[eng] : newSkill.script })}
                          style={{
                            padding: '6px 10px', borderRadius: 8, cursor: 'pointer', textAlign: 'center', fontSize: '0.72rem', fontWeight: 500,
                            background: newSkill.engine === eng ? 'var(--color-primary, #6366f1)' : 'rgba(255,255,255,0.05)',
                            color: newSkill.engine === eng ? '#fff' : 'var(--color-foreground-muted)',
                            border: `1px solid ${newSkill.engine === eng ? 'transparent' : 'rgba(255,255,255,0.1)'}`,
                          }}
                        >
                          <Icon size={14} className="d-block mx-auto mb-1" />
                          {eng.charAt(0).toUpperCase() + eng.slice(1)}
                        </div>
                      );
                    })}
                  </div>
                </Form.Group>
              </Col>
              <Col md={3}>
                <Form.Group className="mb-3">
                  <Form.Label style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>{t('form.category')}</Form.Label>
                  <Form.Select value={newSkill.category} onChange={e => setNewSkill({ ...newSkill, category: e.target.value })} style={inputStyle}>
                    {CATEGORIES.filter(c => c !== 'all').map(c => <option key={c} value={c}>{t(`categories.${c}`)}</option>)}
                  </Form.Select>
                </Form.Group>
              </Col>
            </Row>

            <Form.Group className="mb-3">
              <Form.Label style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>{t('form.description')}</Form.Label>
              <Form.Control as="textarea" rows={2} placeholder={t('form.descriptionPlaceholder')} value={newSkill.description} onChange={e => setNewSkill({ ...newSkill, description: e.target.value })} style={inputStyle} />
            </Form.Group>

            <Row>
              <Col md={6}>
                <Form.Group className="mb-3">
                  <Form.Label style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>{t('form.autoTrigger')}</Form.Label>
                  <Form.Control type="text" placeholder={t('form.autoTriggerPlaceholder')} value={newSkill.auto_trigger} onChange={e => setNewSkill({ ...newSkill, auto_trigger: e.target.value })} style={inputStyle} />
                </Form.Group>
              </Col>
              <Col md={6}>
                <Form.Group className="mb-3">
                  <Form.Label style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>{t('form.tags')}</Form.Label>
                  <Form.Control type="text" placeholder={t('form.tagsPlaceholder')} value={newSkill.tags} onChange={e => setNewSkill({ ...newSkill, tags: e.target.value })} style={inputStyle} />
                </Form.Group>
              </Col>
            </Row>

            <Form.Group className="mb-3">
              <Form.Label style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>{t('form.chainTo')}</Form.Label>
              <Form.Select multiple value={newSkill.chain_to} onChange={e => setNewSkill({ ...newSkill, chain_to: Array.from(e.target.selectedOptions, o => o.value) })} style={{ ...inputStyle, minHeight: 60 }}>
                {skills.filter(s => s.slug !== editSkill?.slug).map(s => <option key={s.slug} value={s.slug}>{s.name}</option>)}
              </Form.Select>
              <Form.Text className="text-muted" style={{ fontSize: '0.72rem' }}>{t('form.chainToPlaceholder')}</Form.Text>
            </Form.Group>

            {/* Inputs builder */}
            <div className="mb-3">
              <div className="d-flex align-items-center justify-content-between mb-2">
                <Form.Label className="mb-0" style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>{t('form.inputsSection')}</Form.Label>
                <Button variant="outline-primary" size="sm" onClick={handleAddInput} style={{ borderRadius: 6, fontSize: '0.78rem' }}><FaPlus className="me-1" size={10} />{t('form.addInput')}</Button>
              </div>
              {newSkill.inputs.map((inp, i) => (
                <div key={i} className="d-flex align-items-center gap-2 mb-2 p-2" style={{ background: 'rgba(100,130,170,0.05)', borderRadius: 8, border: '1px solid var(--color-border)' }}>
                  <Form.Control size="sm" placeholder={t('form.inputName')} value={inp.name} onChange={e => handleInputChange(i, 'name', e.target.value)} style={{ ...inputStyle, borderRadius: 6, flex: '0 0 130px' }} />
                  <Form.Select size="sm" value={inp.type} onChange={e => handleInputChange(i, 'type', e.target.value)} style={{ ...inputStyle, borderRadius: 6, flex: '0 0 90px' }}>
                    <option value="string">string</option>
                    <option value="number">number</option>
                    <option value="boolean">boolean</option>
                  </Form.Select>
                  <Form.Control size="sm" placeholder={t('form.inputDescription')} value={inp.description} onChange={e => handleInputChange(i, 'description', e.target.value)} style={{ ...inputStyle, borderRadius: 6, flex: 1 }} />
                  <Form.Check type="switch" label={t('form.inputRequired')} checked={inp.required} onChange={e => handleInputChange(i, 'required', e.target.checked)} style={{ fontSize: '0.72rem', whiteSpace: 'nowrap' }} />
                  <Button variant="outline-danger" size="sm" onClick={() => handleRemoveInput(i)} style={{ borderRadius: 6, padding: '2px 8px' }}><FaTimes size={10} /></Button>
                </div>
              ))}
            </div>

            {/* Script */}
            <Form.Group className="mb-3">
              <Form.Label style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>{t('form.scriptContent')}</Form.Label>
              <Form.Control as="textarea" rows={10} value={newSkill.script} onChange={e => setNewSkill({ ...newSkill, script: e.target.value })} style={{ background: 'rgba(0,0,0,0.3)', border: '1px solid var(--color-border)', color: '#e2e8f0', borderRadius: 8, fontFamily: 'monospace', fontSize: '0.82rem', lineHeight: 1.6 }} />
            </Form.Group>
          </Modal.Body>
          <Modal.Footer style={{ ...modalSectionStyle, borderTop: '1px solid var(--color-border)' }}>
            <Button variant="outline-secondary" onClick={() => setShowCreate(false)} style={{ borderRadius: 8 }}>{t('cancel')}</Button>
            <Button variant="primary" onClick={handleSaveSkill} disabled={creating || !newSkill.name.trim()} style={{ borderRadius: 8 }}>
              {creating ? <><Spinner animation="border" size="sm" className="me-2" style={{ width: 14, height: 14, borderWidth: 1.5 }} />{t('creating')}</> : <><FaPlus className="me-2" size={10} />{editSkill ? t('actions.edit') : t('create')}</>}
            </Button>
          </Modal.Footer>
        </Modal>

        {/* ── GitHub Import Modal ── */}
        <Modal show={showImport} onHide={() => setShowImport(false)} centered>
          <Modal.Header closeButton style={{ ...modalSectionStyle, borderBottom: '1px solid var(--color-border)' }}>
            <Modal.Title style={{ fontSize: '1.1rem' }}><FaGithub className="me-2" size={18} />{t('actions.import')}</Modal.Title>
          </Modal.Header>
          <Modal.Body style={modalSectionStyle}>
            <p className="text-muted small mb-3">Paste a GitHub repo URL containing skill directories (each with a <code>skill.md</code> file).</p>
            <Form.Group>
              <Form.Label style={{ fontSize: '0.85rem', color: 'var(--color-foreground)' }}>Repository URL</Form.Label>
              <Form.Control type="text" placeholder="https://github.com/owner/repo or owner/repo/path/to/skills" value={importUrl} onChange={e => setImportUrl(e.target.value)} style={inputStyle} />
              <Form.Text className="text-muted" style={{ fontSize: '0.75rem' }}>Supports: full URLs, <code>owner/repo</code>, or <code>owner/repo/path/to/skill</code></Form.Text>
            </Form.Group>
          </Modal.Body>
          <Modal.Footer style={{ ...modalSectionStyle, borderTop: '1px solid var(--color-border)' }}>
            <Button variant="outline-secondary" onClick={() => setShowImport(false)} style={{ borderRadius: 8 }}>{t('cancel')}</Button>
            <Button variant="primary" onClick={handleImportGithub} disabled={importing || !importUrl.trim()} style={{ borderRadius: 8 }}>
              {importing ? <><Spinner animation="border" size="sm" className="me-2" style={{ width: 14, height: 14, borderWidth: 1.5 }} />Importing...</> : <><FaGithub className="me-2" size={14} />Import</>}
            </Button>
          </Modal.Footer>
        </Modal>
      </div>
    </Layout>
  );
};

export default SkillsPage;
