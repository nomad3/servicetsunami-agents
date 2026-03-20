import api from '../utils/api';

const integrationConfigService = {
  getRegistry: () => api.get('/integration-configs/registry'),
  getAll: (params = {}) => api.get('/integration-configs/', { params }),
  create: (data) => api.post('/integration-configs/', data),
  update: (id, data) => api.put(`/integration-configs/${id}`, data),
  remove: (id) => api.delete(`/integration-configs/${id}`),
  addCredential: (id, data) => api.post(`/integration-configs/${id}/credentials`, data),
  revokeCredential: (id, key) => api.delete(`/integration-configs/${id}/credentials/${key}`),
  getCredentialStatus: (id) => api.get(`/integration-configs/${id}/credentials/status`),
  // OAuth
  oauthAuthorize: (provider) => api.get(`/oauth/${provider}/authorize`),
  oauthDisconnect: (provider, accountEmail) =>
    api.post(`/oauth/${provider}/disconnect`, null, {
      params: accountEmail ? { account_email: accountEmail } : {},
    }),
  oauthStatus: (provider) => api.get(`/oauth/${provider}/status`),
  codexAuthStart: () => api.post('/codex-auth/start'),
  codexAuthStatus: () => api.get('/codex-auth/status'),
  codexAuthCancel: () => api.post('/codex-auth/cancel'),
};

export default integrationConfigService;
