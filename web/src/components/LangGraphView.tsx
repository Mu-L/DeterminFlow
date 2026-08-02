import { useMemo } from "react";
import ReactFlow, {
  Node,
  Edge,
  Background,
  Handle,
  Position,
  NodeProps,
} from "reactflow";
import "reactflow/dist/style.css";
import { GraphStructure } from "../types";

interface LangGraphViewProps {
  mainGraph: GraphStructure;
  subGraph: GraphStructure;
}

// ============ Custom Nodes ============

function LLMNode({ data }: NodeProps) {
  return (
    <div
      className="px-4 py-2 rounded-lg bg-indigo-500/15 border-2 border-indigo-500/50 min-w-[100px] min-h-[44px] text-center flex flex-col justify-center"
      role="article"
      aria-label={`LLM 节点: ${data.label}${data.description ? `, ${data.description}` : ""}`}
    >
      <Handle type="target" position={Position.Top} className="opacity-0" />
      <div className="text-xs font-bold text-indigo-400">{data.label}</div>
      <div className="text-xs text-muted-foreground mt-0.5">{data.description}</div>
      <Handle type="source" position={Position.Bottom} className="opacity-0" />
    </div>
  );
}

function ToolNodeComponent({ data }: NodeProps) {
  return (
    <div
      className="px-4 py-2 rounded-lg bg-amber-500/15 border-2 border-amber-500/50 min-w-[100px] min-h-[44px] text-center flex flex-col justify-center"
      role="article"
      aria-label={`工具节点: ${data.label}${data.description ? `, ${data.description}` : ""}`}
    >
      <Handle type="target" position={Position.Top} className="opacity-0" />
      <div className="text-xs font-bold text-amber-400">{data.label}</div>
      <div className="text-xs text-muted-foreground mt-0.5">{data.description}</div>
      <Handle type="source" position={Position.Bottom} className="opacity-0" />
    </div>
  );
}

function CheckNode({ data }: NodeProps) {
  return (
    <div
      className="px-4 py-2 rounded-lg bg-cyan-500/15 border-2 border-cyan-500/50 min-w-[100px] min-h-[44px] text-center flex flex-col justify-center"
      role="article"
      aria-label={`检查节点: ${data.label}${data.description ? `, ${data.description}` : ""}`}
    >
      <Handle type="target" position={Position.Top} className="opacity-0" />
      <div className="text-xs font-bold text-cyan-400">{data.label}</div>
      <div className="text-xs text-muted-foreground mt-0.5">{data.description}</div>
      <Handle type="source" position={Position.Bottom} className="opacity-0" />
    </div>
  );
}

function StartEndNode({ data }: NodeProps) {
  return (
    <div
      className="px-3 py-1.5 rounded-full bg-slate-700 border border-muted-foreground/30 text-center min-h-[44px] flex items-center justify-center"
      role="article"
      aria-label={`${data.label} 节点`}
    >
      <Handle type="target" position={Position.Top} className="opacity-0" />
      <div className="text-xs text-muted-foreground">{data.label}</div>
      <Handle type="source" position={Position.Bottom} className="opacity-0" />
    </div>
  );
}

const nodeTypes = {
  llmNode: LLMNode,
  toolNode: ToolNodeComponent,
  checkNode: CheckNode,
  startEnd: StartEndNode,
};

// ============ Graph Builder ============

function buildFlowElements(graph: GraphStructure, offsetX: number = 0) {
  const nodes: Node[] = [];
  const edges: Edge[] = [];

  // Add start node
  nodes.push({
    id: `${graph.name}__start__`,
    type: "startEnd",
    position: { x: offsetX + 120, y: 20 },
    data: { label: "START" },
  });

  // Add end node
  nodes.push({
    id: `${graph.name}__end__`,
    type: "startEnd",
    position: { x: offsetX + 120, y: 340 },
    data: { label: "END" },
  });

  // Position nodes
  const positions: Record<string, { x: number; y: number }> = {
    llm: { x: offsetX + 100, y: 80 },
    tools: { x: offsetX + 100, y: 180 },
    check_status: { x: offsetX + 250, y: 230 },
  };

  graph.nodes.forEach((node) => {
    const pos = positions[node.id] || { x: offsetX + 100, y: 150 };
    const typeMap: Record<string, string> = { llm: "llmNode", tool: "toolNode", check: "checkNode" };

    nodes.push({
      id: `${graph.name}_${node.id}`,
      type: typeMap[node.type] || "llmNode",
      position: pos,
      data: { label: node.label, description: node.description?.slice(0, 30) || "" },
    });
  });

  // Add edges
  graph.edges.forEach((edge, i) => {
    const sourceId = edge.source === "__start__"
      ? `${graph.name}__start__`
      : `${graph.name}_${edge.source}`;
    const targetId = edge.target === "__end__"
      ? `${graph.name}__end__`
      : `${graph.name}_${edge.target}`;

    edges.push({
      id: `${graph.name}_edge_${i}`,
      source: sourceId,
      target: targetId,
      label: edge.label?.slice(0, 20) || "",
      animated: edge.conditional,
      style: {
        stroke: edge.conditional ? "rgb(245 158 11)" : "rgb(99 102 241)", // amber-500 : indigo-500
        strokeWidth: 1.5,
      },
      className: edge.conditional ? "motion-reduce:!transition-none motion-reduce:!animate-none" : undefined,
      labelStyle: { fontSize: 12, fill: "rgb(148 163 184)" }, // slate-400
    });
  });

  return { nodes, edges };
}

// ============ Main Component ============

export default function LangGraphView({ mainGraph, subGraph }: LangGraphViewProps) {
  const { nodes, edges } = useMemo(() => {
    const main = buildFlowElements(mainGraph, 0);
    const sub = buildFlowElements(subGraph, 350);
    return {
      nodes: [...main.nodes, ...sub.nodes],
      edges: [...main.edges, ...sub.edges],
    };
  }, [mainGraph, subGraph]);

  return (
    <div
      className="w-full h-full relative"
      role="application"
      aria-label="LangGraph 结构可视化图"
    >
      {/* Labels */}
      <span className="sr-only">此图包含 Main Agent 和 Sub Agent 两个图结构</span>
      <div
        className="absolute top-2 left-4 z-10 text-xs font-medium text-indigo-400 bg-slate-800 border border-slate-700 rounded px-2 py-1"
        aria-hidden="true"
      >
        Main Agent Graph
      </div>
      <div
        className="absolute top-2 left-4 z-10 text-xs font-medium text-cyan-400 bg-slate-800 border border-slate-700 rounded px-2 py-1"
        style={{ transform: "translateX(350px)" }}
        aria-hidden="true"
      >
        Sub Agent Graph
      </div>

      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        className="bg-slate-900"
        nodesDraggable={false}
      >
        <Background color="rgb(51 65 85)" gap={20} />
      </ReactFlow>
    </div>
  );
}
