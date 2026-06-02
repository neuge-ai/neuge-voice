from __future__ import annotations

import json

import pytest

from nextgen_voice_agent.agent.prompts import build_codex_task_prompt
from nextgen_voice_agent.models.runtime import RuntimeResult, RuntimeResultStatus
from nextgen_voice_agent.models.runtime_contract import (
    RUNTIME_RESULT_TOP_LEVEL_FIELDS,
    runtime_result_output_schema,
    runtime_result_status_values,
)
from nextgen_voice_agent.models.task import StartTaskRequest, Task, utc_now
from nextgen_voice_agent.runtimes.codex_runtime import CodexAppServerRuntime


def test_generated_schema_contains_all_status_values() -> None:
    schema = runtime_result_output_schema()
    status = schema["properties"]["status"]
    assert status["enum"] == runtime_result_status_values()
    for value in RuntimeResultStatus:
        assert value.value in status["enum"]


def test_generated_schema_requires_runtime_result_fields() -> None:
    schema = runtime_result_output_schema()
    assert set(schema["required"]) == set(RUNTIME_RESULT_TOP_LEVEL_FIELDS)


def test_generated_schema_rejects_unknown_top_level_property() -> None:
    schema = runtime_result_output_schema()
    assert schema.get("additionalProperties") is False

    try:
        import jsonschema
    except ImportError:
        invalid = {
            "task_id": "t1",
            "generation": 1,
            "status": "completed",
            "spoken_answer": "",
            "technical_summary": "",
            "sources_or_tools_used": [],
            "actions_requiring_approval": [],
            "followup_question": None,
            "error": None,
            "unexpected_field": True,
        }
        with pytest.raises(Exception):
            RuntimeResult.model_validate(invalid)
        return

    invalid = {
        "task_id": "t1",
        "generation": 1,
        "status": "completed",
        "spoken_answer": "",
        "technical_summary": "",
        "sources_or_tools_used": [],
        "actions_requiring_approval": [],
        "followup_question": None,
        "error": None,
        "unexpected_field": True,
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid, schema)


def test_build_codex_task_prompt_includes_contract_fragments() -> None:
    task = Task(
        task_id="task_prompt",
        generation=3,
        original_request="Check weather",
        created_at=utc_now(),
        latest_user_constraints=[],
    )
    request = StartTaskRequest(task="Check weather", context="none")
    prompt = build_codex_task_prompt(task, request)

    assert "task_prompt" in prompt
    assert '"generation": 3' in prompt
    for status in RuntimeResultStatus:
        assert status.value in prompt
    for field in RUNTIME_RESULT_TOP_LEVEL_FIELDS:
        assert field in prompt
    assert '"completed | needs_clarification | needs_approval | failed"' not in prompt


def test_parse_runtime_result_json_accepts_valid_payload() -> None:
    runtime = CodexAppServerRuntime(client=object())  # type: ignore[arg-type]
    payload = RuntimeResult(
        task_id="task_1",
        generation=1,
        status=RuntimeResultStatus.COMPLETED,
        spoken_answer="Done.",
    )
    parsed = runtime._parse_runtime_result_json(payload.model_dump_json())
    assert parsed == payload


def test_parse_runtime_result_json_rejects_malformed_json() -> None:
    runtime = CodexAppServerRuntime(client=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        runtime._parse_runtime_result_json("not json")


def test_parse_runtime_result_json_rejects_invalid_status() -> None:
    runtime = CodexAppServerRuntime(client=object())  # type: ignore[arg-type]
    invalid = json.dumps(
        {
            "task_id": "task_1",
            "generation": 1,
            "status": "not_a_status",
            "spoken_answer": "",
            "technical_summary": "",
            "sources_or_tools_used": [],
            "actions_requiring_approval": [],
            "followup_question": None,
            "error": None,
        }
    )
    with pytest.raises(ValueError):
        runtime._parse_runtime_result_json(invalid)


def test_codex_runtime_default_schema_is_generated() -> None:
    runtime = CodexAppServerRuntime(client=object())  # type: ignore[arg-type]
    schema = runtime._load_output_schema()
    assert schema == runtime_result_output_schema()
