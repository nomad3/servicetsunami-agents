import api from './api';

const agentService = {
  getAll: () => api.get('/agents/'),

  getById: (id) => api.get(`/agents/${id}`),

  create: (data) => api.post('/agents/', data),

  update: (id, data) => api.put(`/agents/${id}`, data),

  delete: (id) => api.delete(`/agents/${id}`),

  deploy: (id, deploymentData) => api.post(`/agents/${id}/deploy`, deploymentData),

  getTasks: (params = {}) => api.get('/tasks', { params }),

  getGroups: () => api.get('/agent_groups/'),

  // Hire wizard surface (PR-D of the external-agents + A2A plan).
  // Lists native + external + marketplace listings that match a capability.
  discover: (capability, kind) =>
    api.get('/agents/discover', { params: { capability, ...(kind ? { kind } : {}) } }),

  // External-agent hire path — same payload AgentsPage's existing modal
  // posts. Wizard reuses this so we don't fork two creation surfaces.
  createExternal: (data) => api.post('/external-agents/', data),
  testTask: (externalAgentId, task) =>
    api.post(`/external-agents/${externalAgentId}/test-task`, { task }),

  // Marketplace subscribe — for cross-tenant listings.
  subscribeListing: (listingId) =>
    api.post(`/agent-marketplace/subscribe`, { listing_id: listingId }),
  listMarketplace: (capability) =>
    api.get('/agent-marketplace/listings', { params: capability ? { capability } : {} }),

  // CrewAI / LangChain / AutoGen import — backed by AgentImporter.
  importAgent: (content, filename) =>
    api.post('/agents/import', { content, filename }),
};

export default agentService;
