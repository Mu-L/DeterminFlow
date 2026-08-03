import json
import re
from pathlib import Path

import yaml

from src.core.default_resources import DEFAULT_RESOURCES_DIR, provision_core_skills
from src.skills.loader import SkillLoader
from src.skills.validation import security_scan, validate_frontmatter


REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_SKILLS_DIR = DEFAULT_RESOURCES_DIR / "skills"
CORE_SKILL_IDS = {
    "agent-definition-guide",
    "automation-guide",
    "prompt-template-guide",
    "script-library-guide",
    "skill-rule-authoring-guide",
    "workflow-guide",
}
RETIRED_SKILL_IDS = {
    "agent-config-guide",
    "coder-workflow",
    "effective-research",
    "explore-oss-project",
    "hello-world-demo",
    "python-best-practices",
    "rule-creator",
    "script-library-manager",
    "skill-creator",
    "storage-decision-rules",
}


def _frontmatter(skill_file: Path) -> dict:
    content = skill_file.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    assert match is not None, f"missing frontmatter: {skill_file}"
    parsed = yaml.safe_load(match.group(1))
    assert isinstance(parsed, dict)
    return parsed


def test_versioned_core_skill_catalog_is_exact_release_whitelist() -> None:
    actual = {
        path.name
        for path in CORE_SKILLS_DIR.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    }

    assert actual == CORE_SKILL_IDS


def test_versioned_core_skills_are_valid_focused_and_safe() -> None:
    for skill_id in sorted(CORE_SKILL_IDS):
        skill_file = CORE_SKILLS_DIR / skill_id / "SKILL.md"
        content = skill_file.read_text(encoding="utf-8")
        frontmatter = _frontmatter(skill_file)

        assert frontmatter["name"] == skill_id
        assert "必须" in frontmatter["description"]
        assert len(content.splitlines()) < 500
        assert validate_frontmatter(content) == (True, "")
        assert security_scan(content) == (True, "")


def test_fresh_runtime_provision_loads_exact_core_catalog(tmp_path: Path) -> None:
    runtime_skills = tmp_path / "skills"

    provision_core_skills(runtime_skills)
    loaded = SkillLoader(runtime_skills).load_all()

    assert {skill.id for skill in loaded} == CORE_SKILL_IDS


def test_skills_config_matches_versioned_core_catalog() -> None:
    config = json.loads(
        (REPO_ROOT / "config" / "skills_config.json").read_text(encoding="utf-8")
    )

    assert set(config["skill_configs"]) == CORE_SKILL_IDS
    assert set(config["skills"]) == CORE_SKILL_IDS
    for skill_id in CORE_SKILL_IDS:
        assert config["skill_configs"][skill_id] == {
            "enabled": True,
            "priority": 50,
            "auto_inject": True,
            "workflow_only": False,
        }
        assert config["skills"][skill_id] == {
            "group_ids": ["default"],
            "auto_inject": True,
        }


def test_prompt_config_references_only_current_authoring_skill() -> None:
    prompt_text = (REPO_ROOT / "config" / "prompts_config.json").read_text(
        encoding="utf-8"
    )

    assert "skill-rule-authoring-guide" in prompt_text
    for retired_skill_id in RETIRED_SKILL_IDS:
        assert retired_skill_id not in prompt_text
