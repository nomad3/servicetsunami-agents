import React, { useEffect, useState } from 'react';
import { Spinner } from 'react-bootstrap';
import { FaBrain, FaUsers, FaProjectDiagram, FaCalendarDay, FaComments, FaEye } from 'react-icons/fa';
import { getActivityEventConfig } from './constants';
import { memoryService } from '../../services/memory';

const OverviewTab = () => {
  const [stats, setStats] = useState(null);
  const [activity, setActivity] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      const [statsData, activityData] = await Promise.all([
        memoryService.getMemoryStats(),
        memoryService.getActivityFeed({ limit: 10 }),
      ]);
      setStats(statsData);
      setActivity(activityData || []);
    } catch (err) {
      console.error('Failed to load overview:', err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="text-center py-5">
        <Spinner animation="border" size="sm" className="text-muted" />
      </div>
    );
  }

  const statTiles = [
    { label: 'Entities', value: stats?.total_entities || 0, icon: FaUsers, color: '#60a5fa' },
    { label: 'Memories', value: stats?.total_memories || 0, icon: FaBrain, color: '#f472b6' },
    { label: 'Relations', value: stats?.total_relations || 0, icon: FaProjectDiagram, color: '#a78bfa' },
    { label: 'Observations', value: stats?.total_observations || 0, icon: FaEye, color: '#fbbf24' },
    { label: 'Episodes', value: stats?.total_episodes || 0, icon: FaComments, color: '#38bdf8' },
    { label: 'Learned Today', value: stats?.learned_today || 0, icon: FaCalendarDay, color: '#34d399' },
  ];

  const formatTime = (dateStr) => {
    if (!dateStr) return '';
    const date = new Date(dateStr);
    const now = new Date();
    const diff = now - date;
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  };

  return (
    <div className="overview-tab">
      {/* Stat Tiles */}
      <div className="overview-tiles">
        {statTiles.map((tile) => {
          const Icon = tile.icon;
          return (
            <div key={tile.label} className="overview-tile">
              <div className="overview-tile-icon" style={{ color: tile.color }}>
                <Icon size={20} />
              </div>
              <div className="overview-tile-value">{tile.value}</div>
              <div className="overview-tile-label">{tile.label}</div>
            </div>
          );
        })}
      </div>

      {/* Memory Health */}
      <div className="overview-section">
        <h6 className="overview-section-title">Memory Health</h6>
        <div className="health-bars">
          {[
            { label: 'Entities', value: stats?.total_entities || 0, max: 100, color: '#60a5fa' },
            { label: 'Relations', value: stats?.total_relations || 0, max: 50, color: '#a78bfa' },
            { label: 'Memories', value: stats?.total_memories || 0, max: 50, color: '#f472b6' },
            { label: 'Observations', value: stats?.total_observations || 0, max: 200, color: '#fbbf24' },
            { label: 'Episodes', value: stats?.total_episodes || 0, max: 50, color: '#38bdf8' },
          ].map((bar) => (
            <div key={bar.label} className="health-bar-row">
              <span className="health-bar-label">{bar.label}</span>
              <div className="health-bar-track">
                <div
                  className="health-bar-fill"
                  style={{
                    width: `${Math.min(100, (bar.value / bar.max) * 100)}%`,
                    background: bar.color,
                  }}
                />
              </div>
              <span className="health-bar-value" style={{ color: bar.color }}>{bar.value}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Recent Activity */}
      <div className="overview-section">
        <h6 className="overview-section-title">Recent Activity</h6>
        {activity.length === 0 ? (
          <p className="text-muted small">No activity yet. Chat with Luna to start building memory.</p>
        ) : (
          <div className="activity-list">
            {activity.map((item) => {
              const cfg = getActivityEventConfig(item.event_type);
              const EventIcon = cfg.icon;
              return (
                <div key={item.id} className="activity-item">
                  <div className="activity-icon" style={{ color: cfg.color }}>
                    <EventIcon size={13} />
                  </div>
                  <div className="activity-content">
                    <span className="activity-description">{item.description}</span>
                    {item.source && (
                      <span className="activity-source">{item.source}</span>
                    )}
                  </div>
                  <span className="activity-time">{formatTime(item.created_at)}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

export default OverviewTab;
