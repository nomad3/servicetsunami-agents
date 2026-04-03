import React, { useCallback } from 'react';
import ReactFlow, {
  Background, Controls, MiniMap, addEdge,
} from 'reactflow';
import 'reactflow/dist/style.css';

import TriggerNode from './nodes/TriggerNode';
import StepNode from './nodes/StepNode';
import ConditionNode from './nodes/ConditionNode';
import ForEachNode from './nodes/ForEachNode';
import ParallelNode from './nodes/ParallelNode';
import ApprovalNode from './nodes/ApprovalNode';

const nodeTypes = {
  triggerNode: TriggerNode,
  stepNode: StepNode,
  conditionNode: ConditionNode,
  forEachNode: ForEachNode,
  parallelNode: ParallelNode,
  approvalNode: ApprovalNode,
};

export default function WorkflowCanvas({
  nodes, edges, onNodesChange, onEdgesChange, setEdges,
  onNodeClick, onDrop, onDragOver, readOnly = false,
}) {
  const onConnect = useCallback(
    (params) => setEdges((eds) => addEdge({
      ...params,
      animated: false,
      style: { stroke: '#64748b' },
    }, eds)),
    [setEdges]
  );

  return (
    <div className="workflow-canvas" onDrop={onDrop} onDragOver={onDragOver}
         style={{ flex: 1, height: '100%' }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={readOnly ? undefined : onNodesChange}
        onEdgesChange={readOnly ? undefined : onEdgesChange}
        onConnect={readOnly ? undefined : onConnect}
        onNodeClick={onNodeClick}
        nodeTypes={nodeTypes}
        fitView
        deleteKeyCode={readOnly ? null : 'Delete'}
        nodesDraggable={!readOnly}
        nodesConnectable={!readOnly}
        elementsSelectable
        className="ocean-canvas"
      >
        <Background color="#334155" gap={20} size={1} />
        <Controls showInteractive={!readOnly} />
        <MiniMap
          nodeStrokeColor="#64748b"
          nodeColor="#1e293b"
          maskColor="rgba(15, 23, 42, 0.7)"
        />
      </ReactFlow>
    </div>
  );
}
