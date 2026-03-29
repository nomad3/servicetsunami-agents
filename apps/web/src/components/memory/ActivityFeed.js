import React, { useEffect, useState } from 'react';
import { Spinner } from 'react-bootstrap';
import { FaHistory } from 'react-icons/fa';
import { getActivityEventConfig, ACTIVITY_EVENT_CONFIG, ALL_ACTIVITY_SOURCES } from './constants';
import { memoryService } from '../../services/memory';

const ActivityFeed = () => {
  const [activities, setActivities] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sourceFilter, setSourceFilter] = useState('');
  const [eventTypeFilter, setEventTypeFilter] = useState('');
  const [hasMore, setHasMore] = useState(false);
  const [offset, setOffset] = useState(0);
  const PAGE_SIZE = 30;

  useEffect(() => {
    loadActivities(true);
  }, [sourceFilter, eventTypeFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadActivities = async (reset = true) => {
    try {
      setLoading(true);
      const skip = reset ? 0 : offset;
      const data = await memoryService.getActivityFeed({
        source: sourceFilter || undefined,
        eventType: eventTypeFilter || undefined,
        skip,
        limit: PAGE_SIZE,
      });
      const items = data || [];
      if (reset) {
        setActivities(items);
        setOffset(items.length);
      } else {
        setActivities(prev => [...prev, ...items]);
        setOffset(prev => prev + items.length);
      }
      setHasMore(items.length === PAGE_SIZE);
    } catch (err) {
      console.error('Failed to load activity:', err);
    } finally {
      setLoading(false);
    }
  };

  // Group by date
  const groupByDate = (items) => {
    const groups = {};
    const today = new Date().toDateString();
    const yesterday = new Date(Date.now() - 86400000).toDateString();
    items.forEach(item => {
      const dateStr = new Date(item.created_at).toDateString();
      let label = dateStr;
      if (dateStr === today) label = 'Today';
      else if (dateStr === yesterday) label = 'Yesterday';
      else label = new Date(item.created_at).toLocaleDateString(undefined, { month: 'long', day: 'numeric' });
      if (!groups[label]) groups[label] = [];
      groups[label].push(item);
    });
    return groups;
  };

  const grouped = groupByDate(activities);

  return (
    <div className="activity-feed-tab">
      <div className="activity-feed-header">
        <p className="activity-feed-subtitle">Luna's activity log</p>
        <div className="activity-feed-filters">
          <select
            className="filter-select"
            value={eventTypeFilter}
            onChange={(e) => setEventTypeFilter(e.target.value)}
          >
            <option value="">All Events</option>
            {Object.entries(ACTIVITY_EVENT_CONFIG).map(([key, cfg]) => (
              <option key={key} value={key}>{cfg.label}</option>
            ))}
          </select>
          <select
            className="filter-select"
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
          >
            <option value="">All Sources</option>
            {ALL_ACTIVITY_SOURCES.map(s => (
              <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
            ))}
          </select>
        </div>
      </div>

      {loading && activities.length === 0 ? (
        <div className="text-center py-5">
          <Spinner animation="border" size="sm" className="text-muted" />
        </div>
      ) : activities.length === 0 ? (
        <div className="memory-empty">
          <div className="memory-empty-icon"><FaHistory /></div>
          <p>No activity recorded yet. Luna will log her actions as she learns from your conversations.</p>
        </div>
      ) : (
        <>
          {Object.entries(grouped).map(([dateLabel, items]) => (
            <div key={dateLabel} className="activity-date-group">
              <div className="activity-date-label">{dateLabel}</div>
              {items.map(item => {
                const cfg = getActivityEventConfig(item.event_type);
                const EventIcon = cfg.icon;
                return (
                  <div key={item.id} className="activity-feed-item">
                    <div className="activity-feed-time">
                      {new Date(item.created_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}
                    </div>
                    <div className="activity-feed-icon" style={{ color: cfg.color }}>
                      <EventIcon size={13} />
                    </div>
                    <div className="activity-feed-content">
                      <span className="activity-feed-badge" style={{ background: cfg.color + '20', color: cfg.color }}>
                        {cfg.label}
                      </span>
                      <span className="activity-feed-desc">{item.description}</span>
                    </div>
                    {item.source && (
                      <span className="activity-source">{item.source}</span>
                    )}
                  </div>
                );
              })}
            </div>
          ))}

          {hasMore && (
            <div className="memory-load-more">
              <button
                className="btn btn-outline-secondary btn-sm"
                onClick={() => loadActivities(false)}
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

export default ActivityFeed;
