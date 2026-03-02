import api from './api';

export const memoryService = {
  // ── Agent Memories (legacy) ──────────────────────────────────────
  async getMemories(agentId) {
    const response = await api.get(`/memories/agent/${agentId}`);
    return response.data;
  },

  async storeMemory(data) {
    const response = await api.post('/memories', data);
    return response.data;
  },

  async deleteMemory(memoryId) {
    await api.delete(`/memories/${memoryId}`);
  },

  // ── Knowledge Entities ───────────────────────────────────────────
  async getEntities({ entityType, category, status, skip = 0, limit = 50 } = {}) {
    const params = new URLSearchParams();
    if (entityType) params.append('entity_type', entityType);
    if (category) params.append('category', category);
    if (status) params.append('status', status);
    params.append('skip', skip);
    params.append('limit', limit);
    const response = await api.get(`/knowledge/entities?${params.toString()}`);
    return response.data;
  },

  async getEntity(id) {
    const response = await api.get(`/knowledge/entities/${id}`);
    return response.data;
  },

  async searchEntities(query, { entityType, category } = {}) {
    const params = new URLSearchParams({ q: query });
    if (entityType) params.append('entity_type', entityType);
    if (category) params.append('category', category);
    const response = await api.get(`/knowledge/entities/search?${params.toString()}`);
    return response.data;
  },

  async createEntity(data) {
    const response = await api.post('/knowledge/entities', data);
    return response.data;
  },

  async bulkCreateEntities(entities) {
    const response = await api.post('/knowledge/entities/bulk', { entities });
    return response.data;
  },

  async updateEntity(id, data) {
    const response = await api.put(`/knowledge/entities/${id}`, data);
    return response.data;
  },

  async deleteEntity(id) {
    await api.delete(`/knowledge/entities/${id}`);
  },

  async bulkDeleteEntities(ids) {
    const results = await Promise.allSettled(
      ids.map(id => api.delete(`/knowledge/entities/${id}`))
    );
    const failed = results.filter(r => r.status === 'rejected');
    if (failed.length > 0) {
      throw new Error(`Failed to delete ${failed.length} of ${ids.length} entities`);
    }
  },

  async updateEntityStatus(id, status) {
    const response = await api.put(`/knowledge/entities/${id}/status`, { status });
    return response.data;
  },

  async scoreEntity(id, rubricId = null) {
    const params = rubricId ? `?rubric_id=${rubricId}` : '';
    const response = await api.post(`/knowledge/entities/${id}/score${params}`);
    return response.data;
  },

  // ── Relations ────────────────────────────────────────────────────
  async getRelations(entityId, direction = 'both') {
    const response = await api.get(`/knowledge/entities/${entityId}/relations?direction=${direction}`);
    return response.data;
  },

  async createRelation(data) {
    const response = await api.post('/knowledge/relations', data);
    return response.data;
  },

  async deleteRelation(relationId) {
    await api.delete(`/knowledge/relations/${relationId}`);
  },

  // ── Scoring Rubrics ──────────────────────────────────────────────
  async getScoringRubrics() {
    const response = await api.get('/knowledge/scoring-rubrics');
    return response.data;
  },
};
