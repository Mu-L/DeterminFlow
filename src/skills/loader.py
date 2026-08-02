"""
Agent Skills 加载器 - 符合 agentskills.io 标准

标准格式：
skill-name/
├── SKILL.md          # 必需：YAML frontmatter + Markdown 内容
├── scripts/          # 可选：可执行代码
├── references/       # 可选：参考文档
├── assets/           # 可选：模板、资源
└── ...
"""
import re
import logging
import shutil
from pathlib import Path
from typing import Any, Callable
import yaml

from src.extension_api.registrar import OwnedPath

from .models import Skill, SkillCategory

logger = logging.getLogger(__name__)


class SkillResourceConflictError(ValueError):
    """Raised when multiple active owners claim the same Skill ID."""


class SkillLoader:
    """从文件系统加载符合 Agent Skills 标准的 skills"""

    def __init__(
        self,
        skills_dir: Path,
        resource_roots: list[OwnedPath] | None = None,
        owner_enabled: Callable[[str], bool] | None = None,
    ):
        self.skills_dir = skills_dir
        self.resource_roots = list(resource_roots or [])
        self.owner_enabled = owner_enabled or (lambda _owner: True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def _roots(self, *, include_inactive: bool = False) -> list[OwnedPath]:
        plugin_roots = [
            root
            for root in self.resource_roots
            if include_inactive or self.owner_enabled(root.owner)
        ]
        return [OwnedPath("user", self.skills_dir.resolve()), *plugin_roots]

    def load_all(self, *, include_inactive: bool = False) -> list[Skill]:
        """加载所有 skill 目录"""
        skills: list[Skill] = []
        claimed: dict[str, tuple[str, Path]] = {}

        for root in self._roots(include_inactive=include_inactive):
            root_path = root.path.resolve()
            if not root_path.exists():
                raise FileNotFoundError(
                    f"Skill bundle 根目录不存在: {root.owner}: {root_path}"
                )
            if not root_path.is_dir():
                raise ValueError(
                    f"Skill bundle 根路径必须是目录: {root.owner}: {root_path}"
                )
            for skill_dir in sorted(root_path.iterdir()):
                if not skill_dir.is_dir():
                    continue

                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    logger.warning(f"跳过目录 {skill_dir.name}: 缺少 SKILL.md")
                    continue

                skill = self._load_skill_dir(
                    skill_dir,
                    owner=root.owner,
                    read_only=root.owner != "user",
                    strict=root.owner != "user",
                )
                if skill is None:
                    if root.owner != "user":
                        raise ValueError(
                            f"Plugin Skill bundle 无效: "
                            f"{root.owner}: {skill_dir.resolve()}"
                        )
                    continue
                previous = claimed.get(skill.id)
                if previous is not None:
                    previous_owner, previous_path = previous
                    raise SkillResourceConflictError(
                        f"Skill resource 冲突: {skill.id} "
                        f"({previous_owner}: {previous_path} vs "
                        f"{root.owner}: {skill_dir.resolve()})"
                    )
                claimed[skill.id] = (root.owner, skill_dir.resolve())
                skills.append(skill)

        logger.info(f"已加载 {len(skills)} 个 skills")
        return skills

    def validate_sources(self) -> None:
        """Validate every declared owner, including currently disabled Plugins."""
        self.load_all(include_inactive=True)

    def _load_skill_dir(
        self,
        skill_dir: Path,
        *,
        owner: str = "user",
        read_only: bool = False,
        strict: bool = False,
    ) -> Skill | None:
        """加载单个 skill 目录"""
        skill_md = skill_dir / "SKILL.md"

        try:
            content = skill_md.read_text(encoding="utf-8")

            # 解析 YAML frontmatter
            frontmatter, body = self._parse_skill_md(content)

            # 验证必需字段
            if "name" not in frontmatter:
                logger.error(f"{skill_dir.name}: 缺少 name 字段")
                return None

            if "description" not in frontmatter:
                logger.error(f"{skill_dir.name}: 缺少 description 字段")
                return None

            # 验证 name 格式
            name = frontmatter["name"]
            if not self._validate_name(name):
                logger.error(f"{skill_dir.name}: name 格式不符合规范: {name}")
                return None

            # 验证 name 与目录名匹配
            if name != skill_dir.name:
                logger.warning(f"{skill_dir.name}: name '{name}' 与目录名不匹配")

            skill = self._build_skill_from_parsed(frontmatter, body, skill_dir=skill_dir)
            if skill:
                skill.metadata["resource_owner"] = owner
                skill.metadata["resource_read_only"] = read_only
                logger.debug(f"已加载 skill: {skill.id} ({skill.name})")
            return skill

        except Exception as e:
            if strict:
                raise ValueError(
                    f"解析 {skill_dir.name}/SKILL.md 失败: {e}"
                ) from e
            logger.error(f"解析 {skill_dir.name}/SKILL.md 失败: {e}")
            return None

    @staticmethod
    def _build_skill_from_parsed(
        frontmatter: dict,
        body: str,
        *,
        skill_dir: Path | None = None,
    ) -> Skill | None:
        """从已解析的 frontmatter + body 构建 Skill 对象。

        统一的 Skill 对象构建入口，保证 _load_skill_dir 和
        manager.from_raw_content 产出结构一致的 Skill。

        Args:
            frontmatter: YAML frontmatter 字典
            body: Markdown body 文本
            skill_dir: 可选的 skill 目录路径。提供时会填充
                       skill_dir / has_scripts / has_references / has_assets 元数据。

        Returns:
            Skill 对象，字段缺失时返回 None。
        """
        name = frontmatter.get("name", "")
        description = frontmatter.get("description", "")
        if not name or not description:
            logger.error("_build_skill_from_parsed: 缺少 name 或 description")
            return None

        metadata = frontmatter.get("metadata", {})

        # 推断分类
        category_str = metadata.get("category", "general")
        try:
            category = SkillCategory(category_str)
        except ValueError:
            category = SkillCategory.GENERAL

        # 提取 agent_types
        agent_types_raw = metadata.get("agent_types", [])
        if isinstance(agent_types_raw, list):
            agent_types = agent_types_raw
        elif isinstance(agent_types_raw, str):
            agent_types = agent_types_raw.split()
        else:
            agent_types = []

        # 提取 tags
        tags_raw = metadata.get("tags", "")
        if isinstance(tags_raw, list):
            tags = tags_raw
        elif isinstance(tags_raw, str):
            tags = tags_raw.split()
        else:
            tags = []

        # 构建目录相关元数据
        skill_meta: dict[str, Any] = {
            "license": frontmatter.get("license", ""),
            "compatibility": frontmatter.get("compatibility", ""),
            "allowed_tools": frontmatter.get("allowed-tools", ""),
        }
        if skill_dir is not None:
            skill_meta["skill_dir"] = str(skill_dir)
            skill_meta["has_scripts"] = (skill_dir / "scripts").exists()
            skill_meta["has_references"] = (skill_dir / "references").exists()
            skill_meta["has_assets"] = (skill_dir / "assets").exists()

        return Skill(
            id=name,
            name=metadata.get("display_name", name),
            description=description,
            content=body,
            category=category,
            agent_types=agent_types,
            workflow_only=metadata.get("workflow_only", False),
            priority=int(metadata.get("priority", 50)),
            tags=tags,
            enabled=True,
            version=metadata.get("version", "1.0.0"),
            author=metadata.get("author", ""),
            metadata=skill_meta,
        )

    @staticmethod
    def _parse_skill_md(content: str) -> tuple[dict, str]:
        """解析 SKILL.md 的 YAML frontmatter 和 body"""
        # 匹配 YAML frontmatter: ---\n...\n---
        pattern = r'^---\s*\n(.*?)\n---\s*\n(.*)$'
        match = re.match(pattern, content, re.DOTALL)

        if not match:
            raise ValueError("SKILL.md 格式错误：缺少 YAML frontmatter")

        yaml_str = match.group(1)
        body = match.group(2).strip()

        # 解析 YAML
        frontmatter = yaml.safe_load(yaml_str)
        if not isinstance(frontmatter, dict):
            raise ValueError("YAML frontmatter 必须是字典")

        return frontmatter, body

    def _validate_name(self, name: str) -> bool:
        """验证 name 字段格式"""
        # 1-64 字符，只能包含小写字母、数字、连字符
        # 不能以连字符开头或结尾，不能有连续连字符
        if not (1 <= len(name) <= 64):
            return False

        if not re.match(r'^[a-z0-9-]+$', name):
            return False

        if name.startswith('-') or name.endswith('-'):
            return False

        if '--' in name:
            return False

        return True

    def save_skill(self, skill: Skill) -> bool:
        """保存 skill 到目录。

        注意：此方法会就地更新 skill.metadata，填充以下键：
        skill_dir, has_scripts, has_references, has_assets。
        调用方依赖这些值来定位文件，因此就地修改是有意行为。
        """
        try:
            skill_dir = self.skills_dir / skill.id
            skill_dir.mkdir(parents=True, exist_ok=True)

            # 构建 YAML frontmatter
            frontmatter = {
                "name": skill.id,
                "description": skill.description,
            }

            # 添加可选字段（从 metadata 的副本读取，避免受后续写入影响）
            meta_snapshot = dict(skill.metadata)
            if meta_snapshot.get("license"):
                frontmatter["license"] = meta_snapshot["license"]
            if meta_snapshot.get("compatibility"):
                frontmatter["compatibility"] = meta_snapshot["compatibility"]
            if meta_snapshot.get("allowed_tools"):
                frontmatter["allowed-tools"] = meta_snapshot["allowed_tools"]

            # 添加 metadata
            category_value = skill.category.value if hasattr(skill.category, 'value') else skill.category
            fm_metadata = {
                "display_name": skill.name,
                "version": skill.version,
                "author": skill.author,
                "category": category_value,
                "priority": skill.priority,
                "workflow_only": skill.workflow_only,
            }
            if skill.agent_types:
                fm_metadata["agent_types"] = " ".join(skill.agent_types)
            if skill.tags:
                fm_metadata["tags"] = " ".join(skill.tags)
            frontmatter["metadata"] = fm_metadata

            # 构建 SKILL.md 内容
            yaml_str = yaml.dump(frontmatter, allow_unicode=True, sort_keys=False)
            skill_md_content = f"---\n{yaml_str}---\n\n{skill.content}"

            # 写入文件
            skill_md = skill_dir / "SKILL.md"
            skill_md.write_text(skill_md_content, encoding="utf-8")

            # 更新 skill 对象的元数据（目录路径等）
            # 这是有意的就地修改——调用方依赖这些值来定位文件
            skill.metadata["skill_dir"] = str(skill_dir)
            skill.metadata["has_scripts"] = (skill_dir / "scripts").exists()
            skill.metadata["has_references"] = (skill_dir / "references").exists()
            skill.metadata["has_assets"] = (skill_dir / "assets").exists()

            logger.info(f"已保存 skill: {skill.id} -> {skill_dir}")
            return True

        except Exception as e:
            logger.error(f"保存 skill 失败 {skill.id}: {e}")
            return False

    def delete_skill(self, skill_id: str) -> bool:
        """删除 skill 目录"""
        try:
            skill_dir = self.skills_dir / skill_id
            if skill_dir.exists():
                shutil.rmtree(skill_dir)
                logger.info(f"已删除 skill: {skill_id}")
                return True
            else:
                logger.warning(f"Skill 目录不存在: {skill_dir}")
                return False
        except Exception as e:
            logger.error(f"删除 skill 失败 {skill_id}: {e}")
            return False

    def write_supporting_file(self, skill_id: str, file_path: str, file_content: str) -> tuple[bool, str]:
        """写入技能目录中的捆绑资源文件。

        文件限制在 scripts/、references/、assets/ 子目录中，
        路径经过沙箱校验确保不穿越技能目录。

        Args:
            skill_id: 技能 ID
            file_path: 相对路径，如 "scripts/extract.py"
            file_content: 文件内容

        Returns:
            (success, message)
        """
        skill_dir = self.skills_dir / skill_id
        if not skill_dir.exists():
            return False, f"技能目录不存在: {skill_dir}"

        target = skill_dir / file_path

        # 路径沙箱校验：确保目标在技能目录内
        try:
            target_resolved = target.resolve()
            skill_dir_resolved = skill_dir.resolve()
            if not target_resolved.is_relative_to(skill_dir_resolved):
                return False, f"安全错误：文件路径超出技能目录范围: {file_path}"
        except Exception as e:
            return False, f"路径解析错误: {e}"

        # 创建父目录
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(file_content, encoding="utf-8")
            logger.info(f"已写入捆绑文件: {skill_id}/{file_path}")
            return True, f"文件 '{file_path}' 已写入技能 '{skill_id}'。"
        except Exception as e:
            logger.error(f"写入捆绑文件失败 {skill_id}/{file_path}: {e}")
            return False, f"写入文件失败: {e}"

    def remove_supporting_file(self, skill_id: str, file_path: str) -> tuple[bool, str]:
        """删除技能目录中的捆绑资源文件。

        Args:
            skill_id: 技能 ID
            file_path: 相对路径，如 "scripts/extract.py"

        Returns:
            (success, message)
        """
        skill_dir = self.skills_dir / skill_id
        if not skill_dir.exists():
            return False, f"技能目录不存在: {skill_dir}"

        target = skill_dir / file_path

        # 路径沙箱校验
        try:
            target_resolved = target.resolve()
            skill_dir_resolved = skill_dir.resolve()
            if not target_resolved.is_relative_to(skill_dir_resolved):
                return False, f"安全错误：文件路径超出技能目录范围: {file_path}"
        except Exception as e:
            return False, f"路径解析错误: {e}"

        if not target.exists():
            # 列出可用文件帮助 LLM 纠错
            available = []
            for subdir_name in ("scripts", "references", "assets"):
                subdir = skill_dir / subdir_name
                if subdir.exists():
                    for f in subdir.rglob("*"):
                        if f.is_file():
                            available.append(str(f.relative_to(skill_dir)))
            hint = ""
            if available:
                hint = f"\n当前可用文件: {', '.join(available)}"
            return False, f"文件 '{file_path}' 不存在。" + hint

        try:
            target.unlink()
            logger.info(f"已删除捆绑文件: {skill_id}/{file_path}")

            # 清理空目录
            parent = target.parent
            if parent != skill_dir and parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
                logger.debug(f"已清理空目录: {parent}")

            return True, f"文件 '{file_path}' 已从技能 '{skill_id}' 中删除。"
        except Exception as e:
            logger.error(f"删除捆绑文件失败 {skill_id}/{file_path}: {e}")
            return False, f"删除文件失败: {e}"

    def skill_exists(self, skill_id: str) -> bool:
        """检查技能目录是否存在"""
        skill_dir = self.skills_dir / skill_id
        return skill_dir.exists() and (skill_dir / "SKILL.md").exists()

    def create_example_skills(self):
        """创建示例 skills（符合 Agent Skills 标准）"""
        examples = [
            {
                "id": "python-best-practices",
                "name": "Python 最佳实践",
                "description": "Python 编码规范和最佳实践指南。当需要编写或审查 Python 代码时使用。",
                "content": """# Python 最佳实践

## 代码风格

- 遵循 PEP 8 规范
- 使用有意义的变量名
- 函数和类添加 docstring
- 使用类型注解提高可读性

## 常见模式

- 使用上下文管理器处理资源（with 语句）
- 优先使用列表推导式而非 map/filter
- 使用 pathlib 处理文件路径
- 异常处理要具体，避免裸 except

## 性能优化

- 使用生成器处理大数据集
- 避免在循环中重复计算
- 使用内置函数和标准库（通常更快）""",
                "category": "coding",
                "agent_types": ["main", "coder"],
                "priority": 70,
                "tags": ["python", "coding", "best-practices"],
            },
            {
                "id": "effective-research",
                "name": "高效研究方法",
                "description": "如何进行高效的信息研究和分析。当需要搜索、整理和分析信息时使用。",
                "content": """# 高效研究方法

## 研究流程

1. **明确目标**：清晰定义研究问题和预期产出
2. **信息收集**：使用 search_memory 查找相关历史信息
3. **结构化整理**：按主题分类，建立信息层次
4. **交叉验证**：对比多个来源，确认关键信息
5. **总结提炼**：提取核心观点，形成结论

## 记忆搜索技巧

- 使用关键词组合提高搜索精度
- 利用标签过滤特定领域信息
- 定期使用 list_all_memories 了解知识全貌

## 输出规范

- 结构清晰，层次分明
- 标注信息来源和可信度
- 突出关键发现和行动建议""",
                "category": "research",
                "agent_types": ["main", "researcher", "default"],
                "priority": 60,
                "tags": ["research", "methodology"],
            },
        ]

        for example in examples:
            skill = Skill(
                id=example["id"],
                name=example["name"],
                description=example["description"],
                content=example["content"],
                category=SkillCategory(example["category"]),
                agent_types=example["agent_types"],
                workflow_only=False,
                priority=example["priority"],
                tags=example["tags"],
                enabled=True,
                version="1.0.0",
                author="system",
            )
            self.save_skill(skill)

        logger.info(f"已创建 {len(examples)} 个示例 skills")
