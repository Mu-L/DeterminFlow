"""
Rule 配置管理器 - 管理 rules 的分配和优先级
"""
import json
import logging
import os
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class RuleConfigManager:
    """
    管理 rules 的外部配置

    配置存储在 config/rules_config.json 中，包括：
    - agent_types: 分配给哪些 agent 类型
    - priority: 优先级（覆盖 RULE.md 中的默认值）
    """

    def __init__(self, config_file: Path, config_store=None):
        self.config_file = config_file
        self._config_store = config_store
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self._config = self._load()

    def _load(self) -> dict:
        """加载配置文件"""
        if self._config_store is not None:
            return self._config_store.load()
        if not self.config_file.exists():
            default_config = {
                "version": "1.0",
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "description": "Rules 配置文件 - 控制 rules 的分配和优先级",
                "rules": {}
            }
            try:
                self._save_config(default_config)
            except (IOError, OSError) as e:
                logger.warning(f"创建默认 rules 配置失败（内存中使用默认值）: {e}")
            return default_config

        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"加载 rules 配置失败: {e}")
            return {"version": "1.0", "rules": {}}

    def _save_config(self, config: dict):
        """保存配置文件（原子写入）"""
        config["last_updated"] = datetime.now(timezone.utc).isoformat()
        if self._config_store is not None:
            self._config_store.save(config)
            return
        tmp_path = str(self.config_file) + ".tmp"
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, str(self.config_file))
        except (IOError, OSError) as e:
            logger.error(f"保存 rules 配置失败: {e}")
            raise

    def _save(self):
        """保存当前配置"""
        self._save_config(self._config)

    def get_agent_types(self, rule_id: str) -> list[str]:
        """
        获取 rule 分配的 agent 类型（兼容旧接口）

        Args:
            rule_id: rule ID

        Returns:
            agent 类型列表（空列表表示未配置）
        """
        return self._config.get("rules", {}).get(rule_id, {}).get("agent_types", [])

    def set_agent_types(self, rule_id: str, agent_types: list[str]) -> bool:
        """
        设置 rule 的 agent 类型分配（兼容旧接口）

        Args:
            rule_id: rule ID
            agent_types: agent 类型列表

        Returns:
            True 如果设置成功
        """
        try:
            if "rules" not in self._config:
                self._config["rules"] = {}

            if rule_id not in self._config["rules"]:
                self._config["rules"][rule_id] = {}

            self._config["rules"][rule_id]["agent_types"] = agent_types
            self._save()
            logger.info(f"Rule {rule_id} agent_types 已设置为 {agent_types}")
            return True
        except Exception as e:
            logger.error(f"设置 agent_types 失败: {e}")
            return False

    # ============ 组管理方法 ============

    def get_groups(self) -> list[dict]:
        """
        获取所有规则组

        Returns:
            规则组列表，每个组包含 id, name, description
        """
        return self._config.get("groups", [])

    def get_rules_in_group(self, group_id: str) -> list[str]:
        """
        获取组内的 rule ID 列表（通过计算得到）

        Args:
            group_id: 组ID

        Returns:
            属于该组的 rule ID 列表
        """
        result = []
        for rule_id, config in self._config.get("rules", {}).items():
            if group_id in config.get("group_ids", []):
                result.append(rule_id)
        return result

    def get_group(self, group_id: str) -> dict | None:
        """
        获取指定规则组

        Args:
            group_id: 组ID

        Returns:
            规则组信息，不存在时返回 None
        """
        for g in self._config.get("groups", []):
            if g["id"] == group_id:
                return g
        return None

    def create_group(self, group_data: dict) -> dict | None:
        """
        创建新规则组

        Args:
            group_data: 组数据，至少包含 id, name

        Returns:
            创建的组信息。ID 冲突或异常时返回 None。
            调用方可通过先调用 get_group() 区分"ID 冲突"和"真正失败"。
        """
        try:
            if "groups" not in self._config:
                self._config["groups"] = []

            if any(g["id"] == group_data["id"] for g in self._config["groups"]):
                logger.warning(f"规则组 {group_data['id']} 已存在，跳过创建")
                return None

            new_group = {
                "id": group_data["id"],
                "name": group_data.get("name", group_data["id"]),
                "description": group_data.get("description", ""),
            }
            self._config["groups"].append(new_group)
            self._save()
            logger.info(f"规则组 {new_group['id']} 已创建")
            return new_group
        except Exception as e:
            logger.error(f"创建规则组失败: {e}", exc_info=True)
            return None

    def update_group(self, group_id: str, group_data: dict) -> bool:
        """
        更新规则组

        Args:
            group_id: 组ID
            group_data: 更新的字段

        Returns:
            True 如果更新成功
        """
        try:
            groups = self._config.get("groups", [])
            for i, g in enumerate(groups):
                if g["id"] == group_id:
                    if "name" in group_data:
                        groups[i]["name"] = group_data["name"]
                    if "description" in group_data:
                        groups[i]["description"] = group_data["description"]
                    self._save()
                    logger.info(f"规则组 {group_id} 已更新")
                    return True
            logger.error(f"规则组 {group_id} 不存在")
            return False
        except Exception as e:
            logger.error(f"更新规则组失败: {e}")
            return False

    def delete_group(self, group_id: str) -> bool:
        """
        删除规则组

        Args:
            group_id: 组ID

        Returns:
            True 如果删除成功
        """
        try:
            groups = self._config.get("groups", [])
            new_groups = [g for g in groups if g["id"] != group_id]
            if len(new_groups) == len(groups):
                logger.error(f"规则组 {group_id} 不存在")
                return False
            self._config["groups"] = new_groups
            for rule_config in self._config.get("rules", {}).values():
                if "group_ids" in rule_config:
                    rule_config["group_ids"] = [gid for gid in rule_config["group_ids"] if gid != group_id]
            self._save()
            logger.info(f"规则组 {group_id} 已删除")
            return True
        except Exception as e:
            logger.error(f"删除规则组失败: {e}")
            return False

    def get_rule_group_ids(self, rule_id: str) -> list[str]:
        """
        获取 rule 所属的组ID列表

        Args:
            rule_id: rule ID

        Returns:
            组ID列表
        """
        return self._config.get("rules", {}).get(rule_id, {}).get("group_ids", [])

    def set_rule_group_ids(self, rule_id: str, group_ids: list[str]) -> bool:
        """
        设置 rule 所属的组ID列表

        Args:
            rule_id: rule ID
            group_ids: 组ID列表

        Returns:
            True 如果设置成功
        """
        try:
            if "rules" not in self._config:
                self._config["rules"] = {}

            if rule_id not in self._config["rules"]:
                self._config["rules"][rule_id] = {}

            # 更新 rules[].group_ids
            self._config["rules"][rule_id]["group_ids"] = group_ids
            if "agent_types" in self._config["rules"][rule_id]:
                logger.warning(
                    f"Rule {rule_id}: 自动移除已废弃的 agent_types 字段（已迁移到 group_ids 模式）"
                )
                del self._config["rules"][rule_id]["agent_types"]

            self._save()
            logger.info(f"Rule {rule_id} group_ids 已设置为 {group_ids}")
            return True
        except Exception as e:
            logger.error(f"设置 group_ids 失败: {e}")
            return False

    def get_workflow_only(self, rule_id: str) -> bool | None:
        """
        获取 rule 的 workflow_only 配置

        Args:
            rule_id: rule ID

        Returns:
            workflow_only 值（None 表示使用 RULE.md 中的默认值）
        """
        config = self._config.get("rule_configs", {}).get(rule_id, {})
        if "workflow_only" in config:
            return config["workflow_only"]
        return None

    def set_workflow_only(self, rule_id: str, value: bool) -> bool:
        """
        设置 rule 的 workflow_only 配置

        Args:
            rule_id: rule ID
            value: 是否仅工作流可见

        Returns:
            True 如果设置成功
        """
        try:
            if "rule_configs" not in self._config:
                self._config["rule_configs"] = {}

            if rule_id not in self._config["rule_configs"]:
                self._config["rule_configs"][rule_id] = {}

            self._config["rule_configs"][rule_id]["workflow_only"] = value
            self._save()
            logger.info(f"Rule {rule_id} workflow_only 已设置为 {value}")
            return True
        except Exception as e:
            logger.error(f"设置 workflow_only 失败: {e}")
            return False

    def get_config(self, rule_id: str) -> dict:
        """
        获取 rule 的完整配置

        Args:
            rule_id: rule ID

        Returns:
            配置字典
        """
        return self._config.get("rules", {}).get(rule_id, {})

    def get_all_configs(self) -> dict[str, dict]:
        """
        获取所有 rules 的配置

        Returns:
            {rule_id: config} 字典
        """
        return self._config.get("rules", {})

    def remove_rule_config(self, rule_id: str) -> bool:
        """
        移除 rule 的配置（remove_rule 的别名）。

        Args:
            rule_id: rule ID

        Returns:
            True 如果移除成功
        """
        return self.remove_rule(rule_id)

    def remove_rule(self, rule_id: str) -> bool:
        """
        移除 rule 的配置

        Args:
            rule_id: rule ID

        Returns:
            True 如果移除成功
        """
        try:
            if rule_id in self._config.get("rules", {}):
                del self._config["rules"][rule_id]
                self._save()
                logger.info(f"已移除 rule {rule_id} 的配置")
                return True
            return False
        except Exception as e:
            logger.error(f"移除配置失败: {e}")
            return False

    def sync_with_directory(self, rule_ids: list[str]) -> bool:
        """
        同步配置文件与目录中的rules

        Args:
            rule_ids: 目录中存在的rule ID列表

        Returns:
            True 如果同步成功
        """
        try:
            # 确保rules配置存在
            if "rules" not in self._config:
                self._config["rules"] = {}

            # 确保rule_configs配置存在
            if "rule_configs" not in self._config:
                self._config["rule_configs"] = {}

            # 清理已从文件系统删除的 rule 的孤儿配置条目
            rule_id_set = set(rule_ids)
            for stale_id in list(self._config["rules"]):
                if stale_id not in rule_id_set:
                    del self._config["rules"][stale_id]
                    logger.info(f"清理孤儿 rule 配置: {stale_id}")
            for stale_id in list(self._config.get("rule_configs", {})):
                if stale_id not in rule_id_set:
                    del self._config["rule_configs"][stale_id]
                    logger.info(f"清理孤儿 rule_configs 配置: {stale_id}")

            # 为目录中存在但配置中缺少的rule添加默认配置
            for rule_id in rule_ids:
                # 检查rules配置
                if rule_id not in self._config["rules"]:
                    self._config["rules"][rule_id] = {
                        "agent_types": [],  # 空列表表示匹配所有 agent
                        "group_ids": ["default"]  # 默认添加到default组
                    }
                    logger.info(f"为rule {rule_id} 添加默认rules配置")

                # 检查rule_configs配置
                if rule_id not in self._config["rule_configs"]:
                    self._config["rule_configs"][rule_id] = {
                        "enabled": True,
                        "workflow_only": False
                    }
                    logger.info(f"为rule {rule_id} 添加默认rule_configs配置")

            # 确保default组存在
            groups = self._config.get("groups", [])
            default_group_exists = any(g["id"] == "default" for g in groups)
            if not default_group_exists:
                groups.append({
                    "id": "default",
                    "name": "默认规则组",
                    "description": "包含所有现有规则的默认组"
                })
                self._config["groups"] = groups
                logger.info("创建默认规则组")

            self._save()
            logger.info(f"Rules配置同步完成，共同步 {len(rule_ids)} 个rules")
            return True
        except Exception as e:
            logger.error(f"Rules配置同步失败: {e}")
            return False

    def get_rule_configs(self) -> dict:
        """
        获取所有rule的配置（rule_configs部分）

        Returns:
            {rule_id: config} 字典
        """
        return self._config.get("rule_configs", {})

    def reload(self):
        """重新加载配置文件"""
        self._config = self._load()
        logger.info("Rules 配置已重新加载")
