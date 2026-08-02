"""
历史快照持久化 - JSONL日志

FullCompact执行前，将当前完整messages写入JSONL日志文件。
"""
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from langchain_core.messages import BaseMessage, SystemMessage, AIMessage, HumanMessage, ToolMessage

from .config import get_compression_config_manager
from .utils import get_message_role, estimate_messages_tokens
from src.core.utils import estimate_tokens

logger = logging.getLogger(__name__)


class TranscriptSaver:
    """
    历史快照持久化器

    职责：
    1. 在FullCompact执行前保存完整的消息快照
    2. 按会话组织日志文件
    3. 提供日志查询接口
    """

    def __init__(self):
        self.config_manager = get_compression_config_manager()
        self._ensure_logs_dir()

    def _ensure_logs_dir(self):
        """确保日志目录存在"""
        transcript_config = self.config_manager.get_transcript_config()
        logs_dir = transcript_config.get("logsDir", "./logs/compression")
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    async def save_snapshot(
        self,
        messages: List[BaseMessage],
        session_id: str,
        agent_id: str = "",
        compression_type: str = "full",
        snapshot_type: str = "pre-compact"
    ) -> str:
        """
        保存消息快照

        Args:
            messages: 当前消息列表
            session_id: 会话ID
            agent_id: Agent ID
            compression_type: 压缩类型
            snapshot_type: 快照类型 ("pre-compact" 或 "post-compact")

        Returns:
            日志文件路径
        """
        # 创建会话日志目录（sanitize session_id 防止路径穿越）
        safe_session_id = re.sub(r"[^a-zA-Z0-9_-]", "", session_id) or "unknown"
        session_dir = self.logs_dir / safe_session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        # 生成日志文件名
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log_file = session_dir / f"{timestamp}-{snapshot_type}.jsonl"

        # 准备日志记录
        log_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "agentId": agent_id,
            "sessionId": session_id,
            "compressionType": compression_type,
            "snapshotType": snapshot_type,
            "messageCount": len(messages),
            "totalTokens": self._estimate_total_tokens(messages),
            "messages": self._serialize_messages(messages),
        }

        # 写入JSONL文件（原子写入：先写临时文件再 os.replace）
        tmp_file = log_file.with_suffix('.jsonl.tmp')
        try:
            data = json.dumps(log_entry, ensure_ascii=False) + '\n'
            with open(tmp_file, 'w', encoding='utf-8') as f:
                f.write(data)
            os.replace(str(tmp_file), str(log_file))

            logger.info(f"{'压缩后' if snapshot_type == 'post-compact' else '压缩前'}快照已保存: {log_file}")
            return str(log_file)

        except Exception as e:
            logger.error(f"保存快照失败: {e}")
            # 清理残留临时文件
            try:
                tmp_file.unlink(missing_ok=True)
            except Exception:
                pass
            return ""

    def _serialize_messages(self, messages: List[BaseMessage]) -> List[Dict[str, Any]]:
        """序列化消息列表"""
        serialized = []
        for msg in messages:
            msg_dict = {
                "role": self._get_message_role(msg),
                "content": msg.content if isinstance(msg.content, str) else str(msg.content),
            }

            # 添加额外字段
            if isinstance(msg, AIMessage):
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    msg_dict["tool_calls"] = msg.tool_calls
                if hasattr(msg, "additional_kwargs"):
                    msg_dict["additional_kwargs"] = msg.additional_kwargs

            if isinstance(msg, ToolMessage):
                if hasattr(msg, "tool_call_id"):
                    msg_dict["tool_call_id"] = msg.tool_call_id

            if hasattr(msg, "name") and msg.name:
                msg_dict["name"] = msg.name

            serialized.append(msg_dict)

        return serialized

    def _get_message_role(self, msg: BaseMessage) -> str:
        """获取消息角色（委托给公共函数）"""
        return get_message_role(msg)

    def _estimate_total_tokens(self, messages: List[BaseMessage]) -> int:
        """估算总token数（包含所有消息，不含 tool_calls）"""
        return estimate_messages_tokens(messages, include_system=True, include_tool_calls=False)

    def get_session_logs(self, session_id: str) -> List[Dict[str, Any]]:
        """获取会话的所有压缩日志"""
        safe_session_id = re.sub(r"[^a-zA-Z0-9_-]", "", session_id) or "unknown"
        session_dir = self.logs_dir / safe_session_id
        if not session_dir.exists():
            return []

        logs = []
        for log_file in sorted(session_dir.glob("*.jsonl")):
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            log_entry = json.loads(line)
                            log_entry["log_file"] = str(log_file)
                            logs.append(log_entry)
            except Exception as e:
                logger.error(f"读取日志文件失败 {log_file}: {e}")

        return logs

    def get_all_logs(self) -> List[Dict[str, Any]]:
        """获取所有压缩日志"""
        all_logs = []

        if not self.logs_dir.exists():
            return all_logs

        for session_dir in self.logs_dir.iterdir():
            if session_dir.is_dir():
                session_logs = self.get_session_logs(session_dir.name)
                all_logs.extend(session_logs)

        # 按时间排序
        all_logs.sort(key=lambda x: x.get("ts", ""), reverse=True)

        return all_logs

    def get_log_summary(self) -> Dict[str, Any]:
        """获取日志统计摘要"""
        all_logs = self.get_all_logs()

        if not all_logs:
            return {
                "total_sessions": 0,
                "total_compressions": 0,
                "total_tokens_saved": 0,
            }

        sessions = set()
        total_compressions = len(all_logs)
        total_tokens_before = 0
        total_tokens_after = 0

        for log in all_logs:
            sessions.add(log.get("sessionId", ""))
            total_tokens_before += log.get("totalTokens", 0)
            # 注意：这里没有压缩后的token数，需要从其他地方获取

        return {
            "total_sessions": len(sessions),
            "total_compressions": total_compressions,
            "total_tokens_before": total_tokens_before,
            "average_tokens_per_compression": total_tokens_before / total_compressions if total_compressions > 0 else 0,
        }

    def cleanup_old_logs(self, days_to_keep: int = 30):
        """清理旧日志"""
        import time
        cutoff_time = time.time() - (days_to_keep * 24 * 60 * 60)

        if not self.logs_dir.exists():
            return

        deleted_count = 0
        for session_dir in self.logs_dir.iterdir():
            if session_dir.is_dir():
                for log_file in session_dir.glob("*.jsonl"):
                    try:
                        # 检查文件修改时间
                        if log_file.stat().st_mtime < cutoff_time:
                            log_file.unlink()
                            deleted_count += 1
                    except Exception as e:
                        logger.error(f"删除日志文件失败 {log_file}: {e}")

                # 如果目录为空，删除目录
                if not any(session_dir.iterdir()):
                    session_dir.rmdir()

        logger.info(f"清理了 {deleted_count} 个旧日志文件")


# 全局实例
_transcript_saver: Optional[TranscriptSaver] = None


def get_transcript_saver() -> TranscriptSaver:
    """获取全局 TranscriptSaver 实例"""
    global _transcript_saver
    if _transcript_saver is None:
        _transcript_saver = TranscriptSaver()
    return _transcript_saver