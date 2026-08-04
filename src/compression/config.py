"""
压缩配置管理器 - 管理上下文压缩的配置参数

配置存储在 config/compression_config.json
"""
import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from src.environment import get_determinflow_env

logger = logging.getLogger(__name__)


class CompressionConfigManager:
    """
    压缩配置管理器

    配置存储在 config/compression_config.json，包含：
    - 通用配置（compactionThreshold, enabled）
    - MicroCompact配置
    - FullCompact配置
    - ReactiveCompact配置
    - PostCompact配置
    - Transcript配置
    """

    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    _CONFIG_ROOT = Path(
        get_determinflow_env("CONFIG_DIR", str(_PROJECT_ROOT / "config"))
    ).expanduser().resolve()
    _DEFAULT_CONFIG_PATH = str(_CONFIG_ROOT / "compression_config.json")

    def __init__(self, config_path: str | None = None):
        self.config_path = Path(config_path or self._DEFAULT_CONFIG_PATH)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """加载配置文件，如无则创建默认配置"""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"加载压缩配置失败: {e}")
                return self._create_default_config()

        return self._create_default_config()

    def _create_default_config(self) -> Dict[str, Any]:
        """创建默认配置"""
        config = {
            "version": "1.0",
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "description": "上下文压缩配置 - 管理压缩策略参数",
            "general": {
                "compactionThreshold": 0.80,
                "enabled": True
            },
            "micro_compact": {
                "maxToolResults": 15,
                "toolResultTokenRatio": 0.40,
                "keepRecentToolResults": 5,
                "placeholder": "[Content compacted]"
            },
            "full_compact": {
                "keepRecentTokens": 51200,
                "maxRetryCount": 2,
                "summaryTokenBudget": 4096
            },
            "reactive_compact": {
                "maxRetryCount": 5
            },
            "post_compact": {
                "maxFilesToRead": 5,
                "maxTokensPerFile": 5000
            },
            "transcript": {
                "logsDir": "./logs/compression"
            }
        }
        self._config = config
        try:
            self.save()
        except IOError as e:
            logger.warning(f"保存默认压缩配置失败（将使用内存默认值）: {e}")
        return config

    def save(self):
        """保存配置到 JSON 文件（原子写入）"""
        tmp_path = str(self.config_path) + ".tmp"
        try:
            self._config["last_updated"] = datetime.now(timezone.utc).isoformat()
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.config_path)
            logger.info(f"压缩配置已保存到 {self.config_path}")
        except IOError as e:
            logger.error(f"保存压缩配置失败: {e}")
            # 清理残留临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def get_config(self) -> Dict[str, Any]:
        """获取完整配置"""
        return self._config.copy()

    def get_all_config(self) -> Dict[str, Any]:
        """获取所有配置（别名）"""
        return self.get_config()

    def get_general_config(self) -> Dict[str, Any]:
        """获取通用配置"""
        return self._config.get("general", {})

    def get_micro_compact_config(self) -> Dict[str, Any]:
        """获取MicroCompact配置"""
        return self._config.get("micro_compact", {})

    def get_full_compact_config(self) -> Dict[str, Any]:
        """获取FullCompact配置"""
        return self._config.get("full_compact", {})

    def get_reactive_compact_config(self) -> Dict[str, Any]:
        """获取ReactiveCompact配置"""
        return self._config.get("reactive_compact", {})

    def get_post_compact_config(self) -> Dict[str, Any]:
        """获取PostCompact配置"""
        return self._config.get("post_compact", {})

    def get_transcript_config(self) -> Dict[str, Any]:
        """获取Transcript配置"""
        return self._config.get("transcript", {})

    def update_config(self, updates: Dict[str, Any]) -> bool:
        """更新配置

        Args:
            updates: 要更新的配置项

        Returns:
            是否更新成功
        """
        try:
            self._config.update(updates)
            self.save()
            logger.info("压缩配置已更新")
            return True
        except Exception as e:
            logger.error(f"更新压缩配置失败: {e}")
            return False

    def update_section(self, section_key: str, updates: Dict[str, Any]) -> bool:
        """通用配置段更新：合并 updates 到指定段并持久化。

        Args:
            section_key: 配置段键名（如 'general', 'micro_compact'）
            updates: 要合并的更新字典

        Returns:
            是否更新成功
        """
        section = self._config.get(section_key, {})
        old_section = dict(section)  # 备份用于回滚
        section.update(updates)
        self._config[section_key] = section
        try:
            self.save()
            return True
        except IOError:
            # 写入失败，回滚内存状态
            self._config[section_key] = old_section
            return False

    def is_enabled(self) -> bool:
        """检查压缩是否启用"""
        return self._config.get("general", {}).get("enabled", True)

    def get_compaction_threshold(self) -> float:
        """获取压缩阈值"""
        return self._config.get("general", {}).get("compactionThreshold", 0.80)

    def reload(self):
        """重新加载配置"""
        self._config = self._load_config()
        logger.info("压缩配置已重新加载")


# 全局实例
_compression_config_manager: Optional[CompressionConfigManager] = None


def get_compression_config_manager() -> CompressionConfigManager:
    """获取全局 CompressionConfigManager 实例"""
    global _compression_config_manager
    if _compression_config_manager is None:
        _compression_config_manager = CompressionConfigManager()
    return _compression_config_manager
