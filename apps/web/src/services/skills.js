import api from './api';

export const skillsService = {
  getSkills: async (skillType) => {
    const params = {};
    if (skillType) params.skill_type = skillType;
    const response = await api.get('/api/v1/skills', { params });
    return response.data;
  },

  getSkill: async (id) => {
    const response = await api.get(`/api/v1/skills/${id}`);
    return response.data;
  },

  createSkill: async (data) => {
    const response = await api.post('/api/v1/skills', data);
    return response.data;
  },

  updateSkill: async (id, data) => {
    const response = await api.put(`/api/v1/skills/${id}`, data);
    return response.data;
  },

  deleteSkill: async (id) => {
    const response = await api.delete(`/api/v1/skills/${id}`);
    return response.data;
  },

  executeSkill: async (id, entityId, params) => {
    const response = await api.post(`/api/v1/skills/${id}/execute`, {
      entity_id: entityId,
      params: params || {},
    });
    return response.data;
  },

  getSkillExecutions: async (id) => {
    const response = await api.get(`/api/v1/skills/${id}/executions`);
    return response.data;
  },

  cloneSkill: async (id) => {
    const response = await api.post(`/api/v1/skills/${id}/clone`);
    return response.data;
  },
};
