import api from '../utils/api';

const skillConfigService = {
  getRegistry: () => api.get('/skill-configs/registry'),
  getAll: (params = {}) => api.get('/skill-configs/', { params }),
  create: (data) => api.post('/skill-configs/', data),
  update: (id, data) => api.put(`/skill-configs/${id}`, data),
  remove: (id) => api.delete(`/skill-configs/${id}`),
  addCredential: (id, data) => api.post(`/skill-configs/${id}/credentials`, data),
  revokeCredential: (id, key) => api.delete(`/skill-configs/${id}/credentials/${key}`),
  // OAuth
  oauthAuthorize: (provider) => api.get(`/oauth/${provider}/authorize`),
  oauthDisconnect: (provider) => api.post(`/oauth/${provider}/disconnect`),
  oauthStatus: (provider) => api.get(`/oauth/${provider}/status`),
};

export default skillConfigService;
