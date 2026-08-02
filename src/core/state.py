"""
LangGraph 状态定义 - 定义 Agent 工作流的统一状态结构
"""
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    """
    Agent 图的统一状态。

    字段说明:
    - messages: 对话消息列表，使用 add_messages 注解实现自动追加合并
    - session_id: 当前会话 ID
    - status: 会话状态
      - "running": 会话活跃，可接收消息和执行任务
      - "streaming": LLM 正在流式输出中
      - "completed": 当前任务已完成，会话仍可被打开和追问
      - "waiting": 等待外部输入（如 sub agent 请求 main 帮助）
      - "error": 发生错误，会话不可用
      - "idle": 会话空闲（加载的历史会话）
    - remaining_rounds: 剩余工具调用轮次（防止无限循环）
    - metadata: 可扩展的元数据字典
    - agent_type: Agent 类型标识符（用于工具过滤和 prompt 适配）
    """
    messages: Annotated[list[BaseMessage], add_messages]
    session_id: str
    status: str
    remaining_rounds: int
    metadata: dict
    agent_type: str
