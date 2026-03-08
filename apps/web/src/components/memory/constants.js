import {
  FaUser, FaBuilding, FaMapMarkerAlt, FaBox, FaCalendarAlt,
  FaBriefcase, FaTasks, FaLightbulb, FaBullseye, FaHandshake,
  FaChartLine, FaUserTie, FaStore, FaUsers,
  FaHeart, FaInfoCircle, FaStar, FaCheckCircle, FaWrench, FaListOl,
  FaPlus, FaEdit, FaTrash, FaProjectDiagram, FaBrain, FaBolt,
  FaTimesCircle, FaSearch, FaBell,
} from 'react-icons/fa';

// ── Category config (icons, colors, labels) ──────────────────────
export const CATEGORY_CONFIG = {
  lead:         { icon: FaBullseye,      color: '#34d399', bg: 'rgba(52, 211, 153, 0.15)', label: 'Lead' },
  contact:      { icon: FaUser,          color: '#60a5fa', bg: 'rgba(96, 165, 250, 0.15)', label: 'Contact' },
  customer:     { icon: FaUserTie,       color: '#2b7de9', bg: 'rgba(43, 125, 233, 0.15)', label: 'Customer' },
  investor:     { icon: FaChartLine,     color: '#8b5cf6', bg: 'rgba(139, 92, 246, 0.15)', label: 'Investor' },
  partner:      { icon: FaHandshake,     color: '#5ec5b0', bg: 'rgba(94, 197, 176, 0.15)', label: 'Partner' },
  competitor:   { icon: FaStore,         color: '#f59e0b', bg: 'rgba(245, 158, 11, 0.15)', label: 'Competitor' },
  vendor:       { icon: FaStore,         color: '#fb923c', bg: 'rgba(251, 146, 60, 0.15)', label: 'Vendor' },
  prospect:     { icon: FaBullseye,      color: '#a78bfa', bg: 'rgba(167, 139, 250, 0.15)', label: 'Prospect' },
  person:       { icon: FaUser,          color: '#60a5fa', bg: 'rgba(96, 165, 250, 0.15)', label: 'Person' },
  organization: { icon: FaBuilding,      color: '#f472b6', bg: 'rgba(244, 114, 182, 0.15)', label: 'Organization' },
  location:     { icon: FaMapMarkerAlt,  color: '#fb7185', bg: 'rgba(251, 113, 133, 0.15)', label: 'Location' },
  product:      { icon: FaBox,           color: '#fbbf24', bg: 'rgba(251, 191, 36, 0.15)', label: 'Product' },
  event:        { icon: FaCalendarAlt,   color: '#38bdf8', bg: 'rgba(56, 189, 248, 0.15)', label: 'Event' },
  opportunity:  { icon: FaBriefcase,     color: '#4ade80', bg: 'rgba(74, 222, 128, 0.15)', label: 'Opportunity' },
  task:         { icon: FaTasks,         color: '#c084fc', bg: 'rgba(192, 132, 252, 0.15)', label: 'Task' },
  concept:      { icon: FaLightbulb,     color: '#94a3b8', bg: 'rgba(148, 163, 184, 0.15)', label: 'Concept' },
};

export const DEFAULT_CATEGORY = { icon: FaUsers, color: '#94a3b8', bg: 'rgba(148, 163, 184, 0.10)', label: 'Other' };

export const getCategoryConfig = (category) => {
  return CATEGORY_CONFIG[category?.toLowerCase()] || DEFAULT_CATEGORY;
};

// ── Status config ────────────────────────────────────────────────
export const STATUS_CONFIG = {
  draft:    { color: '#fbbf24', bg: 'rgba(251, 191, 36, 0.15)', label: 'Draft' },
  verified: { color: '#34d399', bg: 'rgba(52, 211, 153, 0.15)', label: 'Verified' },
  enriched: { color: '#60a5fa', bg: 'rgba(96, 165, 250, 0.15)', label: 'Enriched' },
  actioned: { color: '#8b5cf6', bg: 'rgba(139, 92, 246, 0.15)', label: 'Actioned' },
  archived: { color: '#94a3b8', bg: 'rgba(148, 163, 184, 0.15)', label: 'Archived' },
};

export const getStatusConfig = (status) => {
  return STATUS_CONFIG[status?.toLowerCase()] || STATUS_CONFIG.draft;
};

// ── Entity types ─────────────────────────────────────────────────
export const ENTITY_TYPES = [
  'person', 'organization', 'product', 'location',
  'event', 'opportunity', 'task', 'concept',
];

// ── Relation types ───────────────────────────────────────────────
export const RELATION_TYPES = [
  'works_at', 'purchased', 'prefers', 'related_to',
  'knows', 'owns', 'manages', 'reports_to',
  'part_of', 'located_in', 'competes_with',
];

// ── All categories for filter dropdowns ──────────────────────────
export const ALL_CATEGORIES = Object.keys(CATEGORY_CONFIG);

// ── All statuses for filter dropdowns ────────────────────────────
export const ALL_STATUSES = Object.keys(STATUS_CONFIG);

// ── Memory type config ───────────────────────────────────────────
export const MEMORY_TYPE_CONFIG = {
  preference: { icon: FaHeart,       color: '#f472b6', bg: 'rgba(244, 114, 182, 0.15)', label: 'Preference' },
  fact:       { icon: FaInfoCircle,  color: '#60a5fa', bg: 'rgba(96, 165, 250, 0.15)',  label: 'Fact' },
  experience: { icon: FaStar,        color: '#fbbf24', bg: 'rgba(251, 191, 36, 0.15)',  label: 'Experience' },
  decision:   { icon: FaCheckCircle, color: '#34d399', bg: 'rgba(52, 211, 153, 0.15)',  label: 'Decision' },
  skill:      { icon: FaWrench,      color: '#a78bfa', bg: 'rgba(167, 139, 250, 0.15)', label: 'Skill' },
  procedure:  { icon: FaListOl,      color: '#38bdf8', bg: 'rgba(56, 189, 248, 0.15)',  label: 'Procedure' },
};

export const getMemoryTypeConfig = (type) => {
  return MEMORY_TYPE_CONFIG[type?.toLowerCase()] || { icon: FaInfoCircle, color: '#94a3b8', bg: 'rgba(148, 163, 184, 0.10)', label: type || 'Other' };
};

export const ALL_MEMORY_TYPES = Object.keys(MEMORY_TYPE_CONFIG);

// ── Activity event type config ───────────────────────────────────
export const ACTIVITY_EVENT_CONFIG = {
  entity_created:   { icon: FaPlus,           color: '#34d399', label: 'Entity Created' },
  entity_updated:   { icon: FaEdit,           color: '#60a5fa', label: 'Entity Updated' },
  entity_deleted:   { icon: FaTrash,          color: '#f87171', label: 'Entity Deleted' },
  relation_created: { icon: FaProjectDiagram, color: '#a78bfa', label: 'Relation Created' },
  memory_created:   { icon: FaBrain,          color: '#f472b6', label: 'Memory Learned' },
  memory_updated:   { icon: FaEdit,           color: '#f472b6', label: 'Memory Updated' },
  action_triggered: { icon: FaBolt,           color: '#fbbf24', label: 'Action Triggered' },
  action_completed: { icon: FaCheckCircle,    color: '#34d399', label: 'Action Completed' },
  action_failed:    { icon: FaTimesCircle,    color: '#f87171', label: 'Action Failed' },
  tool_used:            { icon: FaWrench,         color: '#fb923c', label: 'Tool Used' },
  recall_used:          { icon: FaSearch,         color: '#38bdf8', label: 'Context Recalled' },
  notification_created: { icon: FaBell,           color: '#ffa502', label: 'Notification Created' },
  monitor_scan:         { icon: FaSearch,         color: '#747d8c', label: 'Inbox Scan' },
  skill_executed:       { icon: FaWrench,         color: '#a78bfa', label: 'Skill Executed' },
  entity_scored:        { icon: FaStar,           color: '#fbbf24', label: 'Entity Scored' },
  rubric_created:       { icon: FaPlus,           color: '#34d399', label: 'Rubric Created' },
  rubric_updated:       { icon: FaEdit,           color: '#60a5fa', label: 'Rubric Updated' },
};

export const getActivityEventConfig = (type) => {
  return ACTIVITY_EVENT_CONFIG[type] || { icon: FaInfoCircle, color: '#94a3b8', label: type || 'Event' };
};

export const ALL_ACTIVITY_SOURCES = ['chat', 'gmail', 'whatsapp', 'calendar', 'inbox_monitor', 'manual'];
