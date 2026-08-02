/**
 * StartEndNodes - START/END 终端节点 + 并行/汇聚/条件网关节点渲染
 */
import { Handle, Position, type NodeProps } from "reactflow";

export function StartNode() {
  return (
    <div className="relative px-6 py-2.5 rounded-full bg-slate-900 border-2 border-green-500/50 shadow-lg shadow-green-500/5" role="article" aria-label="工作流开始节点">
      <div className="text-xs font-semibold text-green-500 tracking-wider">START</div>
      <Handle
        type="source"
        position={Position.Bottom}
        className="!bg-green-500 !w-2.5 !h-2.5 !border-2 !border-slate-900"
      />
    </div>
  );
}

export function EndNode() {
  return (
    <div className="relative px-6 py-2.5 rounded-full bg-slate-900 border-2 border-red-500/50 shadow-lg shadow-red-500/5" role="article" aria-label="工作流结束节点">
      <Handle
        type="target"
        position={Position.Top}
        className="!bg-red-500 !w-2.5 !h-2.5 !border-2 !border-slate-900"
      />
      <div className="text-xs font-semibold text-red-500 tracking-wider">END</div>
    </div>
  );
}

const GATEWAY_COLORS: Record<string, string> = {
  parallel: "#8B5CF6",
  converge: "#F59E0B",
  condition: "#3B82F6",
  loop: "#10B981",
};

const GATEWAY_LABELS: Record<string, string> = {
  parallel: "并行",
  converge: "汇聚",
  condition: "条件",
  loop: "循环",
};

function GatewayNode({ data, type }: NodeProps & { type: string }) {
  const gatewayType =
    type === "parallelGateway" ? "parallel" :
    type === "convergeGateway" ? "converge" :
    type === "conditionGateway" ? "condition" :
    type === "loopGateway" ? "loop" : "parallel";
  const color = GATEWAY_COLORS[gatewayType] || "#8b5cf6";
  const nodeLabel = GATEWAY_LABELS[gatewayType] || gatewayType;
  const status = (data as Record<string, string>)?.status || "";
  const isActive = status === "running";

  return (
    <div
      className={`relative flex items-center justify-center w-14 h-14 rotate-45 bg-slate-900 border-2 shadow-lg transition-all duration-300 ${
        isActive ? "animate-pulse motion-reduce:animate-none" : ""
      }`}
      style={{ borderColor: `${color}${isActive ? "" : "80"}`, boxShadow: `0 0 10px ${color}20` }}
      role="article"
      aria-label={`${nodeLabel}网关节点${isActive ? "，运行中" : ""}`}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!bg-slate-400 !w-2 !h-2 !border-2 !border-slate-900"
      />
      <Handle
        type="source"
        position={Position.Bottom}
        className="!bg-slate-400 !w-2 !h-2 !border-2 !border-slate-900"
      />
      <div className="-rotate-45 text-xs font-bold" style={{ color }}>{nodeLabel}</div>
    </div>
  );
}

export function ParallelGatewayNode(props: NodeProps) {
  return <GatewayNode {...props} type="parallelGateway" />;
}

export function ConvergeGatewayNode(props: NodeProps) {
  return <GatewayNode {...props} type="convergeGateway" />;
}

export function ConditionGatewayNode(props: NodeProps) {
  return <GatewayNode {...props} type="conditionGateway" />;
}

export function LoopGatewayNode(props: NodeProps) {
  return <GatewayNode {...props} type="loopGateway" />;
}
