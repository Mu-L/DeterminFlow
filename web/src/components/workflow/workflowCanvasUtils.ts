import { MarkerType } from "reactflow";
import type { Edge, Node } from "reactflow";
import type {
  NodeExecutionInfo,
  TaskDetailResponse,
  WorkflowDefinition,
  WorkflowEdgeDef,
  WorkflowGatewayDef,
  WorkflowNodeDef,
  WorkflowVariable,
} from "../../types";

export const START_NODE_ID = "__start__";
export const END_NODE_ID = "__end__";
export const SNAP_GRID: [number, number] = [10, 10];

const PLACEHOLDER_RE = /\{\{([\w-]+)\}\}/g;
let workflowNodeCounter = 0;

export interface LiveNodeState {
  status: string;
  node_id?: string;
  summary?: string;
  session_id?: string;
  is_skipped?: boolean;
  error?: string;
  parent_node_id?: string;
  attempt_count?: number;
  automatic_retry_count?: number;
  next_retry_at?: string | null;
  attempt_history?: NodeExecutionInfo["attempt_history"];
  input_snapshot?: unknown;
  available_actions?: string[];
}

export interface WorkflowCanvasProps {
  workflowId: string;
  taskId?: string;
  readOnly?: boolean;
  selectionMode?: boolean;
  disabledNodeIds?: Set<string>;
  onNodeToggle?: (nodeId: string, checked: boolean) => void;
  onNodeClick?: (
    nodeId: string,
    sessionId: string,
    nodeType?: string,
    nodeLabel?: string,
    nodeStatus?: string,
  ) => void;
  onDirtyChange?: (dirty: boolean) => void;
  saveRequested?: number;
  onSaveComplete?: () => void;
  onSaveError?: () => void;
  liveNodeStates?: Record<string, LiveNodeState>;
}

export interface ContextMenuState {
  show: boolean;
  x: number;
  y: number;
  type: "node" | "edge";
  nodeId?: string;
  edgeId?: string;
}

export interface ClipboardData {
  nodeType: string;
  nodeData: WorkflowNodeDef | null;
}

const NODE_EXECUTION_STATUSES = new Set<NodeExecutionInfo["status"]>([
  "pending",
  "running",
  "retry_waiting",
  "completed",
  "failed",
  "waiting_approval",
  "skipped",
]);

function isNodeExecutionStatus(status: string): status is NodeExecutionInfo["status"] {
  return NODE_EXECUTION_STATUSES.has(status as NodeExecutionInfo["status"]);
}

export function toNodeExecutionInfo(
  nodeId: string,
  state: LiveNodeState,
  fallbackState?: NodeExecutionInfo,
): NodeExecutionInfo | null {
  if (!isNodeExecutionStatus(state.status)) return null;
  const definedLiveFields = Object.fromEntries(
    Object.entries(state).filter(([, value]) => value !== undefined),
  );
  return {
    ...fallbackState,
    ...definedLiveFields,
    node_id: state.node_id || fallbackState?.node_id || nodeId,
    status: state.status,
    session_id: state.session_id ?? fallbackState?.session_id ?? "",
    summary: state.summary ?? fallbackState?.summary ?? "",
  };
}

export function canvasNodeExecutionData(state: LiveNodeState) {
  return {
    status: state.status,
    summary: state.summary || "",
    session_id: state.session_id || "",
    is_skipped: state.is_skipped || false,
    error: state.error || "",
    attempt_count: state.attempt_count,
    automatic_retry_count: state.automatic_retry_count,
    next_retry_at: state.next_retry_at,
  };
}

export function resolveNodeExecutionInfo(
  nodeId: string,
  liveState: LiveNodeState | undefined,
  persistedState: NodeExecutionInfo | undefined,
): NodeExecutionInfo | undefined {
  return liveState
    ? toNodeExecutionInfo(nodeId, liveState, persistedState) || persistedState
    : persistedState;
}

export function generateEdgeId(source: string, target: string): string {
  return `edge-${source}-${target}`;
}

export function wouldCreateCycle(source: string, target: string, edges: Edge[]): boolean {
  const adjacency = new Map<string, string[]>();
  for (const edge of edges) {
    if (!adjacency.has(edge.source)) adjacency.set(edge.source, []);
    adjacency.get(edge.source)?.push(edge.target);
  }
  if (!adjacency.has(source)) adjacency.set(source, []);
  adjacency.get(source)?.push(target);

  const visited = new Set<string>();
  const queue = [target];
  while (queue.length > 0) {
    const current = queue.shift();
    if (!current) continue;
    if (current === source) return true;
    if (visited.has(current)) continue;
    visited.add(current);
    for (const next of adjacency.get(current) || []) {
      if (!visited.has(next)) queue.push(next);
    }
  }
  return false;
}

function ensureStartEndNodes(
  existingNodes: Node[],
  startPosition: { x: number; y: number } = { x: 300, y: 50 },
  endPosition: { x: number; y: number } = { x: 300, y: 550 },
  isReadOnly = false,
): Node[] {
  const result = [...existingNodes];
  if (!result.some((node) => node.id === START_NODE_ID)) {
    result.unshift({
      id: START_NODE_ID,
      type: "startNode",
      position: startPosition,
      data: {},
      draggable: !isReadOnly,
      deletable: false,
    });
  }
  if (!result.some((node) => node.id === END_NODE_ID)) {
    result.push({
      id: END_NODE_ID,
      type: "endNode",
      position: endPosition,
      data: {},
      draggable: !isReadOnly,
      deletable: false,
    });
  }
  return result;
}

export function buildWorkflowGraph(
  definition: WorkflowDefinition,
  nodeStates: Record<string, NodeExecutionInfo>,
  isTaskMode: boolean,
  isReadOnly: boolean,
  selectionMode: boolean,
  running: boolean,
): { nodes: Node[]; edges: Edge[] } {
  const gatewayIds = new Set((definition.gateways || []).map((gateway) => gateway.id));
  const nodes: Node[] = (definition.nodes || [])
    .filter((node) => !gatewayIds.has(node.id))
    .map((node) => {
      const state = nodeStates[node.id];
      return {
        id: node.id,
        type: "workflowNode",
        position: node.position || { x: 100, y: 100 },
        data: {
          label: node.label,
          agent_type: node.agent_type,
          node_type: node.node_type || "agent",
          status: state?.status || "pending",
          summary: state?.summary || "",
          session_id: state?.session_id || "",
          selectionMode,
          is_skipped: state?.is_skipped || false,
          error: state?.error || "",
          attempt_count: state?.attempt_count,
          automatic_retry_count: state?.automatic_retry_count,
          next_retry_at: state?.next_retry_at,
        },
        deletable: !isReadOnly,
        draggable: !isReadOnly,
      };
    });

  nodes.push(...(definition.gateways || []).map((gateway) => {
    const type = gateway.gateway_type === "parallel"
      ? "parallelGateway"
      : gateway.gateway_type === "condition"
        ? "conditionGateway"
        : gateway.gateway_type === "loop"
          ? "loopGateway"
          : "convergeGateway";
    const label = gateway.label || (
      gateway.gateway_type === "parallel"
        ? "并行网关"
        : gateway.gateway_type === "condition"
          ? "条件网关"
          : gateway.gateway_type === "loop"
            ? "循环网关"
            : "汇聚网关"
    );
    return {
      id: gateway.id,
      type,
      position: gateway.position || { x: 100, y: 100 },
      data: { label, status: "pending" },
      deletable: !isReadOnly,
      draggable: !isReadOnly,
    };
  }));

  const edges: Edge[] = (definition.edges || []).map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    animated: running,
    style: {
      stroke: edge.condition
        ? (edge.condition.is_default ? "#64748B" : "#3B82F6")
        : "#6366F1",
      strokeWidth: 2,
    },
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: edge.condition
        ? (edge.condition.is_default ? "#64748B" : "#3B82F6")
        : "#6366F1",
    },
    label: edge.condition && !edge.condition.is_default
      ? edge.condition.label || edge.condition.expression
      : edge.condition?.is_default
        ? "默认"
        : undefined,
    labelStyle: {
      fontSize: 12,
      fill: edge.condition?.is_default ? "#64748B" : "#3B82F6",
    },
    deletable: !isReadOnly,
    data: { condition: edge.condition || null },
  }));

  return {
    nodes: ensureStartEndNodes(
      nodes,
      definition.start_position,
      definition.end_position,
      isReadOnly,
    ),
    edges,
  };
}

interface LoadWorkflowGraphOptions {
  workflowId: string;
  taskId?: string;
  isTaskMode: boolean;
  isReadOnly: boolean;
  selectionMode: boolean;
}

export interface LoadedWorkflowGraph {
  definition: WorkflowDefinition;
  nodeStates: Record<string, NodeExecutionInfo>;
  nodes: Node[];
  edges: Edge[];
}

export async function loadWorkflowGraph({
  workflowId,
  taskId,
  isTaskMode,
  isReadOnly,
  selectionMode,
}: LoadWorkflowGraphOptions): Promise<LoadedWorkflowGraph | null> {
  let definition: WorkflowDefinition | null = null;
  let nodeStates: Record<string, NodeExecutionInfo> = {};
  let running = false;

  if (isTaskMode && taskId) {
    const response = await fetch(`/api/workflows/${workflowId}/tasks/${taskId}`);
    if (response.ok) {
      const data = await response.json() as TaskDetailResponse;
      definition = data.definition;
      nodeStates = data.task.node_states || {};
      running = data.task.status === "running";
    }
  } else {
    const response = await fetch(`/api/workflows/${workflowId}`);
    if (response.ok) {
      const data = await response.json() as { definition: WorkflowDefinition };
      definition = data.definition;
    }
  }

  if (!definition) return null;
  const graph = buildWorkflowGraph(
    definition,
    nodeStates,
    isTaskMode,
    isReadOnly,
    selectionMode,
    running,
  );
  return { definition, nodeStates, ...graph };
}

export function getVariableReferences(
  definition: WorkflowDefinition | null,
): Record<string, string[]> {
  if (!definition) return {};
  const references: Record<string, Set<string>> = {};

  const collectReferences = (value: unknown, nodeId: string) => {
    if (typeof value !== "string" || !value) return;
    for (const match of value.matchAll(PLACEHOLDER_RE)) {
      (references[match[1]] ??= new Set()).add(nodeId);
    }
  };

  for (const node of definition.nodes) {
    const texts = [node.label, node.agent_type, node.system_prompt_template, node.first_message];
    for (const text of texts) {
      collectReferences(text, node.id);
    }
    for (const value of Object.values(node.node_params || {})) {
      collectReferences(value, node.id);
    }
    for (const binding of Object.values(node.var_bindings || {})) {
      if (!binding || typeof binding !== "object") continue;
      const varKey = binding.var_key;
      if (typeof varKey === "string" && varKey) {
        (references[varKey] ??= new Set()).add(node.id);
      }
    }
  }
  return Object.fromEntries(
    Object.entries(references).map(([key, nodeIds]) => [key, [...nodeIds]]),
  );
}

export interface DroppedWorkflowNode {
  canvasNode: Node;
  definitionNode: WorkflowNodeDef | null;
  isGateway: boolean;
}

export function createDroppedWorkflowNode(
  nodeType: string,
  position: { x: number; y: number },
): DroppedWorkflowNode {
  const isGateway = [
    "parallel_gateway",
    "converge_gateway",
    "condition_gateway",
    "loop_gateway",
  ].includes(nodeType);
  if (isGateway) {
    const gatewayType = nodeType === "parallel_gateway"
      ? "parallel"
      : nodeType === "condition_gateway"
        ? "condition"
        : nodeType === "loop_gateway"
          ? "loop"
          : "converge";
    const reactFlowType = nodeType === "parallel_gateway"
      ? "parallelGateway"
      : nodeType === "condition_gateway"
        ? "conditionGateway"
        : nodeType === "loop_gateway"
          ? "loopGateway"
          : "convergeGateway";
    const id = `gw-${Date.now().toString(36)}-${workflowNodeCounter}`;
    workflowNodeCounter += 1;
    return {
      isGateway: true,
      definitionNode: null,
      canvasNode: {
        id,
        type: reactFlowType,
        position,
        data: {
          label: gatewayType === "parallel"
            ? "并行网关"
            : gatewayType === "condition"
              ? "条件网关"
              : gatewayType === "loop"
                ? "循环网关"
                : "汇聚网关",
          status: "pending",
        },
        deletable: true,
        draggable: true,
      },
    };
  }

  workflowNodeCounter += 1;
  const id = `agent_${Date.now().toString(36)}_${workflowNodeCounter}`;
  const isApproval = nodeType === "approval";
  const isSubprocess = nodeType === "subprocess";
  const label = isApproval ? "审批" : isSubprocess ? "新子流程" : "新 Agent";
  const nodeParams: Record<string, string> = isApproval
    ? { file_paths: "", rejection_reason_placeholder: "请输入驳回原因..." }
    : {};
  return {
    isGateway: false,
    canvasNode: {
      id,
      type: "workflowNode",
      position,
      data: {
        label,
        node_type: nodeType,
        agent_type: (isApproval || isSubprocess) ? "" : "default",
        status: "pending",
        summary: "",
        ...(isApproval ? { node_params: nodeParams } : {}),
      },
      deletable: true,
      draggable: true,
    },
    definitionNode: {
      id,
      label: isApproval ? "审批" : "新 Agent",
      node_type: nodeType,
      agent_type: isApproval ? "" : "default",
      system_prompt_template: "",
      first_message: "",
      position,
      node_params: nodeParams,
    },
  };
}

export function buildWorkflowSavePayload(
  definition: WorkflowDefinition | null,
  nodes: Node[],
  edges: Edge[],
  variables: WorkflowVariable[],
) {
  const definitionNodes = definition?.nodes || [];
  const definitionGateways = definition?.gateways || [];
  const updatedNodes: WorkflowNodeDef[] = nodes
    .filter((node) =>
      node.id !== START_NODE_ID
      && node.id !== END_NODE_ID
      && !["parallelGateway", "convergeGateway", "conditionGateway", "loopGateway"].includes(node.type || ""),
    )
    .map((node) => {
      const original = definitionNodes.find((candidate) => candidate.id === node.id);
      const data = node.data as Partial<WorkflowNodeDef>;
      const updated: WorkflowNodeDef = {
        id: node.id,
        label: data.label || original?.label || "",
        node_type: data.node_type || original?.node_type || "agent",
        agent_type: data.agent_type || original?.agent_type || "default",
        system_prompt_template: original?.system_prompt_template || "",
        first_message: original?.first_message || "",
        position: node.position,
        var_bindings: original?.var_bindings || {},
        node_params: original?.node_params || data.node_params || {},
        auto_flow: original?.auto_flow || false,
        enable_complete_node_task: original?.enable_complete_node_task !== false,
        output_variable: original?.output_variable || "",
        enable_reject_upstream: original?.enable_reject_upstream || false,
        max_reject_count: original?.max_reject_count || 3,
        save_output_to_file: original?.save_output_to_file || false,
        output_file_path: original?.output_file_path || "",
        model_override: original?.model_override || "",
        auto_retry_count: original?.auto_retry_count ?? 0,
        auto_retry_interval_seconds: original?.auto_retry_interval_seconds ?? 0,
        fail_auto_skip: original?.fail_auto_skip || false,
      };
      if (updated.node_type === "subprocess") {
        updated.sub_workflow_id = original?.sub_workflow_id || null;
        updated.sub_scheme_id = original?.sub_scheme_id || null;
        updated.sub_workflow_params = original?.sub_workflow_params || {};
      }
      return updated;
    });

  const updatedGateways: WorkflowGatewayDef[] = nodes
    .filter((node) => ["parallelGateway", "convergeGateway", "conditionGateway", "loopGateway"].includes(node.type || ""))
    .map((node) => {
      const original = definitionGateways.find((candidate) => candidate.id === node.id);
      const gatewayType: WorkflowGatewayDef["gateway_type"] = node.type === "parallelGateway"
        ? "parallel"
        : node.type === "conditionGateway"
          ? "condition"
          : node.type === "loopGateway"
            ? "loop"
            : "converge";
      const data = node.data as { label?: string };
      return {
        id: node.id,
        gateway_type: gatewayType,
        label: data.label || original?.label || (
          gatewayType === "parallel"
            ? "并行网关"
            : gatewayType === "condition"
              ? "条件网关"
              : gatewayType === "loop"
                ? "循环网关"
                : "汇聚网关"
        ),
        position: node.position,
        converge_gateway_id: original?.converge_gateway_id || null,
      };
    });

  const validIds = new Set([
    ...updatedNodes.map((node) => node.id),
    ...updatedGateways.map((gateway) => gateway.id),
  ]);
  const gatewayTypes = Object.fromEntries(
    updatedGateways.map((gateway) => [gateway.id, gateway.gateway_type]),
  );
  const updatedEdges: WorkflowEdgeDef[] = edges
    .filter((edge) =>
      (edge.source === START_NODE_ID || validIds.has(edge.source))
      && (edge.target === END_NODE_ID || validIds.has(edge.target)),
    )
    .map((edge) => {
      const updated: WorkflowEdgeDef = {
        id: edge.id,
        source: edge.source,
        target: edge.target,
      };
      if (gatewayTypes[edge.source] === "condition" || gatewayTypes[edge.source] === "loop") {
        const data = edge.data as { condition?: WorkflowEdgeDef["condition"] } | undefined;
        const original = definition?.edges.find((candidate) => candidate.id === edge.id);
        if (data?.condition) updated.condition = data.condition;
        else if (original?.condition) updated.condition = original.condition;
      }
      return updated;
    });

  return {
    name: definition?.name || "",
    nodes: updatedNodes,
    edges: updatedEdges,
    variables,
    gateways: updatedGateways,
    execution_schemes: definition?.execution_schemes || [],
    start_position: nodes.find((node) => node.id === START_NODE_ID)?.position || { x: 300, y: 50 },
    end_position: nodes.find((node) => node.id === END_NODE_ID)?.position || { x: 300, y: 550 },
  };
}
