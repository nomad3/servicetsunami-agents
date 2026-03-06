import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Container, Spinner } from 'react-bootstrap';
import { FaCloudUploadAlt, FaFileAlt, FaLightbulb, FaPlus, FaSearch, FaTrash } from 'react-icons/fa';
import EntityCard from '../components/memory/EntityCard';
import EntityCreateModal from '../components/memory/EntityCreateModal';
import EntityStatsBar from '../components/memory/EntityStatsBar';
import OverviewTab from '../components/memory/OverviewTab';
import MemoriesTab from '../components/memory/MemoriesTab';
import ActivityFeed from '../components/memory/ActivityFeed';
import { ALL_CATEGORIES, ALL_STATUSES, getCategoryConfig } from '../components/memory/constants';
import Layout from '../components/Layout';
import api from '../services/api';
import { memoryService } from '../services/memory';
import './MemoryPage.css';

const PAGE_SIZE = 50;

function MemoryPage() {
  // ── State ────────────────────────────────────────────────────
  const [entities, setEntities] = useState([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [hasMore, setHasMore] = useState(false);
  const [offset, setOffset] = useState(0);

  // UI state
  const [expandedId, setExpandedId] = useState(null);
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [activeTab, setActiveTab] = useState('overview');

  // Import state
  const [importing, setImporting] = useState(false);
  const [importMessage, setImportMessage] = useState(null);

  // ── Load entities ────────────────────────────────────────────
  const loadEntities = useCallback(async (reset = true) => {
    try {
      setLoading(true);
      const skip = reset ? 0 : offset;
      const data = await memoryService.getEntities({
        category: categoryFilter || undefined,
        status: statusFilter || undefined,
        skip,
        limit: PAGE_SIZE,
      });
      const items = data || [];
      if (reset) {
        setEntities(items);
        setOffset(items.length);
      } else {
        setEntities(prev => [...prev, ...items]);
        setOffset(prev => prev + items.length);
      }
      setHasMore(items.length === PAGE_SIZE);
    } catch (error) {
      console.error('Failed to load entities:', error);
    } finally {
      setLoading(false);
    }
  }, [categoryFilter, statusFilter, offset]);

  useEffect(() => {
    loadEntities(true);
  }, [categoryFilter, statusFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Search ───────────────────────────────────────────────────
  const handleSearch = async () => {
    if (!searchQuery.trim()) {
      loadEntities(true);
      return;
    }
    try {
      setLoading(true);
      const data = await memoryService.searchEntities(searchQuery, {
        category: categoryFilter || undefined,
      });
      setEntities(data || []);
      setHasMore(false);
    } catch (error) {
      console.error('Search failed:', error);
    } finally {
      setLoading(false);
    }
  };

  // ── CRUD handlers ────────────────────────────────────────────
  const handleCreate = async (data) => {
    await memoryService.createEntity(data);
    loadEntities(true);
  };

  const handleUpdate = async (id, data) => {
    await memoryService.updateEntity(id, data);
    setEntities(prev => prev.map(e =>
      e.id === id ? { ...e, ...data } : e
    ));
  };

  const handleDelete = async (id) => {
    await memoryService.deleteEntity(id);
    setEntities(prev => prev.filter(e => e.id !== id));
    setSelectedIds(prev => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
    setExpandedId(prev => prev === id ? null : prev);
  };

  const handleBulkDelete = async () => {
    if (selectedIds.size === 0) return;
    const ids = Array.from(selectedIds);
    try {
      await memoryService.bulkDeleteEntities(ids);
      setEntities(prev => prev.filter(e => !selectedIds.has(e.id)));
      setSelectedIds(new Set());
    } catch (error) {
      console.error('Bulk delete failed:', error);
    }
  };

  const handleStatusChange = async (id, status) => {
    await memoryService.updateEntityStatus(id, status);
    setEntities(prev => prev.map(e =>
      e.id === id ? { ...e, status } : e
    ));
  };

  const handleScore = async (id) => {
    try {
      const result = await memoryService.scoreEntity(id);
      setEntities(prev => prev.map(e =>
        e.id === id ? { ...e, score: result.score, scored_at: result.scored_at } : e
      ));
    } catch (error) {
      console.error('Score failed:', error);
    }
  };

  // ── Selection ────────────────────────────────────────────────
  const toggleSelect = (id) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedIds.size === entities.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(entities.map(e => e.id)));
    }
  };

  // ── Import ───────────────────────────────────────────────────
  const handleImport = async (event, provider) => {
    const file = event.target.files[0];
    if (!file) return;
    setImporting(true);
    setImportMessage(null);
    const formData = new FormData();
    formData.append('file', file);
    try {
      const endpoint = provider === 'chatgpt'
        ? '/integrations/import/chatgpt'
        : '/integrations/import/claude';
      const response = await api.post(endpoint, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setImportMessage({ type: 'success', text: response.data.message });
    } catch (error) {
      setImportMessage({
        type: 'danger',
        text: error.response?.data?.detail || 'Failed to import chat history',
      });
    } finally {
      setImporting(false);
      event.target.value = null;
    }
  };

  // ── Render ───────────────────────────────────────────────────
  return (
    <Layout>
      <Container fluid className="py-2">
        {/* Page Header */}
        <div className="memory-page-header">
          <div>
            <h2 className="page-title">Memory</h2>
            <p className="page-subtitle">What Luna knows, remembers, and has learned from your conversations</p>
          </div>
          <Button variant="primary" size="sm" onClick={() => setShowCreateModal(true)}>
            <FaPlus size={11} className="me-1" /> Add Entity
          </Button>
        </div>

        {/* Tabs */}
        <div className="memory-tabs">
          {['overview', 'entities', 'memories', 'activity', 'import'].map(tab => (
            <button
              key={tab}
              className={`memory-tab-btn ${activeTab === tab ? 'active' : ''}`}
              onClick={() => setActiveTab(tab)}
            >
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
            </button>
          ))}
        </div>

        {activeTab === 'overview' && <OverviewTab />}

        {activeTab === 'entities' && (
          <>
            {/* Stats Bar */}
            {!loading && entities.length > 0 && (
              <EntityStatsBar entities={entities} />
            )}

            {/* Filter Bar */}
            <div className="memory-filter-bar">
              <div className="search-wrapper">
                <FaSearch className="search-icon" />
                <input
                  className="search-input"
                  placeholder="Search entities..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                />
              </div>
              <select
                className="filter-select"
                value={categoryFilter}
                onChange={(e) => setCategoryFilter(e.target.value)}
              >
                <option value="">All Categories</option>
                {ALL_CATEGORIES.map(c => {
                  const cfg = getCategoryConfig(c);
                  return <option key={c} value={c}>{cfg.label}</option>;
                })}
              </select>
              <select
                className="filter-select"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
              >
                <option value="">All Statuses</option>
                {ALL_STATUSES.map(s => (
                  <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
                ))}
              </select>

              <div className="bulk-actions">
                <label className="select-all-wrapper">
                  <input
                    type="checkbox"
                    checked={entities.length > 0 && selectedIds.size === entities.length}
                    onChange={toggleSelectAll}
                    className="entity-checkbox"
                  />
                  Select all
                </label>
                {selectedIds.size > 0 && (
                  <Button variant="outline-danger" size="sm" onClick={handleBulkDelete}>
                    <FaTrash size={11} className="me-1" />
                    Delete {selectedIds.size}
                  </Button>
                )}
              </div>
            </div>

            {/* Entity Grid */}
            {loading && entities.length === 0 ? (
              <div className="text-center py-5">
                <Spinner animation="border" size="sm" className="text-muted" />
              </div>
            ) : entities.length === 0 ? (
              <div className="memory-empty">
                <div className="memory-empty-icon"><FaLightbulb /></div>
                <p>No entities found. Chat with your AI assistant to start building the knowledge base, or add entities manually.</p>
              </div>
            ) : (
              <>
                <div className="entity-grid">
                  {entities.map(entity => (
                    <EntityCard
                      key={entity.id}
                      entity={entity}
                      isExpanded={expandedId === entity.id}
                      isSelected={selectedIds.has(entity.id)}
                      onToggleExpand={(id) => setExpandedId(prev => prev === id ? null : id)}
                      onToggleSelect={toggleSelect}
                      onUpdate={handleUpdate}
                      onDelete={handleDelete}
                      onScore={handleScore}
                      onStatusChange={handleStatusChange}
                    />
                  ))}
                </div>

                {hasMore && (
                  <div className="memory-load-more">
                    <Button
                      variant="outline-secondary"
                      size="sm"
                      onClick={() => loadEntities(false)}
                      disabled={loading}
                    >
                      {loading ? <Spinner size="sm" animation="border" /> : 'Load More'}
                    </Button>
                  </div>
                )}
              </>
            )}
          </>
        )}

        {activeTab === 'memories' && <MemoriesTab />}

        {activeTab === 'activity' && <ActivityFeed />}

        {activeTab === 'import' && (
          <div style={{ maxWidth: 700 }}>
            <h5 className="mb-1" style={{ color: 'var(--color-foreground)' }}>Import Chat History</h5>
            <p className="text-muted small mb-3">Upload chat exports from other LLM providers to build your knowledge base.</p>

            {importMessage && (
              <Alert variant={importMessage.type} dismissible onClose={() => setImportMessage(null)}>
                {importMessage.text}
              </Alert>
            )}

            <div className="d-flex gap-3 flex-wrap">
              {[
                { id: 'chatgpt', label: 'ChatGPT Export', file: 'conversations.json', color: '#34d399', provider: 'chatgpt' },
                { id: 'claude', label: 'Claude Export', file: 'conversations.json', color: '#fbbf24', provider: 'claude' },
              ].map(imp => (
                <div
                  key={imp.id}
                  style={{
                    flex: 1,
                    minWidth: 260,
                    padding: '1.5rem',
                    border: '1px solid var(--color-border)',
                    borderRadius: 10,
                    background: 'var(--surface-elevated)',
                    textAlign: 'center',
                  }}
                >
                  <FaFileAlt size={36} style={{ color: imp.color, marginBottom: '0.75rem' }} />
                  <h6 style={{ color: 'var(--color-foreground)' }}>{imp.label}</h6>
                  <p className="text-muted small mb-3">Upload your <code>{imp.file}</code></p>
                  <input
                    type="file"
                    id={`${imp.id}-upload`}
                    accept=".json"
                    style={{ display: 'none' }}
                    onChange={(e) => handleImport(e, imp.provider)}
                    disabled={importing}
                  />
                  <Button
                    variant={`outline-${imp.id === 'chatgpt' ? 'success' : 'warning'}`}
                    size="sm"
                    onClick={() => document.getElementById(`${imp.id}-upload`).click()}
                    disabled={importing}
                  >
                    {importing ? <Spinner animation="border" size="sm" /> : <><FaCloudUploadAlt className="me-1" /> Upload</>}
                  </Button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Create Modal */}
        <EntityCreateModal
          show={showCreateModal}
          onHide={() => setShowCreateModal(false)}
          onCreate={handleCreate}
        />
      </Container>
    </Layout>
  );
}

export default MemoryPage;
