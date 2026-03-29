import React, { useEffect, useState } from 'react';
import { Spinner } from 'react-bootstrap';
import { FaComments, FaSmile, FaMeh, FaFrown, FaStar } from 'react-icons/fa';
import { memoryService } from '../../services/memory';

const MOOD_CONFIG = {
  positive:    { icon: FaSmile, color: '#34d399', label: 'Positive' },
  neutral:     { icon: FaMeh,   color: '#94a3b8', label: 'Neutral' },
  negative:    { icon: FaFrown, color: '#f87171', label: 'Negative' },
  playful:     { icon: FaSmile, color: '#fbbf24', label: 'Playful' },
  focused:     { icon: FaStar,  color: '#60a5fa', label: 'Focused' },
  empathetic:  { icon: FaSmile, color: '#f472b6', label: 'Empathetic' },
};

const getMoodConfig = (mood) => {
  return MOOD_CONFIG[mood?.toLowerCase()] || { icon: FaMeh, color: '#94a3b8', label: mood || 'Unknown' };
};

const CHANNEL_OPTIONS = ['web', 'whatsapp'];
const MOOD_OPTIONS = Object.keys(MOOD_CONFIG);

const PAGE_SIZE = 30;

const EpisodesTab = () => {
  const [episodes, setEpisodes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [channelFilter, setChannelFilter] = useState('');
  const [moodFilter, setMoodFilter] = useState('');
  const [hasMore, setHasMore] = useState(false);
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    loadEpisodes(true);
  }, [channelFilter, moodFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadEpisodes = async (reset = true) => {
    try {
      setLoading(true);
      const skip = reset ? 0 : offset;
      const data = await memoryService.getEpisodes({
        sourceChannel: channelFilter || undefined,
        mood: moodFilter || undefined,
        skip,
        limit: PAGE_SIZE,
      });
      const items = data || [];
      if (reset) {
        setEpisodes(items);
        setOffset(items.length);
      } else {
        setEpisodes(prev => [...prev, ...items]);
        setOffset(prev => prev + items.length);
      }
      setHasMore(items.length === PAGE_SIZE);
    } catch (err) {
      console.error('Failed to load episodes:', err);
    } finally {
      setLoading(false);
    }
  };

  const formatDate = (dateStr) => {
    if (!dateStr) return '';
    return new Date(dateStr).toLocaleDateString(undefined, {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  };

  return (
    <div className="episodes-tab">
      <div className="episodes-tab-header">
        <p className="episodes-tab-subtitle">
          Conversation episodes -- summaries of what Luna discussed and learned
        </p>
        <div className="episodes-filters">
          <select
            className="filter-select"
            value={channelFilter}
            onChange={(e) => setChannelFilter(e.target.value)}
          >
            <option value="">All Channels</option>
            {CHANNEL_OPTIONS.map(c => (
              <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>
            ))}
          </select>
          <select
            className="filter-select"
            value={moodFilter}
            onChange={(e) => setMoodFilter(e.target.value)}
          >
            <option value="">All Moods</option>
            {MOOD_OPTIONS.map(m => (
              <option key={m} value={m}>{m.charAt(0).toUpperCase() + m.slice(1)}</option>
            ))}
          </select>
        </div>
      </div>

      {loading && episodes.length === 0 ? (
        <div className="text-center py-5">
          <Spinner animation="border" size="sm" className="text-muted" />
        </div>
      ) : episodes.length === 0 ? (
        <div className="memory-empty">
          <div className="memory-empty-icon"><FaComments /></div>
          <p>No episodes yet. Luna creates episode summaries as conversations progress.</p>
        </div>
      ) : (
        <>
          <div className="episodes-list">
            {episodes.map(ep => {
              const moodCfg = getMoodConfig(ep.mood);
              const MoodIcon = moodCfg.icon;
              return (
                <div key={ep.id} className="episode-card">
                  <div className="episode-card-header">
                    <div className="episode-card-mood" style={{ color: moodCfg.color }}>
                      <MoodIcon size={16} />
                    </div>
                    <div className="episode-card-info">
                      <div className="episode-card-summary">{ep.summary}</div>
                      <div className="episode-card-meta">
                        {ep.mood && (
                          <span className="episode-badge" style={{ background: moodCfg.color + '20', color: moodCfg.color }}>
                            {moodCfg.label}
                          </span>
                        )}
                        {ep.source_channel && (
                          <span className="episode-badge channel">
                            {ep.source_channel}
                          </span>
                        )}
                        {ep.message_count > 0 && (
                          <span className="episode-msg-count">
                            {ep.message_count} messages
                          </span>
                        )}
                        {ep.outcome && (
                          <span className="episode-outcome">{ep.outcome}</span>
                        )}
                        <span className="episode-date">{formatDate(ep.created_at)}</span>
                      </div>
                    </div>
                  </div>

                  {/* Topics and entities */}
                  {((ep.key_topics && ep.key_topics.length > 0) || (ep.key_entities && ep.key_entities.length > 0)) && (
                    <div className="episode-card-tags">
                      {(ep.key_topics || []).map((topic, i) => (
                        <span key={`t-${i}`} className="episode-tag topic">{topic}</span>
                      ))}
                      {(ep.key_entities || []).map((entity, i) => (
                        <span key={`e-${i}`} className="episode-tag entity">{entity}</span>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {hasMore && (
            <div className="memory-load-more">
              <button
                className="btn btn-outline-secondary btn-sm"
                onClick={() => loadEpisodes(false)}
                disabled={loading}
              >
                {loading ? <Spinner size="sm" animation="border" /> : 'Load More'}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default EpisodesTab;
