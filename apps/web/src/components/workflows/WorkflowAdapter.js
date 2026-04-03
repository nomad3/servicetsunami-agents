import dagre from '@dagrejs/dagre';

const NODE_WIDTH = 220;
const NODE_HEIGHT = 80;

const STEP_TYPE_MAP = {
  mcp_tool: 'stepNode',
  agent: 'stepNode',
  transform: 'stepNode',
  wait: 'stepNode',
  webhook_trigger: 'stepNode',
  continue_as_new: 'stepNode',
  cli_execute: 'stepNode',
  internal_api: 'stepNode',
  condition: 'conditionNode',
  for_each: 'forEachNode',
  parallel: 'parallelNode',
  human_approval: 'approvalNode',
};

let _idCounter = 0;
function nextId(prefix = 'step') {
  return `${prefix}-${Date.now()}-${_idCounter++}`;
}

function applyDagreLayout(nodes, edges) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'TB', nodesep: 50, ranksep: 80 });

  nodes.forEach((node) => {
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  });
  edges.forEach((edge) => {
    g.setEdge(edge.source, edge.target);
  });

  dagre.layout(g);

  return nodes.map((node) => {
    const pos = g.node(node.id);
    return {
      ...node,
      position: { x: pos.x - NODE_WIDTH / 2, y: pos.y - NODE_HEIGHT / 2 },
    };
  });
}

export function definitionToFlow(definition, triggerConfig) {
  const nodes = [];
  const edges = [];

  const triggerId = 'trigger-root';
  nodes.push({
    id: triggerId,
    type: 'triggerNode',
    data: { trigger: triggerConfig || { type: 'manual' } },
    position: { x: 0, y: 0 },
  });

  const steps = definition?.steps || [];

  function processSteps(stepList, prevNodeId) {
    let currentPrev = prevNodeId;

    stepList.forEach((step) => {
      const nodeId = step.id || nextId();
      const nodeType = STEP_TYPE_MAP[step.type] || 'stepNode';

      nodes.push({
        id: nodeId,
        type: nodeType,
        data: { step: { ...step, id: nodeId } },
        position: { x: 0, y: 0 },
      });

      edges.push({
        id: `e-${currentPrev}-${nodeId}`,
        source: currentPrev,
        target: nodeId,
        sourceHandle: null,
        style: { stroke: '#64748b' },
      });

      if (step.type === 'condition') {
        // Create edges from condition to then/else targets using handle IDs
        // The then/else fields reference step IDs in the same flat list
        if (step.then) {
          edges.push({
            id: `e-${nodeId}-then-${step.then}`,
            source: nodeId,
            sourceHandle: 'then',
            target: step.then,
            style: { stroke: '#4ade80' },
            label: 'then',
          });
        }
        if (step.else && step.else !== 'skip') {
          edges.push({
            id: `e-${nodeId}-else-${step.else}`,
            source: nodeId,
            sourceHandle: 'else',
            target: step.else,
            style: { stroke: '#f87171', strokeDasharray: '5,5' },
            label: 'else',
          });
        }
        // Don't advance currentPrev — condition branches are non-linear
        // Next sequential step will connect from wherever the branches converge
        currentPrev = nodeId;
      } else if (step.type === 'for_each' && step.steps?.length) {
        const lastChild = processSteps(step.steps, nodeId);
        currentPrev = lastChild || nodeId;
      } else if (step.type === 'parallel' && step.steps?.length) {
        const mergeId = `merge-${nodeId}`;
        nodes.push({
          id: mergeId,
          type: 'stepNode',
          data: { step: { id: mergeId, type: 'transform', operation: 'merge' } },
          position: { x: 0, y: 0 },
        });
        step.steps.forEach((subStep) => {
          const subId = subStep.id || nextId();
          const subType = STEP_TYPE_MAP[subStep.type] || 'stepNode';
          nodes.push({
            id: subId,
            type: subType,
            data: { step: { ...subStep, id: subId } },
            position: { x: 0, y: 0 },
          });
          edges.push({
            id: `e-${nodeId}-${subId}`,
            source: nodeId,
            target: subId,
            style: { stroke: '#64748b' },
          });
          edges.push({
            id: `e-${subId}-${mergeId}`,
            source: subId,
            target: mergeId,
            style: { stroke: '#64748b' },
          });
        });
        currentPrev = mergeId;
      } else if (step.type === 'condition') {
        // Condition edges are handled by the canvas — just mark the node
        currentPrev = nodeId;
      } else {
        currentPrev = nodeId;
      }
    });

    return currentPrev;
  }

  processSteps(steps, triggerId);

  const layoutedNodes = applyDagreLayout(nodes, edges);
  return { nodes: layoutedNodes, edges };
}

export function flowToDefinition(nodes, edges) {
  const triggerNode = nodes.find((n) => n.type === 'triggerNode');
  const triggerConfig = triggerNode?.data?.trigger || { type: 'manual' };

  // Build adjacency: parent -> [child1, child2, ...]
  const children = {};
  edges.forEach((e) => {
    if (!children[e.source]) children[e.source] = [];
    children[e.source].push(e);
  });

  // Build simple child ID map for non-condition lookups
  const childIds = {};
  edges.forEach((e) => {
    if (!childIds[e.source]) childIds[e.source] = [];
    childIds[e.source].push(e.target);
  });

  const visited = new Set();

  // Walk a linear chain from startId, collecting steps sequentially
  function walkChain(startId) {
    const steps = [];
    let currentId = startId;

    while (currentId) {
      if (visited.has(currentId)) break;
      visited.add(currentId);

      const node = nodes.find((n) => n.id === currentId);
      if (!node || node.id.startsWith('merge-')) break;
      if (node.type === 'triggerNode') {
        // Skip trigger, follow its child
        const nextList = childIds[currentId] || [];
        currentId = nextList[0] || null;
        continue;
      }

      const step = { ...(node.data?.step || {}), id: node.id };

      // For conditions: serialize then/else from edge handles, then continue
      // walking all reachable branch targets into the flat steps list
      // (the executor runs steps[] sequentially; then/else are ID references)
      if (step.type === 'condition') {
        const outEdges = children[currentId] || [];
        const branchTargets = [];
        outEdges.forEach((edge) => {
          if (edge.sourceHandle === 'then') {
            step.then = edge.target;
            branchTargets.push(edge.target);
          } else if (edge.sourceHandle === 'else') {
            step.else = edge.target;
            branchTargets.push(edge.target);
          }
        });
        steps.push(step);
        // Walk each branch target's chain into the flat list
        branchTargets.forEach((targetId) => {
          const branchSteps = walkChain(targetId);
          steps.push(...branchSteps);
        });
        // No more sequential steps from this condition — branches handle the rest
        currentId = null;
        continue;
      }

      // For for_each/parallel: children are sub-steps, not sequential successors
      if (step.type === 'for_each') {
        step.steps = walkChain(currentId + '-child-start');
        // If no special child-start, use direct children as sub-steps
        if (step.steps.length === 0) {
          const subNodeIds = childIds[currentId] || [];
          visited.delete(currentId);
          const subSteps = [];
          subNodeIds.forEach((subId) => {
            if (!visited.has(subId)) {
              const chain = walkChain(subId);
              subSteps.push(...chain);
            }
          });
          step.steps = subSteps;
        }
      } else if (step.type === 'parallel') {
        const subNodeIds = childIds[currentId] || [];
        step.steps = subNodeIds
          .filter((id) => !id.startsWith('merge-'))
          .map((subId) => {
            const subNode = nodes.find((n) => n.id === subId);
            if (!subNode) return null;
            visited.add(subId);
            return { ...(subNode.data?.step || {}), id: subId };
          })
          .filter(Boolean);
        // Find merge node to continue after
        const mergeId = `merge-${currentId}`;
        const mergeNext = childIds[mergeId] || [];
        currentId = mergeNext[0] || null;
        steps.push(step);
        continue;
      }

      steps.push(step);

      // Follow the chain: next sequential node
      const nextList = childIds[currentId] || [];
      // For non-container nodes, follow the first (only) child
      currentId = nextList.length === 1 ? nextList[0] : null;

      // If multiple children and not parallel/for_each/condition, just take first (linear)
      if (nextList.length > 1 && !['for_each', 'parallel', 'condition'].includes(step.type)) {
        currentId = nextList[0];
      }
    }

    return steps;
  }

  const steps = walkChain(triggerNode?.id || 'trigger-root');
  return { definition: { steps }, triggerConfig };
}
