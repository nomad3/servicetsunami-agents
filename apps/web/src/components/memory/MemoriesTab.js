import React, { useEffect, useState } from 'react';
import { Spinner } from 'react-bootstrap';
import { FaBrain } from 'react-icons/fa';
import { getMemoryTypeConfig, ALL_MEMORY_TYPES } from './constants';
import MemoryCard from './MemoryCard';
import { memoryService } from '../../services/memory';

const MemoriesTab = () => {
  const [memories, setMemories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [typeFilter, setTypeFilter] = useState('');

  useEffect(() => {
    loadMemories();
  }, [typeFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadMemories = async () => {
    try {
      setLoading(true);
      const data = await memoryService.getTenantMemories({
        memoryType: typeFilter || undefined,
        limit: 100,
      });
      setMemories(data || []);
    } catch (err) {
      console.error('Failed to load memories:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleUpdate = async (id, data) => {
    await memoryService.updateMemoryItem(id, data);
    setMemories(prev => prev.map(m => m.id === id ? { ...m, ...data } : m));
  };

  const handleDelete = async (id) => {
    await memoryService.deleteMemoryItem(id);
    setMemories(prev => prev.filter(m => m.id !== id));
  };

  if (loading) {
    return (
      <div className="text-center py-5">
        <Spinner animation="border" size="sm" className="text-muted" />
      </div>
    );
  }

  // Group memories by type
  const grouped = {};
  memories.forEach(m => {
    const type = m.memory_type || 'fact';
    if (!grouped[type]) grouped[type] = [];
    grouped[type].push(m);
  });

  // Sort types by count
  const sortedTypes = Object.keys(grouped).sort((a, b) => grouped[b].length - grouped[a].length);

  if (sortedTypes.length === 0) {
    return (
      <div className="memory-empty">
        <div className="memory-empty-icon"><FaBrain /></div>
        <p>No memories yet. Chat with Luna -- she'll learn your preferences, facts, and decisions over time.</p>
      </div>
    );
  }

  return (
    <div className="memories-tab">
      <div className="memories-tab-header">
        <p className="memories-subtitle">What Luna knows about you</p>
        <select
          className="filter-select"
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
        >
          <option value="">All Types</option>
          {ALL_MEMORY_TYPES.map(t => {
            const cfg = getMemoryTypeConfig(t);
            return <option key={t} value={t}>{cfg.label}</option>;
          })}
        </select>
      </div>
      {sortedTypes.map(type => {
        const cfg = getMemoryTypeConfig(type);
        const TypeIcon = cfg.icon;
        return (
          <div key={type} className="memory-type-group">
            <div className="memory-type-header">
              <TypeIcon size={14} style={{ color: cfg.color }} />
              <span className="memory-type-label">{cfg.label}</span>
              <span className="memory-type-count" style={{ color: cfg.color }}>{grouped[type].length}</span>
            </div>
            <div className="memory-type-cards">
              {grouped[type].map(mem => (
                <MemoryCard
                  key={mem.id}
                  memory={mem}
                  onUpdate={handleUpdate}
                  onDelete={handleDelete}
                />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default MemoriesTab;
