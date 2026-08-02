import json

from src.workflow.json_output import (
    detect_output_format,
    get_json_policy,
    get_json_retry_count,
    safe_repair_json_text,
    validate_and_format_json,
)


def test_detect_json_output_defaults_from_file_extension():
    assert detect_output_format("cache/character/skeleton.json", {}) == "json"
    assert get_json_policy("cache/character/skeleton.json", {}) == "safe_repair_then_retry"
    assert get_json_retry_count({}) == 1


def test_strip_markdown_fence_and_format_json():
    raw = '```json\n{"a": 1}\n```'
    result = validate_and_format_json(raw)

    assert result.success
    assert json.loads(result.formatted) == {"a": 1}
    assert "strip_markdown_fence" in result.repairs


def test_extract_json_body_from_prefixed_text():
    raw = '以下是结果：\n{"a": 1}\n请查收'
    result = validate_and_format_json(raw)

    assert result.success
    assert json.loads(result.formatted) == {"a": 1}
    assert "extract_json_body" in result.repairs


def test_valid_json_with_chinese_dialogue_quotes_is_not_repaired():
    raw = json.dumps(
        {
            "body": "驼铃声从浓雾中传来。\n\n“你那伤，再拖就废了。”小贩说，“用这个。”",
        },
        ensure_ascii=False,
        indent=2,
    )
    result = validate_and_format_json(raw, repair=True)

    assert result.success
    assert result.repairs == []
    body = json.loads(result.formatted)["body"]
    assert body.count("“") == 2
    assert body.count("”") == 2


def test_normalize_smart_quote_keys_and_remove_trailing_commas():
    raw = '{“a”: 1, “b”: [2,],}'
    result = validate_and_format_json(raw)

    assert result.success
    assert json.loads(result.formatted) == {"a": 1, "b": [2]}
    assert "normalize_quotes" in result.repairs
    assert "remove_trailing_commas" in result.repairs


def test_remove_obvious_orphan_line_without_semantic_merge():
    raw = '''{
  "world_position": {
    "origin_class": "流放者，原为灰烬平原某聚落的猎手",
    "affiliation": "无固定势力",
    "social_role": "独行流放者",
寻者",
    "stance_on_core_conflict": "中立但寻求真相"
  }
}'''
    result = validate_and_format_json(raw)

    assert result.success
    parsed = json.loads(result.formatted)
    assert parsed["world_position"]["social_role"] == "独行流放者"
    assert parsed["world_position"]["stance_on_core_conflict"] == "中立但寻求真相"
    assert "remove_orphan_lines" in result.repairs


def test_unrepairable_json_stays_failed():
    raw = '{"a": }'
    result = validate_and_format_json(raw)

    assert not result.success
    assert result.error


def test_safe_repair_does_not_remove_key_like_lines():
    raw = '{\n  "a": 1,\n  broken: value\n}'
    repaired, repairs = safe_repair_json_text(raw)

    assert "broken: value" in repaired
    assert "remove_orphan_lines" not in repairs
