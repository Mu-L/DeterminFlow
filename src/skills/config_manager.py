"""
Skill 配置管理器 - 管理 skills 的外部配置（如自动注入设置）
"""
import json
import logging
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class SkillConfigManager:
    """
    管理 skills 的外部配置

    配置存储在 config/skills_config.json 中，包括：
    - auto_inject: 是否自动注入到提示词
    - 未来可扩展其他配置
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
                "description": "Skills 配置文件 - 控制 skills 的自动注入行为",
                "skills": {}
            }
            try:
                self._save_config(default_config)
            except (IOError, OSError) as e:
                logger.warning(f"创建默认 skills 配置失败（内存中使用默认值）: {e}")
            return default_config

        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"加载 skills 配置失败: {e}")
            return {"version": "1.0", "skills": {}}

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
            logger.error(f"保存 skills 配置失败: {e}")
            raise

    def _save(self):
        """保存当前配置"""
        self._save_config(self._config)

    # ── 通用 getter / setter（消除各 setter 方法的重复 try/ensure/save 模式）──

    def _get_skill_config(self, skill_id: str, key: str, default=None, *, section: str = "skills"):
        """从指定配置节读取单个字段值。

        Args:
            skill_id: skill ID
            key: 字段名
            default: 默认值
            section: 配置节名称（"skills" 或 "skill_configs"）
        """
        return self._config.get(section, {}).get(skill_id, {}).get(key, default)

    def _set_skill_config(self, skill_id: str, key: str, value, *, section: str = "skills") -> bool:
        """向指定配置节写入单个字段并持久化。

        统一处理：确保 section 存在、确保 skill_id 子 dict 存在、
        赋值、save、日志、异常捕获。

        Args:
            skill_id: skill ID
            key: 字段名
            value: 字段值
            section: 配置节名称（"skills" 或 "skill_configs"）

        Returns:
            True 如果设置成功
        """
        try:
            if section not in self._config:
                self._config[section] = {}
            if skill_id not in self._config[section]:
                self._config[section][skill_id] = {}
            self._config[section][skill_id][key] = value
            self._save()
            logger.info(f"Skill {skill_id} {key} 已设置为 {value}")
            return True
        except Exception as e:
            logger.error(f"设置 {key} 失败: {e}")
            return False

    def should_auto_inject(self, skill_id: str) -> bool:
        """
        判断 skill 是否应该自动注入

        优先从 skill_configs 节读取（source of truth），
        回退到 skills 节（向后兼容旧配置格式）。

        Args:
            skill_id: skill ID

        Returns:
            True 如果应该自动注入，否则 False（默认 False）
        """
        # 优先读 skill_configs（与 get_enabled 保持一致的 source of truth）
        configs_val = self._config.get("skill_configs", {}).get(skill_id, {}).get("auto_inject")
        if configs_val is not None:
            return configs_val
        # 回退到 skills 节（旧配置兼容）
        return self._config.get("skills", {}).get(skill_id, {}).get("auto_inject", False)

    def set_auto_inject(self, skill_id: str, value: bool) -> bool:
        """
        设置 skill 的自动注入配置

        同步更新 skills 节和 skill_configs 节

        Args:
            skill_id: skill ID
            value: 是否自动注入

        Returns:
            True 如果设置成功
        """
        # 同时更新 skills 和 skill_configs 两个节，保持一致
        ok_skills = self._set_skill_config(skill_id, "auto_inject", value, section="skills")
        ok_configs = self._set_skill_config(skill_id, "auto_inject", value, section="skill_configs")
        return ok_skills or ok_configs

    def get_agent_types(self, skill_id: str) -> list[str]:
        """获取 skill 分配的 agent 类型（兼容旧接口）"""
        return self._get_skill_config(skill_id, "agent_types", [])

    def set_agent_types(self, skill_id: str, agent_types: list[str]) -> bool:
        """设置 skill 的 agent 类型分配（兼容旧接口）"""
        return self._set_skill_config(skill_id, "agent_types", agent_types)

    # ============ 组管理方法 ============

    def get_groups(self) -> list[dict]:
        """
        获取所有技能组

        Returns:
            技能组列表，每个组包含 id, name, description
        """
        return self._config.get("groups", [])

    def get_skills_in_group(self, group_id: str) -> list[str]:
        """
        获取组内的 skill ID 列表（通过计算得到）

        Args:
            group_id: 组ID

        Returns:
            属于该组的 skill ID 列表
        """
        result = []
        for skill_id, config in self._config.get("skills", {}).items():
            if group_id in config.get("group_ids", []):
                result.append(skill_id)
        return result

    def get_group(self, group_id: str) -> dict | None:
        """
        获取指定技能组

        Args:
            group_id: 组ID

        Returns:
            技能组信息，不存在时返回 None
        """
        for g in self._config.get("groups", []):
            if g["id"] == group_id:
                return g
        return None

    def create_group(self, group_data: dict) -> dict | None:
        """
        创建新技能组

        Args:
            group_data: 组数据，至少包含 id, name

        Returns:
            创建的组信息。ID 冲突或异常时返回 None。
            调用方可通过先调用 get_group() 区分"ID 冲突"和"真正失败"。
        """
        try:
            if "groups" not in self._config:
                self._config["groups"] = []

            # 检查 ID 是否已存在
            if any(g["id"] == group_data["id"] for g in self._config["groups"]):
                logger.warning(f"技能组 {group_data['id']} 已存在，跳过创建")
                return None

            new_group = {
                "id": group_data["id"],
                "name": group_data.get("name", group_data["id"]),
                "description": group_data.get("description", ""),
            }
            self._config["groups"].append(new_group)
            self._save()
            logger.info(f"技能组 {new_group['id']} 已创建")
            return new_group
        except Exception as e:
            logger.error(f"创建技能组失败: {e}", exc_info=True)
            return None

    def update_group(self, group_id: str, group_data: dict) -> bool:
        """
        更新技能组

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
                    logger.info(f"技能组 {group_id} 已更新")
                    return True
            logger.error(f"技能组 {group_id} 不存在")
            return False
        except Exception as e:
            logger.error(f"更新技能组失败: {e}")
            return False

    def delete_group(self, group_id: str) -> bool:
        """
        删除技能组

        Args:
            group_id: 组ID

        Returns:
            True 如果删除成功
        """
        try:
            groups = self._config.get("groups", [])
            new_groups = [g for g in groups if g["id"] != group_id]
            if len(new_groups) == len(groups):
                logger.error(f"技能组 {group_id} 不存在")
                return False
            self._config["groups"] = new_groups
            # 同时移除所有 skill 对该组的引用
            for skill_config in self._config.get("skills", {}).values():
                if "group_ids" in skill_config:
                    skill_config["group_ids"] = [gid for gid in skill_config["group_ids"] if gid != group_id]
            self._save()
            logger.info(f"技能组 {group_id} 已删除")
            return True
        except Exception as e:
            logger.error(f"删除技能组失败: {e}")
            return False

    def get_skill_group_ids(self, skill_id: str) -> list[str]:
        """获取 skill 所属的组ID列表"""
        return self._get_skill_config(skill_id, "group_ids", [])

    def set_skill_group_ids(self, skill_id: str, group_ids: list[str]) -> bool:
        """设置 skill 所属的组ID列表。同时移除旧的 agent_types 字段。"""
        ok = self._set_skill_config(skill_id, "group_ids", group_ids)
        if ok:
            # 移除旧的 agent_types 字段（走组模式）
            skill_cfg = self._config.get("skills", {}).get(skill_id, {})
            if "agent_types" in skill_cfg:
                del skill_cfg["agent_types"]
        return ok

    def get_priority(self, skill_id: str) -> int | None:
        """获取 skill 的优先级配置（None 表示使用 SKILL.md 中的默认值）"""
        return self._get_skill_config(skill_id, "priority")

    def set_priority(self, skill_id: str, priority: int) -> bool:
        """设置 skill 的优先级"""
        return self._set_skill_config(skill_id, "priority", priority)

    def get_auto_inject_skills(self) -> list[str]:
        """
        获取所有标记为自动注入的 skill IDs

        Returns:
            skill ID 列表
        """
        result = []
        for skill_id, config in self._config.get("skills", {}).items():
            if config.get("auto_inject", False):
                result.append(skill_id)
        return result

    def get_workflow_only(self, skill_id: str) -> bool | None:
        """获取 skill 的 workflow_only 配置（None 表示使用 SKILL.md 中的默认值）"""
        val = self._get_skill_config(skill_id, "workflow_only", section="skill_configs")
        return val  # None if not set

    def set_workflow_only(self, skill_id: str, value: bool) -> bool:
        """设置 skill 的 workflow_only 配置"""
        return self._set_skill_config(skill_id, "workflow_only", value, section="skill_configs")

    def get_config(self, skill_id: str) -> dict:
        """获取 skill 的完整配置"""
        return self._config.get("skills", {}).get(skill_id, {})

    def get_all_configs(self) -> dict[str, dict]:
        """获取所有 skills 的配置"""
        return self._config.get("skills", {})

    def get_enabled(self, skill_id: str) -> bool:
        """获取 skill 的启用/禁用状态（默认 True）"""
        return self._get_skill_config(skill_id, "enabled", True, section="skill_configs")

    def set_enabled(self, skill_id: str, enabled: bool) -> bool:
        """设置 skill 的启用/禁用状态（持久化到 skill_configs 节）"""
        return self._set_skill_config(skill_id, "enabled", enabled, section="skill_configs")

    def remove_skill_config(self, skill_id: str) -> bool:
        """
        移除 skill 的配置（remove_skill 的别名）。

        Args:
            skill_id: skill ID

        Returns:
            True 如果移除成功
        """
        return self.remove_skill(skill_id)

    def remove_skill(self, skill_id: str) -> bool:
        """
        移除 skill 的配置（同时清理 skills 和 skill_configs 两个节）。

        Args:
            skill_id: skill ID

        Returns:
            True 如果移除成功
        """
        try:
            found = False
            if skill_id in self._config.get("skills", {}):
                del self._config["skills"][skill_id]
                found = True
            if skill_id in self._config.get("skill_configs", {}):
                del self._config["skill_configs"][skill_id]
                found = True
            if found:
                self._save()
                logger.info(f"已移除 skill {skill_id} 的配置")
            return found
        except Exception as e:
            logger.error(f"移除配置失败: {e}")
            return False

    def sync_with_directory(self, skill_ids: list[str]) -> bool:
        """
        同步配置文件与目录中的skills

        Args:
            skill_ids: 目录中存在的skill ID列表

        Returns:
            True 如果同步成功
        """
        try:
            # 确保skills配置存在
            if "skills" not in self._config:
                self._config["skills"] = {}

            # 确保skill_configs配置存在
            if "skill_configs" not in self._config:
                self._config["skill_configs"] = {}

            # 为目录中存在但配置中缺少的skill添加默认配置
            for skill_id in skill_ids:
                # 检查skills配置（不自动分配组，由管理员显式分配）
                if skill_id not in self._config["skills"]:
                    self._config["skills"][skill_id] = {}
                    logger.info(f"为skill {skill_id} 添加默认skills配置（未分配组）")

                # 检查skill_configs配置
                if skill_id not in self._config["skill_configs"]:
                    self._config["skill_configs"][skill_id] = {
                        "enabled": True,
                        "priority": 50,
                        "auto_inject": False,
                        "workflow_only": False
                    }
                    logger.info(f"为skill {skill_id} 添加默认skill_configs配置")

            # 确保default组存在
            groups = self._config.get("groups", [])
            default_group_exists = any(g["id"] == "default" for g in groups)
            if not default_group_exists:
                groups.append({
                    "id": "default",
                    "name": "默认技能组",
                    "description": "包含所有现有技能的默认组"
                })
                self._config["groups"] = groups
                logger.info("创建默认技能组")

            self._save()
            logger.info(f"配置同步完成，共同步 {len(skill_ids)} 个skills")
            return True
        except Exception as e:
            logger.error(f"配置同步失败: {e}")
            return False

    def get_skill_configs(self) -> dict:
        """
        获取所有skill的配置（skill_configs部分）

        Returns:
            {skill_id: config} 字典
        """
        return self._config.get("skill_configs", {})

    def reload(self):
        """重新加载配置文件"""
        self._config = self._load()
        logger.info("Skills 配置已重新加载")
