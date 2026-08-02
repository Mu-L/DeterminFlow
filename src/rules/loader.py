"""
Rule 加载器 - 从文件系统加载 rules
"""
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Callable
import yaml

from src.extension_api.registrar import OwnedPath

from .models import Rule

logger = logging.getLogger(__name__)


class RuleResourceConflictError(ValueError):
    """Raised when multiple active owners claim the same Rule ID."""


class RuleLoader:
    """
    Rule 加载器

    从文件系统加载 rules，每个 rule 是一个文件夹，包含 RULE.md 文件。
    RULE.md 格式：YAML frontmatter + Markdown 内容
    """

    def __init__(
        self,
        rules_dir: Path,
        resource_roots: list[OwnedPath] | None = None,
        owner_enabled: Callable[[str], bool] | None = None,
    ):
        self.rules_dir = rules_dir
        self.resource_roots = list(resource_roots or [])
        self.owner_enabled = owner_enabled or (lambda _owner: True)
        self.rules_dir.mkdir(parents=True, exist_ok=True)

    def _roots(self, *, include_inactive: bool = False) -> list[OwnedPath]:
        plugin_roots = [
            root
            for root in self.resource_roots
            if include_inactive or self.owner_enabled(root.owner)
        ]
        return [OwnedPath("user", self.rules_dir.resolve()), *plugin_roots]

    def load_all(self, *, include_inactive: bool = False) -> list[Rule]:
        """
        加载所有 rules

        Returns:
            Rule 对象列表
        """
        rules: list[Rule] = []
        claimed: dict[str, tuple[str, Path]] = {}

        for root in self._roots(include_inactive=include_inactive):
            root_path = root.path.resolve()
            if not root_path.exists():
                raise FileNotFoundError(
                    f"Rule bundle 根目录不存在: {root.owner}: {root_path}"
                )
            if not root_path.is_dir():
                raise ValueError(
                    f"Rule bundle 根路径必须是目录: {root.owner}: {root_path}"
                )
            for rule_dir in sorted(root_path.iterdir()):
                if not rule_dir.is_dir():
                    continue

                rule_file = rule_dir / "RULE.md"
                if not rule_file.exists():
                    logger.debug(f"跳过目录 {rule_dir.name}: 缺少 RULE.md")
                    continue

                rule = self._load_rule_dir(
                    rule_dir,
                    owner=root.owner,
                    read_only=root.owner != "user",
                    strict=root.owner != "user",
                )
                if rule is None:
                    if root.owner != "user":
                        raise ValueError(
                            f"Plugin Rule bundle 无效: "
                            f"{root.owner}: {rule_dir.resolve()}"
                        )
                    continue
                previous = claimed.get(rule.id)
                if previous is not None:
                    previous_owner, previous_path = previous
                    raise RuleResourceConflictError(
                        f"Rule resource 冲突: {rule.id} "
                        f"({previous_owner}: {previous_path} vs "
                        f"{root.owner}: {rule_dir.resolve()})"
                    )
                claimed[rule.id] = (root.owner, rule_dir.resolve())
                rules.append(rule)

        logger.info(f"成功加载 {len(rules)} 个 rules")
        return rules

    def validate_sources(self) -> None:
        """Validate every declared owner, including currently disabled Plugins."""
        self.load_all(include_inactive=True)

    def load_rule(self, rule_id: str) -> Rule | None:
        """
        加载单个 rule

        Args:
            rule_id: rule ID（文件夹名）

        Returns:
            Rule 对象，如果加载失败则返回 None
        """
        rule_dir = self.rules_dir / rule_id
        return self._load_rule_dir(rule_dir)

    def _load_rule_dir(
        self,
        rule_dir: Path,
        *,
        owner: str = "user",
        read_only: bool = False,
        strict: bool = False,
    ) -> Rule | None:
        """Load one Rule directory while preserving its owning resource."""
        rule_id = rule_dir.name
        rule_file = rule_dir / "RULE.md"

        if not rule_file.exists():
            logger.error(f"Rule 文件不存在: {rule_file}")
            return None

        try:
            with open(rule_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # 解析 YAML frontmatter
            frontmatter, body = RuleLoader._parse_frontmatter(content)

            # 验证必需字段
            if "name" not in frontmatter:
                logger.error(f"Rule {rule_id} 缺少 name 字段")
                return None

            # 提取字段
            name = frontmatter["name"]
            description = frontmatter.get("description", "")
            summary = frontmatter.get("summary", "")

            # 解析 metadata（仅保留 version, author）
            # 使用副本避免修改调用方传入的 frontmatter 原始数据
            metadata = dict(frontmatter.get("metadata", {}))
            version = metadata.get("version", "1.0")
            author = metadata.get("author", "")

            # 保存 rule 目录路径
            metadata["rule_dir"] = str(rule_dir.resolve())
            metadata["resource_owner"] = owner
            metadata["resource_read_only"] = read_only

            rule = Rule(
                id=rule_id,
                name=name,
                description=description,
                summary=summary,
                content=body.strip(),
                version=version,
                author=author,
                metadata=metadata,
            )

            logger.debug(f"成功加载 rule: {rule_id}")
            return rule

        except Exception as e:
            if strict:
                raise ValueError(
                    f"解析 rule {rule_id} 失败: {e}"
                ) from e
            logger.error(f"解析 rule {rule_id} 失败: {e}")
            return None

    @staticmethod
    def _parse_frontmatter(content: str) -> tuple[dict, str]:
        """
        解析 YAML frontmatter（静态方法，供外部模块复用）。

        Args:
            content: 文件内容

        Returns:
            (frontmatter_dict, body_content)
        """
        # 匹配 YAML frontmatter: ---\n...\n---
        pattern = r'^---\s*\n(.*?)\n---\s*\n(.*)$'
        match = re.match(pattern, content, re.DOTALL)

        if not match:
            # 没有 frontmatter，整个内容作为 body
            return {}, content

        yaml_str = match.group(1)
        body = match.group(2)

        try:
            frontmatter = yaml.safe_load(yaml_str) or {}
        except yaml.YAMLError as e:
            logger.error(f"解析 YAML frontmatter 失败: {e}")
            frontmatter = {}

        return frontmatter, body

    def save_rule(self, rule: Rule) -> bool:
        """
        保存 rule 到文件系统

        Args:
            rule: Rule 对象

        Returns:
            True 如果保存成功
        """
        rule_dir = self.rules_dir / rule.id
        rule_dir.mkdir(parents=True, exist_ok=True)
        rule_file = rule_dir / "RULE.md"

        try:
            # 构建 frontmatter
            frontmatter = {
                "name": rule.name,
                "description": rule.description,
                "metadata": {
                    "version": rule.version,
                    "author": rule.author,
                }
            }
            if rule.summary:
                frontmatter["summary"] = rule.summary

            # 构建完整内容
            yaml_str = yaml.dump(frontmatter, allow_unicode=True, sort_keys=False)
            content = f"---\n{yaml_str}---\n\n{rule.content}"

            tmp_file = str(rule_file) + ".tmp"
            with open(tmp_file, 'w', encoding='utf-8') as f:
                f.write(content)
            os.replace(tmp_file, rule_file)

            # 更新 rule 对象的元数据（目录路径）
            rule.metadata["rule_dir"] = str(rule_dir)

            logger.info(f"成功保存 rule: {rule.id}")
            return True

        except Exception as e:
            logger.error(f"保存 rule {rule.id} 失败: {e}")
            # 清理残留临时文件
            try:
                os.unlink(str(rule_file) + ".tmp")
            except OSError:
                pass
            return False

    def delete_rule(self, rule_id: str) -> bool:
        """
        删除 rule

        Args:
            rule_id: rule ID

        Returns:
            True 如果删除成功
        """
        rule_dir = self.rules_dir / rule_id

        if not rule_dir.exists():
            logger.warning(f"Rule 目录不存在: {rule_dir}")
            return False

        try:
            shutil.rmtree(rule_dir)
            logger.info(f"成功删除 rule: {rule_id}")
            return True
        except Exception as e:
            logger.error(f"删除 rule {rule_id} 失败: {e}")
            return False
