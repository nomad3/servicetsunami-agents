import api from './api';

// Client for the Luna Value Layer (#647).
//
// Endpoints (see apps/api/app/api/v1/values.py):
//   GET    /api/v1/luna/values                          → default agent's value set
//   GET    /api/v1/luna/values/agents/{agentId}         → specific agent's value set
//   PUT    /api/v1/luna/values                          → full-replace default
//   PUT    /api/v1/luna/values/agents/{agentId}         → full-replace specific
//   POST   /api/v1/luna/values/break-glass              → time-boxed override (default)
//   POST   /api/v1/luna/values/agents/{agentId}/break-glass → per-agent break-glass
//
// All endpoints return the persisted ValueSetOut with version + updated_at.
// Break-glass adds expires_at / break_glass_reason / break_glass_operator_id
// fields (Optional<str>; null on ordinary versions).

const valuesService = {
  getDefault: () => api.get('/luna/values'),
  getForAgent: (agentId) => api.get(`/luna/values/agents/${agentId}`),

  putDefault: (body) => api.put('/luna/values', body),
  putForAgent: (agentId, body) => api.put(`/luna/values/agents/${agentId}`, body),

  // body shape:
  //   {
  //     reason: string (required, 1..500 chars),
  //     duration_seconds: int (60..86400, default 3600),
  //     keep_protect_slugs?: string[],   // empty/omitted = full clear
  //     keep_avoid_slugs?: string[],
  //   }
  breakGlassDefault: (body) => api.post('/luna/values/break-glass', body),
  breakGlassForAgent: (agentId, body) =>
    api.post(`/luna/values/agents/${agentId}/break-glass`, body),

  // Tenant-level kill-switch (tenant_features.value_layer_enabled).
  // GET reads the full features payload; the caller picks out
  // value_layer_enabled. PUT only sends the one field; the backend's
  // _MEMBER_WRITABLE_FIELDS allowlist silently drops it for
  // non-superusers (and logs at WARNING). The UI re-reads after PUT
  // to show whether the change actually persisted.
  getFeatures: () => api.get('/features'),
  setValueLayerEnabled: (enabled) =>
    api.put('/features', { value_layer_enabled: !!enabled }),
};

export default valuesService;
