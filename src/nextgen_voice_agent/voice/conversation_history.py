from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable

from nextgen_voice_agent.agent.controller import AgentController
from nextgen_voice_agent.config import get_settings
from nextgen_voice_agent.models.runtime import RuntimeResult

if TYPE_CHECKING:
    from nextgen_voice_agent.voice.orchestrator import VoiceSessionState


class ConversationHistoryManager:
    """Append and serialize conversation history for LLM routing."""

    def __init__(
        self,
        *,
        controller: AgentController,
        clock: Callable[[], Any],
        session_state_payload: Callable[[VoiceSessionState], dict[str, Any]],
    ) -> None:
        self._controller = controller
        self._clock = clock
        self._session_state_payload = session_state_payload

    def iso_now(self) -> str:
        return self._clock().astimezone().isoformat()

    def append_history(
        self,
        state: VoiceSessionState,
        role: str,
        content: str | None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        message = dict(extra or {})
        message["role"] = role
        if content is not None:
            message["content"] = content
        message["timestamp"] = self.iso_now()
        state.conversation_history.append(message)
        limit = get_settings().conversation_history_limit
        if len(state.conversation_history) > limit:
            state.conversation_history = state.conversation_history[-limit:]

    def append_tool_result_history(self, state: VoiceSessionState, result: RuntimeResult) -> None:
        tool_call_id = f"result_{result.task_id}"
        if any(
            msg.get("role") == "tool" and msg.get("tool_call_id") == tool_call_id
            for msg in state.conversation_history
        ):
            return
        self.append_history(
            state,
            "tool",
            json.dumps(result.model_dump(mode="json")),
            extra={
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": "start_task",
            },
        )

    def build_system_state(self, state: VoiceSessionState) -> str:
        active_task_id = state.active_task_id or "None"
        return (
            f"Active Task ID: {active_task_id}\nStructured Session State:\n"
            f"{json.dumps(self._session_state_payload(state), default=str, indent=2)}"
        )

    def build_task_context(self, state: VoiceSessionState, current_user_text: str) -> str:
        prior_results: list[dict[str, Any]] = []
        transcript_lines: list[str] = []
        for msg in state.conversation_history:
            role = msg.get("role")
            content = msg.get("content")
            if role == "tool" and msg.get("name") == "start_task" and isinstance(content, str):
                try:
                    payload = json.loads(content)
                except json.JSONDecodeError:
                    transcript_lines.append(f"Tool: {content}")
                    continue
                if isinstance(payload, dict) and "status" in payload:
                    prior_results.append(payload)
                    continue
            if role == "assistant" and "tool_calls" in msg:
                transcript_lines.append(f"Assistant tool call: {json.dumps(msg.get('tool_calls'))}")
            elif content is not None:
                transcript_lines.append(f"{str(role or 'message').capitalize()}: {content}")

        sections = ["Current user request:", current_user_text]
        if prior_results:
            sections.append("\nPrior completed tool results to preserve when relevant:")
            for index, result in enumerate(prior_results, start=1):
                sections.append(f"Prior result {index}: {json.dumps(result)}")
        sections.append("\nConversation transcript:")
        sections.extend(transcript_lines or ["No previous conversation turns."])
        return "\n".join(sections)
