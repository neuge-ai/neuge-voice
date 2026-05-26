from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nextgen_voice_agent.models.runtime import RuntimeResult, RuntimeResultStatus
from nextgen_voice_agent.voice.llm_router import OrchestratorLLMProvider, ROUTER_TOOLS, sanitize_user_facing_text


def _completion_message(content: str | None = None, tool_name: str | None = None, arguments: str = "{}") -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    if tool_name is None:
        choice.message.tool_calls = []
    else:
        tool_call = MagicMock()
        tool_call.function.name = tool_name
        tool_call.function.arguments = arguments
        choice.message.tool_calls = [tool_call]

    response = MagicMock()
    response.choices = [choice]
    return response


def test_router_tools_exclude_answer_directly() -> None:
    tool_names = {tool["function"]["name"] for tool in ROUTER_TOOLS}

    assert tool_names == {"start_task", "amend_task", "cancel_task"}


def test_start_task_tool_description_requires_grounded_followups() -> None:
    start_task_tool = next(tool for tool in ROUTER_TOOLS if tool["function"]["name"] == "start_task")

    description = start_task_tool["function"]["description"]
    assert "follow-up requests" in description
    assert "prior facts" in description
    assert "missing/new information" in description


def test_sanitize_user_facing_text_removes_thinking_blocks() -> None:
    assert sanitize_user_facing_text("<think>reasoning</think> Delhi is hot.") == "Delhi is hot."
    assert sanitize_user_facing_text("Delhi is hot. <think>extra</think>") == "Delhi is hot."
    assert sanitize_user_facing_text("<think>one</think> Delhi <think>two</think> is hot.") == "Delhi  is hot."


def test_sanitize_user_facing_text_unwraps_fenced_answers() -> None:
    assert sanitize_user_facing_text("```text\nDelhi is very hot today.\n```") == "Delhi is very hot today."


def test_sanitize_user_facing_text_uses_fallback_for_unclosed_thinking() -> None:
    assert sanitize_user_facing_text("<think>unfinished reasoning", "Fallback answer.") == "Fallback answer."


@pytest.mark.asyncio
async def test_router_returns_assistant_content_without_tool(mock_litellm_acompletion) -> None:
    mock_litellm_acompletion.side_effect = None
    mock_litellm_acompletion.return_value = _completion_message(content="Hello.")
    provider = OrchestratorLLMProvider(model="test-model")

    decision = await provider.route_turn("hello", "Active Task ID: None", [])

    assert decision == {"type": "assistant_response", "response": "Hello."}
    assert mock_litellm_acompletion.call_args.kwargs["tool_choice"] == "auto"
    system_prompt = mock_litellm_acompletion.call_args.kwargs["messages"][0]["content"]
    assert "inspect the conversation history" in system_prompt
    assert "preserve it and ask the tool only for missing/new information" in system_prompt


@pytest.mark.asyncio
async def test_router_returns_allowed_tool_call(mock_litellm_acompletion) -> None:
    mock_litellm_acompletion.side_effect = None
    mock_litellm_acompletion.return_value = _completion_message(
        content="I'll check that now.",
        tool_name="start_task",
        arguments='{"task": "Check the weather."}',
    )
    provider = OrchestratorLLMProvider(model="test-model")

    decision = await provider.route_turn("check the weather", "Active Task ID: None", [])

    assert decision == {
        "type": "tool_call",
        "tool": "start_task",
        "arguments": {"task": "Check the weather."},
        "assistant_response": "I'll check that now.",
    }


@pytest.mark.asyncio
async def test_router_falls_back_when_tool_call_has_no_assistant_content(mock_litellm_acompletion) -> None:
    mock_litellm_acompletion.side_effect = None
    mock_litellm_acompletion.return_value = _completion_message(
        tool_name="start_task",
        arguments='{"task": "Check the weather."}',
    )
    provider = OrchestratorLLMProvider(model="test-model")

    decision = await provider.route_turn("check the weather", "Active Task ID: None", [])

    assert decision == {
        "type": "tool_call",
        "tool": "start_task",
        "arguments": {"task": "Check the weather."},
        "assistant_response": "I'll start that now.",
    }


@pytest.mark.asyncio
async def test_router_rejects_unknown_tool_call(mock_litellm_acompletion) -> None:
    mock_litellm_acompletion.side_effect = None
    mock_litellm_acompletion.return_value = _completion_message(
        tool_name="brave_search",
        arguments='{"query": "weather"}',
    )
    provider = OrchestratorLLMProvider(model="test-model")

    decision = await provider.route_turn("check the weather", "Active Task ID: None", [])

    assert decision["type"] == "assistant_response"
    assert "cannot use that tool" in decision["response"]


@pytest.mark.asyncio
async def test_router_composes_tool_result_for_final_voice_answer(mock_litellm_acompletion) -> None:
    mock_litellm_acompletion.side_effect = None
    mock_litellm_acompletion.return_value = _completion_message(
        content="<think>Need to summarize the facts.</think> Delhi is very hot today."
    )
    provider = OrchestratorLLMProvider(model="test-model")
    result = RuntimeResult(
        task_id="task_1",
        generation=1,
        status=RuntimeResultStatus.COMPLETED,
        spoken_answer="raw facts: Delhi 43 C",
        technical_summary="weather lookup",
    )

    answer = await provider.compose_tool_result("Check Delhi weather.", "Completed Task ID: task_1", [], result)

    assert answer == "Delhi is very hot today."
    compose_messages = mock_litellm_acompletion.call_args.kwargs["messages"]
    assert all(message["role"] != "tool" for message in compose_messages)
    assert any("Background tool result JSON" in message["content"] for message in compose_messages if message["role"] == "user")
