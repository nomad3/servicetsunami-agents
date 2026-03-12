import api from './api';

const learningService = {
  getOverview: async () => { const r = await api.get('/rl/overview'); return r.data; },
  getExperiences: async (params = {}) => { const r = await api.get('/rl/experiences', { params }); return r.data; },
  getTrajectory: async (trajectoryId) => { const r = await api.get(`/rl/experiences/${trajectoryId}`); return r.data; },
  submitFeedback: async (data) => { const r = await api.post('/rl/feedback', data); return r.data; },
  getDecisionPoints: async () => { const r = await api.get('/rl/decision-points'); return r.data; },
  getDecisionPoint: async (name) => { const r = await api.get(`/rl/decision-points/${name}`); return r.data; },
  getExperiments: async () => { const r = await api.get('/rl/experiments'); return r.data; },
  triggerExperiment: async (decisionPoint) => { const r = await api.post(`/rl/experiments/trigger?decision_point=${decisionPoint}`); return r.data; },
  getPendingReviews: async (params = {}) => { const r = await api.get('/rl/reviews/pending', { params }); return r.data; },
  rateExperience: async (experienceId, rating) => { const r = await api.post(`/rl/reviews/${experienceId}/rate?rating=${rating}`); return r.data; },
  batchRate: async (ratings) => { const r = await api.post('/rl/reviews/batch-rate', ratings); return r.data; },
  getSettings: async () => { const r = await api.get('/rl/settings'); return r.data; },
  updateSettings: async (data) => { const r = await api.put('/rl/settings', data); return r.data; },
  getPolicyVersions: async () => { const r = await api.get('/rl/policy/versions'); return r.data; },
  rollbackPolicy: async (decisionPoint, version) => { const r = await api.post(`/rl/policy/rollback?decision_point=${decisionPoint}&version=${version}`); return r.data; },
  exportExperiences: async (decisionPoint) => { const r = await api.get('/rl/export', { params: { decision_point: decisionPoint }, responseType: 'blob' }); return r.data; },
};

export default learningService;
