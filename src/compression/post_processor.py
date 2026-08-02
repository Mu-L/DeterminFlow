"""
压缩后处理器 - 文件补读和边界消息注入

FullCompact执行后，需要将近期操作过的文件重新注入上下文，
并注入compact_boundary边界消息。
"""
import logging
import os
from typing import List, Dict, Any, Optional
from pathlib import Path

from langchain_core.messages import BaseMessage, SystemMessage, AIMessage, HumanMessage

from .config import get_compression_config_manager
from src.core.utils import estimate_tokens

logger = logging.getLogger(__name__)


class PostProcessor:
    """
    压缩后处理器

    职责：
    1. 文件补读：将近期操作过的文件重新注入上下文
    2. 边界消息注入：注入compact_boundary消息
    """

    def __init__(self):
        self.config_manager = get_compression_config_manager()
        # 文件读取注册表（简化版，实际应该从Agent会话获取）
        self.file_read_registry: List[Dict[str, Any]] = []

    async def process(
        self,
        messages: List[BaseMessage],
        session_id: str = ""
    ) -> List[BaseMessage]:
        """
        执行压缩后处理

        Args:
            messages: 压缩后的消息列表
            session_id: 会话ID

        Returns:
            处理后的消息列表
        """
        # 获取配置
        post_config = self.config_manager.get_post_compact_config()
        max_files_to_read = post_config.get("maxFilesToRead", 5)
        max_tokens_per_file = post_config.get("maxTokensPerFile", 5000)

        # 获取近期操作过的文件
        recent_files = self._get_recent_files(max_files_to_read)

        # 读取文件内容
        file_contents = []
        for file_path in recent_files:
            content = await self._read_file_content(file_path, max_tokens_per_file)
            if content:
                file_contents.append({
                    "path": file_path,
                    "content": content,
                })

        # 注入边界消息
        boundary_message = self._create_boundary_message(
            session_id=session_id,
            file_count=len(file_contents),
            has_files=len(file_contents) > 0
        )

        # 构建最终消息列表
        result = list(messages)

        # 在摘要消息后注入边界消息
        # 找到摘要消息的位置（通常是第一个非system的assistant消息）
        insert_index = 0
        for i, msg in enumerate(result):
            if isinstance(msg, SystemMessage):
                continue
            if isinstance(msg, AIMessage):
                insert_index = i + 1
                break

        # 插入边界消息
        result.insert(insert_index, boundary_message)

        # 如果有文件内容，注入为附件消息
        if file_contents:
            file_message = self._create_file_message(file_contents)
            result.insert(insert_index + 1, file_message)

        return result

    def _get_recent_files(self, max_files: int) -> List[str]:
        """获取近期操作过的文件列表"""
        # 从文件读取注册表获取最近的文件
        recent_files = []
        seen_paths = set()

        # 按时间倒序排列
        sorted_registry = sorted(
            self.file_read_registry,
            key=lambda x: x.get("timestamp", 0),
            reverse=True
        )

        for entry in sorted_registry:
            path = entry.get("path", "")
            if path and path not in seen_paths:
                # 排除某些文件类型
                if self._should_exclude_file(path):
                    continue
                recent_files.append(path)
                seen_paths.add(path)

                if len(recent_files) >= max_files:
                    break

        return recent_files

    def _should_exclude_file(self, file_path: str) -> bool:
        """检查是否应该排除该文件"""
        exclude_names = {
            "plan.md",
        }
        exclude_dir_names = {
            "memory",
            "compression_logs",
        }
        exclude_suffixes = {
            ".log",
        }

        path = Path(file_path)
        path_lower = file_path.lower()
        name_lower = path.name.lower()

        # 精确文件名匹配
        if name_lower in exclude_names:
            return True
        # 精确后缀匹配
        if any(name_lower.endswith(suf) for suf in exclude_suffixes):
            return True
        # 路径中包含目录名
        parts_lower = [p.lower() for p in path.parts]
        if any(d in parts_lower for d in exclude_dir_names):
            return True

        return False

    async def _read_file_content(
        self,
        file_path: str,
        max_tokens: int
    ) -> Optional[str]:
        """读取文件内容"""
        try:
            path = Path(file_path).resolve()
            if not path.exists():
                logger.warning(f"文件不存在: {file_path}")
                return None

            # 路径安全检查：禁止访问系统敏感路径（使用 Path.is_relative_to 做精确目录边界检查）
            _SENSITIVE_DIRS = (Path("/etc"), Path("/proc"), Path("/sys"), Path("/dev"), Path("/boot"))
            if any(path.is_relative_to(d) for d in _SENSITIVE_DIRS):
                logger.warning(f"拒绝访问系统敏感路径: {file_path}")
                return None

            # 检查文件大小
            file_size = path.stat().st_size
            if file_size > 1024 * 1024:  # 1MB限制
                logger.warning(f"文件过大，跳过: {file_path} ({file_size} bytes)")
                return None

            # 读取文件内容
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # 截断到最大token数
            tokens = estimate_tokens(content)
            if tokens > max_tokens:
                # 使用保守字符比（中文约1.5-2字符/token），预留截断标记空间
                truncate_marker = "\n... [内容已截断]"
                marker_len = len(truncate_marker)
                max_chars = max_tokens * 2 - marker_len  # 保守估算
                if max_chars > 0:
                    # 按 UTF-8 字符边界安全截断（Python str 切片已保证 codepoint 完整）
                    content = content[:max_chars] + truncate_marker
                else:
                    content = truncate_marker

            return content

        except Exception as e:
            logger.error(f"读取文件失败 {file_path}: {e}")
            return None

    def _create_boundary_message(
        self,
        session_id: str,
        file_count: int,
        has_files: bool
    ) -> SystemMessage:
        """创建边界消息（system role，作为 LLM 上下文元指令）"""
        content = "[Context Compact Boundary]\n\n"
        content += "上下文已被压缩。以下是压缩后的上下文结构：\n\n"
        content += "1. 系统提示词（保持不变）\n"
        content += "2. 对话摘要（压缩后的关键信息）\n"
        content += "3. 本边界消息\n"
        content += "4. 最近的对话原文（保留）\n"

        if has_files:
            content += f"5. 近期操作的文件内容（{file_count}个文件）\n"

        content += "\n注意：之前的对话历史已被压缩为摘要，请基于摘要继续工作。"

        return SystemMessage(content=content)

    def _create_file_message(self, file_contents: List[Dict[str, Any]]) -> SystemMessage:
        """创建文件内容消息（系统注入，非 assistant 消息）"""
        content = "[附件文件]\n\n"
        content += "以下是近期操作过的文件内容，供参考：\n\n"

        for file_info in file_contents:
            path = file_info["path"]
            file_content = file_info["content"]
            content += f"--- {path} ---\n"
            content += file_content
            content += "\n\n"

        return SystemMessage(content=content)

    def register_file_read(self, file_path: str, operation_type: str = "read"):
        """注册文件读取操作（仅允许工作区内路径）"""
        import time
        # 路径安全检查：禁止注册系统敏感路径
        try:
            resolved = Path(file_path).resolve()
            _SENSITIVE_DIRS = (Path("/etc"), Path("/proc"), Path("/sys"), Path("/dev"), Path("/boot"))
            if any(resolved.is_relative_to(d) for d in _SENSITIVE_DIRS):
                logger.warning(f"拒绝注册系统敏感路径: {file_path}")
                return
        except (OSError, ValueError):
            logger.warning(f"路径解析失败，跳过注册: {file_path}")
            return

        self.file_read_registry.append({
            "path": file_path,
            "timestamp": time.time(),
            "operation": operation_type,
        })

        # 保持注册表大小
        max_entries = 100
        if len(self.file_read_registry) > max_entries:
            self.file_read_registry = self.file_read_registry[-max_entries:]


# 全局实例
_post_processor: Optional[PostProcessor] = None


def get_post_processor() -> PostProcessor:
    """获取全局 PostProcessor 实例"""
    global _post_processor
    if _post_processor is None:
        _post_processor = PostProcessor()
    return _post_processor