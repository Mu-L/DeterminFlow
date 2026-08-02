import { request } from "./http-client";

export interface NodeFailureActionResponse {
  success: boolean;
  message: string;
  task_id?: string;
  node_id?: string;
  node_state?: import("../types").NodeExecutionInfo;
}

/** 在原 Task 中使用失败时冻结的输入重试当前节点。 */
export async function retryWorkflowNode(
  workflowId: string,
  taskId: string,
  nodeId: string,
  expectedAttemptCount: number,
) {
  return request<NodeFailureActionResponse>(
    `/workflows/${encodeURIComponent(workflowId)}/tasks/${encodeURIComponent(taskId)}/nodes/${encodeURIComponent(nodeId)}/retry`,
    {
      method: "POST",
      body: JSON.stringify({ expected_attempt_count: expectedAttemptCount }),
    },
  );
}

/** 跳过当前失败节点并继续原 Task。 */
export async function skipWorkflowNode(
  workflowId: string,
  taskId: string,
  nodeId: string,
  expectedAttemptCount: number,
) {
  return request<NodeFailureActionResponse>(
    `/workflows/${encodeURIComponent(workflowId)}/tasks/${encodeURIComponent(taskId)}/nodes/${encodeURIComponent(nodeId)}/skip`,
    {
      method: "POST",
      body: JSON.stringify({ expected_attempt_count: expectedAttemptCount }),
    },
  );
}
