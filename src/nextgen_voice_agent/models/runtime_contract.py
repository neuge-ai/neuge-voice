from __future__ import annotations

import json
from typing import Any

from nextgen_voice_agent.models.runtime import ApprovalAction, RuntimeResult, RuntimeResultStatus

RUNTIME_RESULT_TOP_LEVEL_FIELDS: tuple[str, ...] = tuple(RuntimeResult.model_fields.keys())


def _strict_object_schema(node: dict[str, Any]) -> dict[str, Any]:
    if node.get("type") == "object":
        node = dict(node)
        node["additionalProperties"] = False
        properties = node.get("properties")
        if isinstance(properties, dict):
            node["properties"] = {
                key: _strict_object_schema(value) if isinstance(value, dict) else value
                for key, value in properties.items()
            }
    return node


def _strict_defs(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.get("$defs")
    if not isinstance(defs, dict):
        return schema
    schema = dict(schema)
    schema["$defs"] = {
        name: _strict_object_schema(defn) if isinstance(defn, dict) else defn
        for name, defn in defs.items()
    }
    return schema


def runtime_result_output_schema() -> dict[str, object]:
    """JSON Schema for Codex turn output, generated from RuntimeResult."""
    schema = RuntimeResult.model_json_schema()
    schema = _strict_defs(schema)
    schema = _strict_object_schema(schema)
    schema["required"] = list(RUNTIME_RESULT_TOP_LEVEL_FIELDS)

    defs = schema.get("$defs")
    if isinstance(defs, dict) and "ApprovalAction" in defs:
        approval = dict(defs["ApprovalAction"])
        approval = _strict_object_schema(approval)
        approval["required"] = list(ApprovalAction.model_fields.keys())
        metadata = approval.get("properties", {}).get("metadata")
        if isinstance(metadata, dict):
            metadata = dict(metadata)
            metadata["additionalProperties"] = False
            metadata["properties"] = {}
            approval.setdefault("properties", {})["metadata"] = metadata
        defs = dict(defs)
        defs["ApprovalAction"] = approval
        schema["$defs"] = defs

    status_prop = schema.get("properties", {}).get("status")
    if isinstance(status_prop, dict) and "$ref" in status_prop:
        schema = dict(schema)
        properties = dict(schema["properties"])
        properties["status"] = {
            "type": "string",
            "enum": [status.value for status in RuntimeResultStatus],
        }
        schema["properties"] = properties

    return schema


def runtime_result_status_values() -> list[str]:
    return [status.value for status in RuntimeResultStatus]


def runtime_result_prompt_contract(task_id: str, generation: int) -> str:
    statuses = " | ".join(runtime_result_status_values())
    example = {
        "task_id": task_id,
        "generation": generation,
        "status": RuntimeResultStatus.COMPLETED.value,
        "spoken_answer": "Concise factual answer for the supervisor to phrase.",
        "technical_summary": "...",
        "sources_or_tools_used": [],
        "actions_requiring_approval": [],
        "followup_question": None,
        "error": None,
    }
    example_json = json.dumps(example, indent=2)
    field_list = ", ".join(f"`{name}`" for name in RUNTIME_RESULT_TOP_LEVEL_FIELDS)
    return (
        "Return structured JSON as the final assistant message. "
        "The JSON must match the runtime result schema with these fields: "
        f"{field_list}.\n"
        f"Valid `status` values: {statuses}.\n"
        f"Example shape:\n{example_json}"
    )
