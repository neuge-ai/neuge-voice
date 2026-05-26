import pytest
from unittest.mock import AsyncMock, MagicMock, patch

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
            
            sys_state = kwargs.get("messages", [{}])[0].get("content", "")
            active_task_id = "test-task"
            if "Active Task ID: " in sys_state:
                import re
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
