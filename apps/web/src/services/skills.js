import api from './api';

export const skillsService = {
  getSkills: async (skillType) => {
    const params = {};
    if (skillType) params.skill_type = skillType;
    const response = await api.get('/skills', { params });
    return response.data;
  },

  getSkill: async (id) => {
    const response = await api.get(`/skills/${id}`);
    return response.data;
  },

  createSkill: async (data) => {
    const response = await api.post('/skills', data);
    return response.data;
  },

  updateSkill: async (id, data) => {
    const response = await api.put(`/skills/${id}`, data);
    return response.data;
  },

  deleteSkill: async (id) => {
    const response = await api.delete(`/skills/${id}`);
    return response.data;
  },

  executeSkill: async (id, entityId, params) => {
    const response = await api.post(`/skills/${id}/execute`, {
      entity_id: entityId,
      params: params || {},
    });
    return response.data;
  },

  getSkillExecutions: async (id) => {
    const response = await api.get(`/skills/${id}/executions`);
    return response.data;
  },

  cloneSkill: async (id) => {
    const response = await api.post(`/skills/${id}/clone`);
    return response.data;
  },
};

// --- File-based Skills Marketplace v2 ---

export const getFileSkills = (params = {}) => {
  const query = new URLSearchParams();
  if (params.tier) query.append('tier', params.tier);
  if (params.category) query.append('category', params.category);
  if (params.search) query.append('search', params.search);
  const qs = query.toString();
  return api.get(`/skills/library${qs ? '?' + qs : ''}`);
};

export const createFileSkill = (data) =>
  api.post('/skills/library/create', data);

export const updateFileSkill = (slug, data) =>
  api.put(`/skills/library/${slug}`, data);

export const forkFileSkill = (slug) =>
  api.post(`/skills/library/${slug}/fork`);

export const deleteFileSkill = (slug) =>
  api.delete(`/skills/library/${slug}`);

export const executeFileSkill = (skillName, inputs = {}) =>
  api.post('/skills/library/execute', { skill_name: skillName, inputs });

export const getSkillVersions = (slug) =>
  api.get(`/skills/library/${slug}/versions`);

export const importFromGithub = (repoUrl) =>
  api.post('/skills/library/import-github', { repo_url: repoUrl });

// Returns MCP manifest (server_url, tenant_id, list of tool definitions) so
// external agents — Claude Code, Gemini CLI, VS Code Copilot — can connect.
export const getMcpManifest = () => api.get('/skills/mcp-manifest');

// Export a single skill as SKILL.md (superpowers/gws) or OpenAI function JSON.
// The API returns the raw content; the UI wraps it in a Blob for download.
export const exportSkill = (slug, format) =>
  api.get(`/skills/library/${slug}/export`, {
    params: { format },
    // force text/json passthrough — browser may otherwise parse as JSON and choke on md
    transformResponse: [(data) => data],
  });
