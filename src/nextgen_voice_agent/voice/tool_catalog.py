"""Canonical voice and Realtime tool names and schemas."""

from __future__ import annotations

from nextgen_voice_agent.models.realtime import RealtimeTool

CODEX_ROUTER_TOOL_NAMES: frozenset[str] = frozenset({"start_task", "amend_task", "cancel_task"})

REALTIME_CODEX_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "start_codex_task",
        "amend_codex_task",
        "cancel_codex_task",
        "get_codex_task_status",
        "read_codex_result",
        "approve_codex_action",
    }
)

NATIVE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "start_timer",
        "get_timer_status",
        "list_active_timers",
        "cancel_timer",
        "start_activity",
        "get_activity_status",
        "list_active_activities",
        "end_activity",
        "cancel_activity",
        "list_active_background_tasks",
        "get_background_task_status",
        "cancel_background_task",
    }
)

ROUTER_TOOL_NAMES: frozenset[str] = CODEX_ROUTER_TOOL_NAMES | NATIVE_TOOL_NAMES

UI_TITLE_PROPERTY: dict[str, object] = {
    "type": "string",
    "description": "A concise 2-3 word title summarizing what this specific tool execution is doing, for display on the UI.",
}


def realtime_native_tool_names() -> frozenset[str]:
    return NATIVE_TOOL_NAMES


def _router_tool(name: str, description: str, parameters: dict[str, object]) -> dict[str, object]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}


def _realtime_tool(name: str, description: str, parameters: dict[str, object]) -> RealtimeTool:
    return RealtimeTool(name=name, description=description, parameters=parameters)


def _rt_object(
    properties: dict[str, object],
    required: list[str],
    *,
    additional_properties: bool = False,
) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": additional_properties,
        "properties": properties,
        "required": required,
    }


def build_router_tools() -> list[dict[str, object]]:
    return [
        _router_tool(
            "start_task",
            (
                "Start a new powerful background task to write code, search the web, or run commands. "
                "Use this when the user's request requires heavy lifting. For follow-up requests, inspect "
                "the conversation history and include relevant prior facts directly in the task argument; "
                "ask the background task only for missing/new information and comparison work."
            ),
            {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "The goal for the background task to accomplish."}
                },
                "required": ["task"],
            },
        ),
        _router_tool(
            "amend_task",
            "Amend or add instructions to an already running background task.",
            {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The ID of the active task."},
                    "amendment": {"type": "string", "description": "The new instructions or feedback to add to the task."},
                },
                "required": ["task_id", "amendment"],
            },
        ),
        _router_tool(
            "cancel_task",
            "Cancel a running task.",
            {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The ID of the active task to cancel."}
                },
                "required": ["task_id"],
            },
        ),
        _router_tool(
            "start_timer",
            "Start a deterministic timer. Use for low-latency duration tracking; do not use Codex for timers.",
            {
                "type": "object",
                "properties": {
                    "duration_ms": {"type": "integer"},
                    "label": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["duration_ms", "label"],
            },
        ),
        _router_tool(
            "get_timer_status",
            "Read elapsed and remaining time for a timer.",
            {"type": "object", "properties": {"timer_id": {"type": "string"}}},
        ),
        _router_tool("list_active_timers", "List active timers.", {"type": "object", "properties": {}}),
        _router_tool(
            "cancel_timer",
            "Cancel an active timer.",
            {"type": "object", "properties": {"timer_id": {"type": "string"}}},
        ),
        _router_tool(
            "start_activity",
            "Start tracking a user activity such as a run. Store factual state like type, label, start time, and any explicit targets.",
            {
                "type": "object",
                "properties": {
                    "activity_type": {"type": "string"},
                    "label": {"type": "string"},
                    "target_duration_ms": {"type": "integer"},
                    "target_distance_meters": {"type": "integer"},
                },
                "required": ["activity_type", "label"],
            },
        ),
        _router_tool(
            "get_activity_status",
            "Read factual status for an active activity, including elapsed time and any stored targets.",
            {"type": "object", "properties": {"activity_id": {"type": "string"}}},
        ),
        _router_tool(
            "list_active_activities",
            "List active activities and their factual state.",
            {"type": "object", "properties": {}},
        ),
        _router_tool(
            "end_activity",
            "End an active activity after reasoning over the authoritative state and latest user claim.",
            {"type": "object", "properties": {"activity_id": {"type": "string"}}},
        ),
        _router_tool(
            "cancel_activity",
            "Cancel activity tracking.",
            {"type": "object", "properties": {"activity_id": {"type": "string"}}},
        ),
        _router_tool(
            "list_active_background_tasks",
            "List active background Codex tasks.",
            {"type": "object", "properties": {}},
        ),
        _router_tool(
            "get_background_task_status",
            "Read status for a background Codex task.",
            {"type": "object", "properties": {"task_id": {"type": "string"}}},
        ),
        _router_tool(
            "cancel_background_task",
            "Cancel a background Codex task.",
            {"type": "object", "properties": {"task_id": {"type": "string"}, "reason": {"type": "string"}}},
        ),
    ]


def build_realtime_tools() -> list[RealtimeTool]:
    return [
        _realtime_tool(
            "start_codex_task",
            "Start a background Codex task for nontrivial work.",
            _rt_object(
                {
                    "task": {"type": "string"},
                    "context": {"type": ["string", "null"]},
                    "ui_title": UI_TITLE_PROPERTY,
                },
                ["task", "context", "ui_title"],
            ),
        ),
        _realtime_tool(
            "amend_codex_task",
            (
                "Steer the active running Codex task with a related follow-up, correction, or added constraint. "
                "Send only the new instruction, not a full rewritten task."
            ),
            _rt_object(
                {"task_id": {"type": "string"}, "amendment": {"type": "string"}},
                ["task_id", "amendment"],
            ),
        ),
        _realtime_tool(
            "cancel_codex_task",
            "Cancel a running Codex task.",
            _rt_object(
                {"task_id": {"type": "string"}, "reason": {"type": "string"}},
                ["task_id", "reason"],
            ),
        ),
        _realtime_tool(
            "get_codex_task_status",
            "Read user-visible progress for a Codex task.",
            _rt_object({"task_id": {"type": "string"}}, ["task_id"]),
        ),
        _realtime_tool(
            "read_codex_result",
            "Read the final structured result for a Codex task.",
            _rt_object({"task_id": {"type": "string"}}, ["task_id"]),
        ),
        _realtime_tool(
            "approve_codex_action",
            "Record user approval or denial for a Codex action.",
            _rt_object(
                {
                    "task_id": {"type": "string"},
                    "action_id": {"type": "string"},
                    "approved": {"type": "boolean"},
                },
                ["task_id", "action_id", "approved"],
            ),
        ),
        _realtime_tool(
            "start_timer",
            "Start a deterministic timer.",
            _rt_object(
                {
                    "duration_ms": {"type": "integer"},
                    "label": {"type": "string"},
                    "reason": {"type": ["string", "null"]},
                    "ui_title": UI_TITLE_PROPERTY,
                },
                ["duration_ms", "label", "reason", "ui_title"],
            ),
        ),
        _realtime_tool(
            "get_timer_status",
            "Read elapsed and remaining time for a timer.",
            _rt_object({"timer_id": {"type": ["string", "null"]}}, ["timer_id"]),
        ),
        _realtime_tool(
            "list_active_timers",
            "List active timers.",
            _rt_object({}, [], additional_properties=False),
        ),
        _realtime_tool(
            "cancel_timer",
            "Cancel a timer.",
            _rt_object({"timer_id": {"type": ["string", "null"]}}, ["timer_id"]),
        ),
        _realtime_tool(
            "start_activity",
            "Start tracking an activity with optional duration or distance target.",
            _rt_object(
                {
                    "activity_type": {"type": "string"},
                    "label": {"type": "string"},
                    "target_duration_ms": {"type": ["integer", "null"]},
                    "target_distance_meters": {"type": ["integer", "null"]},
                    "ui_title": UI_TITLE_PROPERTY,
                },
                ["activity_type", "label", "target_duration_ms", "target_distance_meters", "ui_title"],
            ),
        ),
        _realtime_tool(
            "get_activity_status",
            "Read factual status for an activity.",
            _rt_object({"activity_id": {"type": ["string", "null"]}}, ["activity_id"]),
        ),
        _realtime_tool(
            "list_active_activities",
            "List active activities.",
            _rt_object({}, [], additional_properties=False),
        ),
        _realtime_tool(
            "end_activity",
            "End an active activity after reasoning over state and the latest user claim.",
            _rt_object({"activity_id": {"type": ["string", "null"]}}, ["activity_id"]),
        ),
        _realtime_tool(
            "cancel_activity",
            "Cancel activity tracking.",
            _rt_object({"activity_id": {"type": ["string", "null"]}}, ["activity_id"]),
        ),
        _realtime_tool(
            "list_active_background_tasks",
            "List active background Codex tasks.",
            _rt_object({}, [], additional_properties=False),
        ),
        _realtime_tool(
            "get_background_task_status",
            "Read status for a background Codex task.",
            _rt_object({"task_id": {"type": ["string", "null"]}}, ["task_id"]),
        ),
        _realtime_tool(
            "cancel_background_task",
            "Cancel a background Codex task.",
            _rt_object(
                {"task_id": {"type": ["string", "null"]}, "reason": {"type": ["string", "null"]}},
                ["task_id", "reason"],
            ),
        ),
    ]


ROUTER_TOOLS: list[dict[str, object]] = build_router_tools()
