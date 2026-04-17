import React, { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button, Badge, Spinner } from 'react-bootstrap';
import { useNodesState, useEdgesState } from 'reactflow';
import { FiSave, FiPlay, FiPower, FiCode, FiArrowLeft, FiDownload } from 'react-icons/fi';
import { useTranslation } from 'react-i18next';
import './WorkflowBuilder.css';

import WorkflowCanvas from './WorkflowCanvas';
import StepPalette from './StepPalette';
import StepInspector from './StepInspector';
import TestConsole from './TestConsole';
import { definitionToFlow, flowToDefinition } from './WorkflowAdapter';
import dynamicWorkflowService from '../../services/dynamicWorkflowService';

const STEP_TYPE_MAP = {
  mcp_tool: 'stepNode', agent: 'stepNode', transform: 'stepNode',
  wait: 'stepNode', condition: 'conditionNode', for_each: 'forEachNode',
  parallel: 'parallelNode', human_approval: 'approvalNode',
};

export default function WorkflowBuilder() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { t } = useTranslation('workflows');

  const [workflow, setWorkflow] = useState(null);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedNode, setSelectedNode] = useState(null);
  const [showJson, setShowJson] = useState(false);
  const [showTestConsole, setShowTestConsole] = useState(false);
  const [testResults, setTestResults] = useState(null);
  const [saving, setSaving] = useState(false);
  const [integrationStatus, setIntegrationStatus] = useState({});
  const [toolMapping, setToolMapping] = useState({});
  const [workflowName, setWorkflowName] = useState(t('builder.untitled'));

  useEffect(() => {
    async function load() {
      try {
        const [intStatus, mapping] = await Promise.all([
          dynamicWorkflowService.getIntegrationStatus().catch(() => ({})),
          dynamicWorkflowService.getToolMapping().catch(() => ({})),
        ]);
        setIntegrationStatus(intStatus);
        setToolMapping(mapping);

        if (id) {
          const wf = await dynamicWorkflowService.get(id);
          setWorkflow(wf);
          setWorkflowName(wf.name || t('builder.untitled'));
          const { nodes: n, edges: e } = definitionToFlow(wf.definition, wf.trigger_config);
          setNodes(n);
          setEdges(e);
        } else {
          setNodes([{
            id: 'trigger-root', type: 'triggerNode',
            data: { trigger: { type: 'manual' } },
            position: { x: 300, y: 50 },
          }]);
          setEdges([]);
        }
      } catch (err) {
        console.error('Failed to load workflow:', err);
      }
    }
    load();
  }, [id, setNodes, setEdges]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const { definition, triggerConfig } = flowToDefinition(nodes, edges);
      const payload = {
        name: workflowName,
        description: workflow?.description || '',
        definition,
        trigger_config: triggerConfig,
      };
      if (id) {
        await dynamicWorkflowService.update(id, payload);
      } else {
        const created = await dynamicWorkflowService.create(payload);
        navigate(`/workflows/builder/${created.id}`, { replace: true });
        setWorkflow(created);
      }
    } catch (err) {
      console.error('Save failed:', err);
    }
    setSaving(false);
  };

  const handleTest = async () => {
    setShowTestConsole(true);
    setTestResults(null);
    try {
      const results = await dynamicWorkflowService.dryRun(id || workflow?.id, {});
      setTestResults(results);
    } catch (err) {
      setTestResults({ validation_errors: [err.message || 'Test failed'], steps_planned: [], step_count: 0 });
    }
  };

  const handleActivate = async () => {
    try {
      await dynamicWorkflowService.activate(id);
      setWorkflow((prev) => ({ ...prev, status: 'active' }));
    } catch (err) {
      console.error('Activation failed:', err);
    }
  };

  const handleInstall = async () => {
    try {
      const installed = await dynamicWorkflowService.installTemplate(id);
      navigate(`/workflows/builder/${installed.id}`, { replace: true });
    } catch (err) {
      console.error('Install failed:', err);
    }
  };

  const isTemplate = workflow?.tier === 'native' || workflow?.tier === 'community';

  const onDrop = useCallback((event) => {
    event.preventDefault();
    const raw = event.dataTransfer.getData('application/workflow-step');
    if (!raw) return;
    const data = JSON.parse(raw);

    const reactFlowBounds = event.currentTarget.getBoundingClientRect();
    const position = {
      x: event.clientX - reactFlowBounds.left - 110,
      y: event.clientY - reactFlowBounds.top - 40,
    };

    const newId = `${data.type}-${Date.now()}`;
    const nodeType = data.type === 'trigger' ? 'triggerNode' : (STEP_TYPE_MAP[data.type] || 'stepNode');

    const newNode = {
      id: newId,
      type: nodeType,
      data: {
        step: {
          id: newId,
          type: data.type,
          tool: data.subtype || '',
          params: {},
          output: '',
        },
        ...(data.type === 'trigger' ? { trigger: { type: data.subtype || 'manual' } } : {}),
      },
      position,
    };
    setNodes((nds) => [...nds, newNode]);
  }, [setNodes]);

  const onDragOver = useCallback((event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onNodeClick = useCallback((_, node) => {
    setSelectedNode(node);
  }, []);

  const handleNodeUpdate = useCallback((nodeId, updatedData) => {
    setNodes((nds) => nds.map((n) => {
      if (n.id !== nodeId) return n;
      return {
        ...n,
        data: {
          ...n.data,
          ...(updatedData.step ? { step: updatedData.step } : {}),
          ...(updatedData.trigger ? { trigger: updatedData.trigger } : {}),
        },
      };
    }));
  }, [setNodes]);

  const getNodeIntegration = () => {
    if (!selectedNode?.data?.step?.tool) return null;
    const intName = toolMapping[selectedNode.data.step.tool];
    if (!intName) return null;
    return integrationStatus[intName] || null;
  };

  const integrationPill = () => {
    const required = new Set();
    nodes.forEach((n) => {
      const tool = n.data?.step?.tool;
      if (tool && toolMapping[tool]) required.add(toolMapping[tool]);
    });
    const connected = [...required].filter((r) => integrationStatus[r]?.connected).length;
    return { connected, total: required.size };
  };

  const pill = integrationPill();

  return (
    <div className="workflow-builder">
      {/* Toolbar */}
      <div className="builder-toolbar">
        <Button variant="link" size="sm" className="btn-back"
          onClick={() => navigate('/workflows')}>
          <FiArrowLeft /> {t('builder.back')}
        </Button>
        <input
          className="workflow-name-input"
          value={workflowName}
          onChange={(e) => !isTemplate && setWorkflowName(e.target.value)}
          readOnly={isTemplate}
          style={isTemplate ? { opacity: 0.7, cursor: 'default' } : {}}
        />
        {isTemplate
          ? <Badge bg="info">preview</Badge>
          : (
            <Badge bg={workflow?.status === 'active' ? 'success' : 'secondary'}>
              {workflow?.status || 'draft'}
            </Badge>
          )
        }
        {!isTemplate && pill.total > 0 && (
          <Badge bg={pill.connected === pill.total ? 'success' : 'warning'}>
            {t('builder.integrations')}: {pill.connected}/{pill.total}
          </Badge>
        )}

        <div className="builder-toolbar-actions">
          <Button variant="outline-secondary" size="sm" onClick={() => setShowJson(!showJson)}>
            <FiCode /> {t('builder.json')}
          </Button>
          {isTemplate ? (
            <Button variant="primary" size="sm" onClick={handleInstall}>
              <FiDownload /> {t('templates.install')}
            </Button>
          ) : (
            <>
              <Button variant="outline-info" size="sm" onClick={handleTest} disabled={!id}>
                <FiPlay /> {t('builder.test')}
              </Button>
              <Button variant="primary" size="sm" onClick={handleSave} disabled={saving}>
                {saving ? <Spinner size="sm" /> : <><FiSave /> {t('builder.save')}</>}
              </Button>
              <Button variant="success" size="sm" onClick={handleActivate}
                disabled={!id || workflow?.status === 'active' || pill.connected < pill.total}>
                <FiPower /> {t('builder.activate')}
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Main layout */}
      <div className="builder-layout">
        {!isTemplate && <StepPalette mcpTools={Object.keys(toolMapping).filter(k => k)} />}
        <WorkflowCanvas
          nodes={nodes} edges={edges}
          onNodesChange={isTemplate ? undefined : onNodesChange}
          onEdgesChange={isTemplate ? undefined : onEdgesChange}
          setEdges={setEdges}
          onNodeClick={onNodeClick}
          onDrop={isTemplate ? undefined : onDrop}
          onDragOver={isTemplate ? undefined : onDragOver}
        />
        {selectedNode && (
          <StepInspector
            node={selectedNode}
            integrationStatus={getNodeIntegration()}
            onUpdate={handleNodeUpdate}
            onClose={() => setSelectedNode(null)}
          />
        )}
      </div>

      {/* JSON toggle */}
      {showJson && (
        <div className="json-editor">
          <pre>
            {JSON.stringify(flowToDefinition(nodes, edges), null, 2)}
          </pre>
        </div>
      )}

      {/* Test console */}
      {showTestConsole && (
        <TestConsole results={testResults} onClose={() => setShowTestConsole(false)} />
      )}
    </div>
  );
}
