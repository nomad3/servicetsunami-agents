import api from './api';

const dynamicWorkflowService = {
  // CRUD
  list: async (status) => {
    const params = status ? { status } : {};
    const r = await api.get('/dynamic-workflows', { params });
    return r.data;
  },
  get: async (id) => { const r = await api.get(`/dynamic-workflows/${id}`); return r.data; },
  create: async (data) => { const r = await api.post('/dynamic-workflows', data); return r.data; },
  update: async (id, data) => { const r = await api.put(`/dynamic-workflows/${id}`, data); return r.data; },
  delete: async (id) => { await api.delete(`/dynamic-workflows/${id}`); },

  // Status
  activate: async (id) => { const r = await api.post(`/dynamic-workflows/${id}/activate`); return r.data; },
  pause: async (id) => { const r = await api.post(`/dynamic-workflows/${id}/pause`); return r.data; },

  // Execution
  run: async (id, inputData = {}) => { const r = await api.post(`/dynamic-workflows/${id}/run`, { input_data: inputData }); return r.data; },
  listRuns: async (id, limit = 20) => { const r = await api.get(`/dynamic-workflows/${id}/runs`, { params: { limit } }); return r.data; },
  getRun: async (runId) => { const r = await api.get(`/dynamic-workflows/runs/${runId}`); return r.data; },
  approveStep: async (runId, stepId, approved = true) => {
    const r = await api.post(`/dynamic-workflows/runs/${runId}/approve/${stepId}`, null, { params: { approved } });
    return r.data;
  },

  // Validation
  dryRun: async (id, inputData = {}) => {
    const r = await api.post(`/dynamic-workflows/${id}/run`, { input_data: inputData, dry_run: true });
    return r.data;
  },

  // Integration awareness
  getIntegrationStatus: async () => { const r = await api.get('/integrations/status'); return r.data; },
  getToolMapping: async () => { const r = await api.get('/integrations/tool-mapping'); return r.data; },

  // Templates
  browseTemplates: async (tier) => {
    const params = tier ? { tier } : {};
    const r = await api.get('/dynamic-workflows/templates/browse', { params });
    return r.data;
  },
  installTemplate: async (templateId) => {
    const r = await api.post(`/dynamic-workflows/templates/${templateId}/install`);
    return r.data;
  },
};

export default dynamicWorkflowService;
