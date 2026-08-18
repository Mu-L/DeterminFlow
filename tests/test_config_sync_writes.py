import json

from src.rules.config_manager import RuleConfigManager
from src.skills.config_manager import SkillConfigManager


def _write_json(path, document):
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_skill_directory_sync_does_not_rewrite_unchanged_config(tmp_path):
    config_file = tmp_path / "skills_config.json"
    _write_json(
        config_file,
        {
            "version": "1.0",
            "last_updated": "2026-01-01T00:00:00+00:00",
            "skills": {"workflow-guide": {}},
            "skill_configs": {
                "workflow-guide": {
                    "enabled": True,
                    "priority": 50,
                    "auto_inject": False,
                    "workflow_only": False,
                }
            },
            "groups": [
                {
                    "id": "default",
                    "name": "默认技能组",
                    "description": "包含所有现有技能的默认组",
                }
            ],
        },
    )
    before = config_file.read_bytes()

    manager = SkillConfigManager(config_file)

    assert manager.sync_with_directory(["workflow-guide"]) is True
    assert config_file.read_bytes() == before


def test_rule_directory_sync_does_not_rewrite_unchanged_config(tmp_path):
    config_file = tmp_path / "rules_config.json"
    _write_json(
        config_file,
        {
            "version": "1.0",
            "last_updated": "2026-01-01T00:00:00+00:00",
            "rules": {
                "safe-default": {
                    "agent_types": [],
                    "group_ids": ["default"],
                }
            },
            "rule_configs": {
                "safe-default": {
                    "enabled": True,
                    "workflow_only": False,
                }
            },
            "groups": [
                {
                    "id": "default",
                    "name": "默认规则组",
                    "description": "包含所有现有规则的默认组",
                }
            ],
        },
    )
    before = config_file.read_bytes()

    manager = RuleConfigManager(config_file)

    assert manager.sync_with_directory(["safe-default"]) is True
    assert config_file.read_bytes() == before


def test_directory_sync_still_persists_real_changes(tmp_path):
    skill_config = tmp_path / "skills_config.json"
    rule_config = tmp_path / "rules_config.json"
    _write_json(
        skill_config,
        {
            "version": "1.0",
            "last_updated": "2026-01-01T00:00:00+00:00",
            "skills": {},
            "skill_configs": {},
            "groups": [],
        },
    )
    _write_json(
        rule_config,
        {
            "version": "1.0",
            "last_updated": "2026-01-01T00:00:00+00:00",
            "rules": {},
            "rule_configs": {},
            "groups": [],
        },
    )

    assert SkillConfigManager(skill_config).sync_with_directory(["new-skill"])
    assert RuleConfigManager(rule_config).sync_with_directory(["new-rule"])

    saved_skills = json.loads(skill_config.read_text(encoding="utf-8"))
    saved_rules = json.loads(rule_config.read_text(encoding="utf-8"))
    assert "new-skill" in saved_skills["skills"]
    assert "new-rule" in saved_rules["rules"]
    assert saved_skills["last_updated"] != "2026-01-01T00:00:00+00:00"
    assert saved_rules["last_updated"] != "2026-01-01T00:00:00+00:00"
