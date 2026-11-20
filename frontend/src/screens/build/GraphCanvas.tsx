// SCR-08's canvas (frames A and B): React Flow draws the graph with automatic layout
// (layers stack top to bottom; a skip edge is a labelled curve). Dragging from a node's
// lower handle onto another node connects them; nothing else is edited here, so the list
// view stays the full keyboard path (accessibility note 1).
import "@xyflow/react/dist/base.css";

import {
  Background,
  BackgroundVariant,
  type Connection,
  Controls,
  type Edge,
  Handle,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
} from "@xyflow/react";
import { memo, useMemo } from "react";

import { type Graph, INPUT, layout, OUTPUT } from "../../lib/graph";
import { NodeCard, type NodeView } from "./parts";

type CardNode = Node<{ view: NodeView; selected: boolean }, "card">;

const CardNodeView = memo(function CardNodeView({ data }: NodeProps<CardNode>) {
  return (
    <>
      {data.view.id === INPUT ? null : <Handle type="target" position={Position.Top} />}
      <NodeCard view={data.view} selected={data.selected} />
      {data.view.id === OUTPUT ? null : <Handle type="source" position={Position.Bottom} />}
    </>
  );
});

const NODE_TYPES = { card: CardNodeView };
const ROW = 52;
const COLUMN = 300;

export function GraphCanvas({ graph, views, selected, onSelect, onConnect }: {
  graph: Graph;
  views: NodeView[];
  selected: string | null;
  onSelect: (id: string | null) => void;
  onConnect: (from: string, to: string) => void;
}) {
  const byId = useMemo(() => new Map(views.map((v) => [v.id, v])), [views]);
  const placed = useMemo(() => layout(graph), [graph]);
  const rank = useMemo(() => new Map(placed.map((p) => [p.id, p.rank])), [placed]);

  const nodes: CardNode[] = placed.map((p) => {
    const view = byId.get(p.id);
    return {
      id: p.id,
      type: "card",
      position: { x: (p.slot - (p.of - 1) / 2) * COLUMN, y: p.rank * ROW },
      data: { view: view ?? emptyView(p.id), selected: selected === p.id },
      draggable: false,
      selectable: p.id !== INPUT && p.id !== OUTPUT,
    };
  });
  const edges: Edge[] = graph.edges.map(([from, to]) => {
    const skip = (rank.get(to) ?? 0) - (rank.get(from) ?? 0) > 1;
    const on = selected !== null && (from === selected || to === selected);
    return {
      id: `${from}>${to}`,
      source: from,
      target: to,
      type: skip ? "default" : "straight",
      label: skip ? "skip" : undefined,
      className: on ? "on" : undefined,
    };
  });

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={NODE_TYPES}
      fitView
      fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
      minZoom={0.3}
      maxZoom={1.5}
      nodesDraggable={false}
      onNodeClick={(_, node) => {
        onSelect(node.id === INPUT || node.id === OUTPUT ? null : node.id);
      }}
      onPaneClick={() => {
        onSelect(null);
      }}
      onConnect={(c: Connection) => {
        if (c.source && c.target) onConnect(c.source, c.target);
      }}
      proOptions={{ hideAttribution: true }}
      aria-label="Model graph"
    >
      <Background variant={BackgroundVariant.Dots} gap={16} size={1} color="var(--border)" />
      <Controls showInteractive={false} position="bottom-left" />
    </ReactFlow>
  );
}

function emptyView(id: string): NodeView {
  return { id, title: "", detail: "", shape: "?", parameters: "", locked: false, problem: null, muted: false };
}
