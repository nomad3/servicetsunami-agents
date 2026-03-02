import {
  FaUser, FaBuilding, FaMapMarkerAlt, FaBox, FaCalendarAlt,
  FaBriefcase, FaTasks, FaLightbulb, FaBullseye, FaHandshake,
  FaChartLine, FaUserTie, FaStore, FaUsers
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
