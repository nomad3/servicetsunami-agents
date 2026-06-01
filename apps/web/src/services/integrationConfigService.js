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
  // GitHub SSH key (for OAuth-blocked orgs). Returns/stores only a fingerprint;
  // the private key is never echoed back.
  githubSshKeyStatus: () => api.get('/oauth/github/ssh-key'),
  githubSshKeySave: (privateKey) => api.post('/oauth/github/ssh-key', { private_key: privateKey }),
  githubSshKeyDelete: () => api.delete('/oauth/github/ssh-key'),
  codexAuthStart: () => api.post('/codex-auth/start'),
  codexAuthStatus: () => api.get('/codex-auth/status'),
  codexAuthCancel: () => api.post('/codex-auth/cancel'),
  claudeAuthStart: () => api.post('/claude-auth/start'),
  claudeAuthStatus: () => api.get('/claude-auth/status'),
  claudeAuthCancel: () => api.post('/claude-auth/cancel'),
  // Mirrors gemini-cli-auth submit-code: forwards the verification
  // code the user pasted from claude.com to the running subprocess's
  // stdin. Required for the modern claude CLI which doesn't have a
  // localhost OAuth callback inside the container.
  claudeAuthSubmitCode: (code) => api.post('/claude-auth/submit-code', { code }),
  // Bypass for users who don't want the subscription-OAuth flow —
  // paste an Anthropic Console API key (sk-ant-...) and we store it
  // in the same credential slot.
  claudeAuthSetApiKey: (apiKey) => api.post('/claude-auth/api-key', { api_key: apiKey }),
  geminiCliAuthStart: () => api.post('/gemini-cli-auth/start'),
  geminiCliAuthStatus: () => api.get('/gemini-cli-auth/status'),
  geminiCliAuthSubmitCode: (code) => api.post('/gemini-cli-auth/submit-code', { code }),
  geminiCliAuthCancel: () => api.post('/gemini-cli-auth/cancel'),
  geminiCliAuthDisconnect: () => api.post('/gemini-cli-auth/disconnect'),
  // Higgsfield creative-content MCP source — Wave 1a of the CLI catalog
  // (#270). Mirrors the gemini-cli paste-back PKCE flow. The resulting
  // OAuth blob registers a per-tenant Higgsfield MCP server that the
  // Marketing/Sales specialist agent can call via discover_mcp_tools /
  // call_mcp_tool.
  higgsfieldAuthStart: () => api.post('/higgsfield-auth/start'),
  higgsfieldAuthStatus: () => api.get('/higgsfield-auth/status'),
  higgsfieldAuthSubmitCode: (code) => api.post('/higgsfield-auth/submit-code', { code }),
  higgsfieldAuthCancel: () => api.post('/higgsfield-auth/cancel'),
  higgsfieldAuthDisconnect: () => api.post('/higgsfield-auth/disconnect'),
  // Returns { connected: ["claude_code", ...] } for the current tenant
  // — the resolver-aligned list of CLIs the InlineCliPicker filters
  // its dropdown against. See apps/api/app/api/v1/integrations.py
  // GET /connected-clis for the contract.
  listConnectedClis: () => api.get('/integrations/connected-clis'),
};

export default integrationConfigService;
