import json
import re

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _parse_tool_content(content: str) -> dict:
    if content.startswith("[timestamp:"):
        _, _, rest = content.partition("]\n")
        content = rest or content
    return json.loads(content)


def _latest_tool_messages(messages: list[dict]) -> list[dict]:
    return [message for message in reversed(messages) if message.get("role") == "tool"]


def _speak_from_history(messages: list[dict]) -> str:
    last_msg = messages[-1].get("content", "") if messages else ""

    if "Background research finished" in last_msg or "Background event ready" in last_msg:
        for message in _latest_tool_messages(messages):
            if not str(message.get("tool_call_id", "")).startswith("result_"):
                continue
            data = _parse_tool_content(message.get("content", ""))
            if data.get("status") == "failed":
                return "The background task failed."
            spoken = data.get("spoken_answer") or data.get("error") or "The task finished without a spoken answer."
            return f"I have the result now. {spoken}"

    if "Timer completed" in last_msg:
        sys_content = messages[0].get("content", "") if messages else ""
        timer_labels = re.findall(r'"label":\s*"([^"]+)"', sys_content)
        if timer_labels:
            return f"Your {timer_labels[-1]} timer is done."
        for message in _latest_tool_messages(messages):
            data = _parse_tool_content(message.get("content", ""))
            if data.get("timer"):
                label = data["timer"].get("label", "timer")
                return f"Your {label} timer is done."
        return "Your one minute timer is done."

    for message in _latest_tool_messages(messages):
        if str(message.get("tool_call_id", "")).startswith("result_"):
            continue
        try:
            data = _parse_tool_content(message.get("content", ""))
        except json.JSONDecodeError:
            continue
        if data.get("acknowledgement"):
            return str(data["acknowledgement"])
        if data.get("error_code") == "codex_task_conflict" or (
            data.get("ok") is False and data.get("active_task")
        ):
            return str(data.get("message") or "I already have a background task running.")
        if data.get("error_code") == "activity_completion_too_early":
            return "That was only about ten seconds, so the five-minute run is still active."
        if data.get("error_code") == "activity_completion_physically_implausible":
            return "That pace would be impossible, so I'm keeping the run active."
        if data.get("ok") is False:
            return str(data.get("message") or "That did not work.")
        if data.get("message"):
            return str(data["message"])
        if data.get("activity"):
            return "I started tracking your run."
        if data.get("timer"):
            label = data["timer"].get("label", "timer")
            return f"I started the {label} timer."

    return "I'll look into that in the background."


@pytest.fixture(autouse=True)
def mock_litellm_acompletion():
    with patch("nextgen_voice_agent.voice.llm_router.acompletion", new_callable=AsyncMock) as mock_acompletion:
        async def fake_acompletion(*args, **kwargs):
            messages = kwargs.get("messages", [])
            if not messages:
                raw_msg = ""
                last_msg = ""
            else:
                raw_msg = messages[-1].get("content", "")
                last_msg = raw_msg.lower()

            if not kwargs.get("tools"):
                choice_mock = MagicMock()
                choice_mock.message.tool_calls = []
                choice_mock.message.content = _speak_from_history(messages)
                response_mock = MagicMock()
                response_mock.choices = [choice_mock]
                return response_mock

            sys_state = kwargs.get("messages", [{}])[0].get("content", "")
            active_task_id = "test-task"
            if "Active Task ID: " in sys_state:
                match = re.search(r"Active Task ID: (\S+)", sys_state)
                if match and match.group(1) != "None":
                    active_task_id = match.group(1)
                else:
                    active_task_id = None

            tool_name = "start_task"
            arguments = f'{{"task": "{raw_msg}"}}'

            if active_task_id is not None and ("cancel" in last_msg or "stop" in last_msg or "forget that" in last_msg):
                tool_name = "cancel_task"
                arguments = f'{{"task_id": "{active_task_id}"}}'
            elif active_task_id is not None and ("actually" in last_msg or "amend" in last_msg or "no " in last_msg or "wrong" in last_msg):
                tool_name = "amend_task"
                arguments = f'{{"task_id": "{active_task_id}", "amendment": "something"}}'
            elif "status" in last_msg or "progress" in last_msg or "how is" in last_msg:
                choice_mock = MagicMock()
                choice_mock.message.tool_calls = []
                choice_mock.message.content = "Task is running."

                response_mock = MagicMock()
                response_mock.choices = [choice_mock]
                return response_mock

            choice_mock = MagicMock()
            tool_call_mock = MagicMock()
            tool_call_mock.function.name = tool_name
            tool_call_mock.function.arguments = arguments
            choice_mock.message.tool_calls = [tool_call_mock]
            choice_mock.message.content = None

            response_mock = MagicMock()
            response_mock.choices = [choice_mock]
            return response_mock

        mock_acompletion.side_effect = fake_acompletion
        yield mock_acompletion
