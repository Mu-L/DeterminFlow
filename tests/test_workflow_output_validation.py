from __future__ import annotations

from src.workflow.definition import WorkflowDef, WorkflowNode
from src.workflow.output_validation import validate_agent_output


def test_non_empty_output_validation_rejects_whitespace() -> None:
    result = validate_agent_output(" \n\t", require_non_empty=True)

    assert result.success is False
    assert "最终输出为空" in result.error


def test_json_field_validation_supports_nested_array_paths() -> None:
    result = validate_agent_output(
        '```json\n{"chapters":[{"body":"正文内容"}]}\n```',
        json_field="chapters.0.body",
        json_field_min_chars=3,
    )

    assert result.success is True


def test_json_field_length_must_be_strictly_greater_than_threshold() -> None:
    result = validate_agent_output(
        '{"body":"一二三"}',
        json_field="body",
        json_field_min_chars=3,
    )

    assert result.success is False
    assert "字数为 3" in result.error
    assert "必须大于 3" in result.error


def test_json_field_validation_rejects_missing_and_non_string_values() -> None:
    missing = validate_agent_output(
        '{"title":"章节"}',
        json_field="body",
        json_field_min_chars=1,
    )
    non_string = validate_agent_output(
        '{"body":["正文"]}',
        json_field="body",
        json_field_min_chars=1,
    )

    assert missing.success is False
    assert "缺少字段 'body'" in missing.error
    assert non_string.success is False
    assert "必须是字符串" in non_string.error


def test_agent_output_validation_definition_round_trip_and_validation() -> None:
    node = WorkflowNode.from_dict({
        "id": "writer",
        "node_type": "agent",
        "require_non_empty_output": True,
        "json_output_field": "result.body",
        "json_output_field_min_chars": "1000",
    })

    assert node.require_non_empty_output is True
    assert node.json_output_field_min_chars == 1000
    assert node.to_dict()["json_output_field"] == "result.body"

    invalid = WorkflowDef(
        workflow_id="invalid-output-gate",
        nodes=[WorkflowNode(
            id="writer",
            node_type="agent",
            json_output_field="body",
            json_output_field_min_chars=0,
        )],
    ).validate()
    assert any("字段路径和最小字数必须同时配置" in error for error in invalid)
