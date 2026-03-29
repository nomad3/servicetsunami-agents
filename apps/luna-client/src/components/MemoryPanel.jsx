import React, { useState, useEffect } from 'react';
import { apiJson } from '../api';

export default function MemoryPanel({ visible, onClose }) {
  const [episodes, setEpisodes] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!visible) return;
    setLoading(true);
    apiJson('/api/v1/chat/episodes?limit=10')
      .then(setEpisodes)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [visible]);

  if (!visible) return null;

  const timeAgo = (dateStr) => {
    if (!dateStr) return '';
    const diff = Date.now() - new Date(dateStr).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  };

  const moodIcon = (mood) => {
    switch (mood) {
      case 'positive': return '+';
      case 'frustrated': return '!';
      case 'curious': return '?';
      default: return '-';
    }
  };

  const channelLabel = (ch) => {
    if (!ch) return 'chat';
    return ch;
  };

  return (
    <div className="memory-panel">
      <div className="memory-panel-header">
        <span>Recent Memory</span>
        <button className="notif-close" onClick={onClose}>x</button>
      </div>
      <div className="memory-panel-body">
        {loading && <p className="notif-empty">Loading...</p>}
        {!loading && episodes.length === 0 && <p className="notif-empty">No episodes yet</p>}
        {episodes.map(ep => (
          <div key={ep.id} className="episode-card">
            <div className="episode-meta">
              <span className={`episode-mood mood-${ep.mood || 'neutral'}`}>{moodIcon(ep.mood)}</span>
              <span className="episode-channel">{channelLabel(ep.source_channel)}</span>
              <span className="episode-time">{timeAgo(ep.created_at)}</span>
            </div>
            <p className="episode-summary">{ep.summary}</p>
            {ep.key_entities?.length > 0 && (
              <div className="episode-entities">
                {ep.key_entities.slice(0, 5).map((e, i) => (
                  <span key={i} className="memory-tag">{e}</span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
