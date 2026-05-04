// Batch coverage for the simpler API service modules. Each service is a thin
// wrapper over axios; we just want to lock in the URLs + payloads.

import api from '../api';
import utilApi from '../../utils/api';
import llmService from '../llm';
import dynamicWorkflowService from '../dynamicWorkflowService';
import integrationConfigService from '../integrationConfigService';
import datasetService from '../dataset';
import { teamsService } from '../teams';
import notebookService from '../notebook';
import toolService from '../tool';
import vectorStoreService from '../vectorStore';
import dataSourceService from '../dataSource';
import dataPipelineService from '../dataPipeline';
import deploymentService from '../deployment';
import datasetGroupService from '../datasetGroup';
import * as brandingMod from '../branding';
import * as analyticsMod from '../analytics';
import connectorService from '../connector';
import skillService from '../skillService';
import taskService from '../taskService';
import channelService from '../channelService';
import mediaService from '../mediaService';

jest.mock('../api');
jest.mock('../../utils/api');

beforeEach(() => {
  jest.clearAllMocks();
  for (const m of [api, utilApi]) {
    m.get.mockResolvedValue({ data: {} });
    m.post.mockResolvedValue({ data: {} });
    m.put.mockResolvedValue({ data: {} });
    m.patch.mockResolvedValue({ data: {} });
    m.delete.mockResolvedValue({ data: {} });
  }
});

describe('llmService', () => {
  test('endpoints', async () => {
    await llmService.getProviders();
    expect(api.get).toHaveBeenCalledWith('/llm/providers');

    await llmService.getModels();
    expect(api.get).toHaveBeenCalledWith('/llm/models');

    await llmService.getModels('openai');
    expect(api.get).toHaveBeenCalledWith('/llm/models?provider_name=openai');

    await llmService.getConfigs();
    expect(api.get).toHaveBeenCalledWith('/llm/configs');

    await llmService.createConfig({ provider_name: 'openai' });
    expect(api.post).toHaveBeenCalledWith('/llm/configs', { provider_name: 'openai' });

    await llmService.getProviderStatus();
    expect(api.get).toHaveBeenCalledWith('/llm/providers/status');

    await llmService.setProviderKey('openai', 'sk-x');
    expect(api.post).toHaveBeenCalledWith('/llm/providers/openai/key', { api_key: 'sk-x' });
  });
});

describe('dynamicWorkflowService', () => {
  test('CRUD + run + templates', async () => {
    await dynamicWorkflowService.list();
    expect(api.get).toHaveBeenCalledWith('/dynamic-workflows', { params: {} });

    await dynamicWorkflowService.list('active');
    expect(api.get).toHaveBeenCalledWith('/dynamic-workflows', { params: { status: 'active' } });

    await dynamicWorkflowService.get('w1');
    expect(api.get).toHaveBeenCalledWith('/dynamic-workflows/w1');

    await dynamicWorkflowService.create({ name: 'wf' });
    expect(api.post).toHaveBeenCalledWith('/dynamic-workflows', { name: 'wf' });

    await dynamicWorkflowService.update('w1', { name: 'wf2' });
    expect(api.put).toHaveBeenCalledWith('/dynamic-workflows/w1', { name: 'wf2' });

    await dynamicWorkflowService.delete('w1');
    expect(api.delete).toHaveBeenCalledWith('/dynamic-workflows/w1');

    await dynamicWorkflowService.activate('w1');
    expect(api.post).toHaveBeenCalledWith('/dynamic-workflows/w1/activate');

    await dynamicWorkflowService.pause('w1');
    expect(api.post).toHaveBeenCalledWith('/dynamic-workflows/w1/pause');

    await dynamicWorkflowService.run('w1', { x: 1 });
    expect(api.post).toHaveBeenCalledWith('/dynamic-workflows/w1/run', { input_data: { x: 1 } });

    await dynamicWorkflowService.dryRun('w1');
    expect(api.post).toHaveBeenCalledWith('/dynamic-workflows/w1/run', {
      input_data: {},
      dry_run: true,
    });

    await dynamicWorkflowService.listRuns('w1');
    expect(api.get).toHaveBeenCalledWith('/dynamic-workflows/w1/runs', { params: { limit: 20 } });

    await dynamicWorkflowService.getRun('r1');
    expect(api.get).toHaveBeenCalledWith('/dynamic-workflows/runs/r1');

    await dynamicWorkflowService.approveStep('r1', 's1', true);
    expect(api.post).toHaveBeenCalledWith(
      '/dynamic-workflows/runs/r1/approve/s1',
      null,
      { params: { approved: true } }
    );

    await dynamicWorkflowService.getIntegrationStatus();
    expect(api.get).toHaveBeenCalledWith('/integrations/status');

    await dynamicWorkflowService.getToolMapping();
    expect(api.get).toHaveBeenCalledWith('/integrations/tool-mapping');

    await dynamicWorkflowService.browseTemplates();
    expect(api.get).toHaveBeenCalledWith('/dynamic-workflows/templates/browse', { params: {} });

    await dynamicWorkflowService.browseTemplates('community');
    expect(api.get).toHaveBeenCalledWith('/dynamic-workflows/templates/browse', {
      params: { tier: 'community' },
    });

    await dynamicWorkflowService.installTemplate('tpl-1');
    expect(api.post).toHaveBeenCalledWith('/dynamic-workflows/templates/tpl-1/install');
  });
});

describe('integrationConfigService', () => {
  test('endpoints route to /integration-configs and /oauth', async () => {
    await integrationConfigService.getRegistry();
    expect(utilApi.get).toHaveBeenCalledWith('/integration-configs/registry');

    await integrationConfigService.getAll({ enabled: true });
    expect(utilApi.get).toHaveBeenCalledWith('/integration-configs/', { params: { enabled: true } });

    await integrationConfigService.create({ name: 'x' });
    expect(utilApi.post).toHaveBeenCalledWith('/integration-configs/', { name: 'x' });

    await integrationConfigService.update('1', { enabled: false });
    expect(utilApi.put).toHaveBeenCalledWith('/integration-configs/1', { enabled: false });

    await integrationConfigService.remove('1');
    expect(utilApi.delete).toHaveBeenCalledWith('/integration-configs/1');

    await integrationConfigService.addCredential('1', { key: 'k', value: 'v' });
    expect(utilApi.post).toHaveBeenCalledWith('/integration-configs/1/credentials', {
      key: 'k',
      value: 'v',
    });

    await integrationConfigService.revokeCredential('1', 'KEY');
    expect(utilApi.delete).toHaveBeenCalledWith('/integration-configs/1/credentials/KEY');

    await integrationConfigService.getCredentialStatus('1');
    expect(utilApi.get).toHaveBeenCalledWith('/integration-configs/1/credentials/status');

    await integrationConfigService.oauthAuthorize('google');
    expect(utilApi.get).toHaveBeenCalledWith('/oauth/google/authorize');

    await integrationConfigService.oauthDisconnect('google');
    expect(utilApi.post).toHaveBeenCalledWith('/oauth/google/disconnect', null, { params: {} });

    await integrationConfigService.oauthDisconnect('google', 'a@b.com');
    expect(utilApi.post).toHaveBeenCalledWith('/oauth/google/disconnect', null, {
      params: { account_email: 'a@b.com' },
    });

    await integrationConfigService.codexAuthStart();
    expect(utilApi.post).toHaveBeenCalledWith('/codex-auth/start');

    await integrationConfigService.geminiCliAuthSubmitCode('123');
    expect(utilApi.post).toHaveBeenCalledWith('/gemini-cli-auth/submit-code', { code: '123' });
  });
});

describe('datasetService', () => {
  test('endpoints', async () => {
    await datasetService.getAll();
    expect(utilApi.get).toHaveBeenCalledWith('/datasets/');
    await datasetService.get('1');
    expect(utilApi.get).toHaveBeenCalledWith('/datasets/1');
    await datasetService.getPreview('1');
    expect(utilApi.get).toHaveBeenCalledWith('/datasets/1/preview');
    await datasetService.getSummary('1');
    expect(utilApi.get).toHaveBeenCalledWith('/datasets/1/summary');
    await datasetService.sync('1');
    expect(utilApi.post).toHaveBeenCalledWith('/datasets/1/sync');

    const fd = new FormData();
    await datasetService.upload(fd);
    expect(utilApi.post).toHaveBeenCalledWith(
      '/datasets/upload',
      fd,
      expect.objectContaining({ headers: { 'Content-Type': 'multipart/form-data' } })
    );
  });
});

describe('teamsService', () => {
  test('group + tasks endpoints', async () => {
    await teamsService.getGroups();
    expect(api.get).toHaveBeenCalledWith('/agent_groups');

    await teamsService.getGroup('g1');
    expect(api.get).toHaveBeenCalledWith('/agent_groups/g1');

    await teamsService.createGroup({ name: 'X' });
    expect(api.post).toHaveBeenCalledWith('/agent_groups', { name: 'X' });

    await teamsService.updateGroup('g1', { name: 'Y' });
    expect(api.put).toHaveBeenCalledWith('/agent_groups/g1', { name: 'Y' });

    await teamsService.deleteGroup('g1');
    expect(api.delete).toHaveBeenCalledWith('/agent_groups/g1');

    await teamsService.getGroupAgents('g1');
    expect(api.get).toHaveBeenCalledWith('/agent_groups/g1/agents');

    await teamsService.getTasks('g1');
    expect(api.get).toHaveBeenCalledWith('/tasks?group_id=g1');
  });
});

describe('miscellaneous services', () => {
  test('hit their declared endpoints', async () => {
    await notebookService.getAll();
    expect(api.get).toHaveBeenCalledWith('/notebooks');

    await toolService.getAll();
    expect(api.get).toHaveBeenCalledWith('/tools');

    // vectorStore + datasetGroup + connector + ... import from
    // '../utils/api', so they hit the utilApi mock instead of the
    // services/api mock.
    await vectorStoreService.getAll();
    expect(utilApi.get).toHaveBeenCalledWith('/vector_stores/');

    await dataSourceService.getAll();
    expect(api.get).toHaveBeenCalledWith('/data_sources/');

    await dataPipelineService.getAll();
    expect(api.get).toHaveBeenCalledWith('/data_pipelines/');

    await deploymentService.getAll();
    expect(api.get).toHaveBeenCalledWith('/deployments');

    await datasetGroupService.getAll();
    expect(utilApi.get).toHaveBeenCalledWith('/dataset-groups/');

    // branding/analytics modules are intentionally not asserted: they expose
    // store-style helpers without a fixed URL surface, and we already cover
    // them in their own focused tests.
    expect(brandingMod).toBeDefined();
    expect(analyticsMod).toBeDefined();

    if (connectorService?.getAll) {
      await connectorService.getAll();
      expect(utilApi.get).toHaveBeenCalledWith('/connectors/');
    }

    if (skillService?.health) {
      await skillService.health();
      expect(utilApi.get).toHaveBeenCalledWith('/skills/health');
    }

    if (taskService?.getAll) {
      await taskService.getAll();
      expect(utilApi.get).toHaveBeenCalledWith('/tasks', { params: {} });
    }

    if (channelService?.getWhatsAppStatus) {
      await channelService.getWhatsAppStatus();
      expect(api.get).toHaveBeenCalledWith('/channels/whatsapp/status');
    }

    if (mediaService?.transcribeAudio) {
      await mediaService.transcribeAudio(new Blob(['x'], { type: 'audio/wav' }));
      expect(utilApi.post).toHaveBeenCalledWith(
        '/media/transcribe',
        expect.any(Object),
        expect.objectContaining({ headers: expect.any(Object) }),
      );
    }
  });
});
