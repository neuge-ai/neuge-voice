from __future__ import annotations

import asyncio
import json
from datetime import datetime
from datetime import timedelta

import pytest

from nextgen_voice_agent.agent.controller import AgentController
from nextgen_voice_agent.models.runtime import RuntimeResult, RuntimeResultStatus
from nextgen_voice_agent.models.task import TaskStatus
from nextgen_voice_agent.models.voice import VoiceEvent, VoiceEventType, VoiceTransportKind
from nextgen_voice_agent.runtimes.fake import FakeRuntime
from nextgen_voice_agent.voice.stt import SttTranscriptEvent, SttTranscriptEventType
from nextgen_voice_agent.voice.orchestrator import VoiceSessionOrchestrator, VoiceTimingConfig, VoiceTurnState


async def wait_for_voice_event(
    orchestrator: VoiceSessionOrchestrator,
    session_id: str,
    event_type: VoiceEventType,
) -> VoiceEvent:
    for _ in range(100):
        events = await orchestrator.drain_events(session_id)
        for event in events:
            if event.event == event_type:
                return event
        await asyncio.sleep(0.01)
    raise AssertionError(f"Voice event {event_type} was not emitted.")


@pytest.mark.asyncio
async def test_orchestrator_delivers_task_result_only_when_idle() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        timing=VoiceTimingConfig(control_loop_ms=10, first_thinking_ack_ms=5000, first_tool_status_ms=5000),
    )
    session_id = "browser-session"

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.USER_TURN,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            text="Check demo weather.",
        ),
    )
    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.SPEECH_STARTED, transport=VoiceTransportKind.BROWSER, session_id=session_id),
    )
    await asyncio.sleep(0.08)

    held_events = await orchestrator.drain_events(session_id)
    assert all(event.event != VoiceEventType.DELIVERY_READY for event in held_events)

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.SPEECH_ENDED, transport=VoiceTransportKind.BROWSER, session_id=session_id),
    )

    delivered = await wait_for_voice_event(orchestrator, session_id, VoiceEventType.DELIVERY_READY)

    assert delivered.text is not None
    assert delivered.text.startswith("I have the result now.")
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_routes_cancel_turn_to_controller() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=1.0))
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10, turn_commit_default_wait_ms=0, turn_commit_active_task_wait_ms=0, turn_commit_hard_command_wait_ms=0))
    session_id = "browser-session"

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.USER_TURN,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            text="Analyze the repo.",
        ),
    )
    task_id = orchestrator.sessions[session_id].active_task_id
    assert task_id is not None

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.USER_TURN,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            text="Cancel that.",
        ),
    )

    assert controller.get_status(task_id).task.status == TaskStatus.CANCELLED
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_speaks_router_acknowledgement_for_voice_task() -> None:
    class RouterWithAcknowledgement:
        async def route_turn(self, user_text, system_state, conversation_history):
            return {
                "type": "tool_call",
                "tool": "start_task",
                "arguments": {"task": user_text},
                "assistant_response": "I'll check that now.",
            }

    controller = AgentController(runtime=FakeRuntime(delay_seconds=1.0))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        llm_provider=RouterWithAcknowledgement(),
        timing=VoiceTimingConfig(control_loop_ms=10),
    )
    session_id = "browser-session"

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.USER_TURN,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            text="Check weather in Delhi tomorrow.",
        ),
    )
    events = await orchestrator.drain_events(session_id)

    assistant_events = [event for event in events if event.event == VoiceEventType.ASSISTANT_RESPONSE]
    assert assistant_events[-1].text == "I'll check that now."
    assert "Sure, I will work on" not in assistant_events[-1].text
    assert orchestrator.sessions[session_id].active_task_id is not None
    assert next(iter(controller.tasks.values())).original_request == "Check weather in Delhi tomorrow."
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_uses_controller_acknowledgement_when_router_tool_ack_is_generic() -> None:
    class GenericAckRouter:
        async def route_turn(self, user_text, system_state, conversation_history):
            return {
                "type": "tool_call",
                "tool": "start_task",
                "arguments": {"task": user_text},
                "assistant_response": "I'll start that now.",
            }

    controller = AgentController(runtime=FakeRuntime(delay_seconds=1.0))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        llm_provider=GenericAckRouter(),
        timing=VoiceTimingConfig(control_loop_ms=10),
    )
    session_id = "browser-session"

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.USER_TURN,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            text="Check weather in Calcutta.",
        ),
    )
    events = await orchestrator.drain_events(session_id)

    assistant_events = [event for event in events if event.event == VoiceEventType.ASSISTANT_RESPONSE]
    assert assistant_events[-1].text is not None
    assert assistant_events[-1].text == "I'll look into that in the background."
    assert "I'll start that now." not in assistant_events[-1].text
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_records_timestamped_history_and_structured_state() -> None:
    now = datetime(2026, 5, 26, 14, 32, 18)

    class DirectRouter:
        async def route_turn(self, user_text, system_state, conversation_history):
            assert "Structured Session State" in system_state
            assert "2026-05-26T14:32:18" in system_state
            return {"type": "assistant_response", "response": "Okay."}

    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        llm_provider=DirectRouter(),
        clock=lambda: now,
        timing=VoiceTimingConfig(control_loop_ms=10),
    )
    session_id = "browser-session"

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.USER_TURN, transport=VoiceTransportKind.BROWSER, session_id=session_id, text="hello"),
    )

    history = orchestrator.sessions[session_id].conversation_history
    assert history[0]["role"] == "user"
    assert history[0]["timestamp"].startswith("2026-05-26T14:32:18")
    assert history[1]["role"] == "assistant"
    assert history[1]["timestamp"].startswith("2026-05-26T14:32:18")
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_native_activity_payload_is_factual_only() -> None:
    now = datetime(2026, 5, 26, 14, 0, 0)

    def clock() -> datetime:
        return now

    class ActivityRouter:
        async def route_turn(self, user_text, system_state, conversation_history):
            return {
                "type": "tool_call",
                "tool": "start_activity",
                "arguments": {"activity_type": "run", "label": "5 km run", "target_distance_meters": 5000},
                "assistant_response": "I started tracking your 5 km run.",
            }

    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        llm_provider=ActivityRouter(),
        clock=clock,
        timing=VoiceTimingConfig(control_loop_ms=10),
    )
    session_id = "browser-session"

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.USER_TURN, transport=VoiceTransportKind.BROWSER, session_id=session_id, text="I'm going for a 5 km run."),
    )
    now = now + timedelta(seconds=15)
    state = orchestrator.sessions[session_id]
    payload = orchestrator._session_state_payload(state)

    activity = payload["active_activities"][0]
    assert activity["elapsed_ms"] == 15000
    assert activity["target_distance_meters"] == 5000
    assert "computed_plausibility" not in activity
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_uses_native_tool_message_when_router_tool_ack_is_generic() -> None:
    class GenericActivityAckRouter:
        async def route_turn(self, user_text, system_state, conversation_history):
            return {
                "type": "tool_call",
                "tool": "start_activity",
                "arguments": {"activity_type": "run", "label": "run"},
                "assistant_response": "I'll start that now.",
            }

        async def compose_native_tool_result(
            self,
            user_text,
            system_state,
            conversation_history,
            tool_name,
            tool_result,
            fallback,
        ):
            assert tool_name == "start_activity"
            assert "activity" in tool_result
            return "I started tracking your run."

    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        llm_provider=GenericActivityAckRouter(),
        timing=VoiceTimingConfig(control_loop_ms=10),
    )
    session_id = "browser-session"

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.USER_TURN, transport=VoiceTransportKind.BROWSER, session_id=session_id, text="I'm going for a run."),
    )
    events = await orchestrator.drain_events(session_id)

    assistant_events = [event for event in events if event.event == VoiceEventType.ASSISTANT_RESPONSE]
    assert assistant_events[-1].text == "I started tracking your run."
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_rejects_end_activity_when_target_duration_has_not_elapsed() -> None:
    now = datetime(2026, 5, 26, 14, 0, 0)

    def clock() -> datetime:
        return now

    class EarlyFinishRouter:
        def __init__(self) -> None:
            self.calls = 0

        async def route_turn(self, user_text, system_state, conversation_history):
            self.calls += 1
            if self.calls == 1:
                return {
                    "type": "tool_call",
                    "tool": "start_activity",
                    "arguments": {"activity_type": "run", "label": "5 minute run", "target_duration_ms": 300000},
                    "assistant_response": "I'll start that now.",
                }
            return {
                "type": "tool_call",
                "tool": "end_activity",
                "arguments": {},
                "assistant_response": "Your 5-minute run is complete. Great job!",
            }

        async def compose_native_tool_result(
            self,
            user_text,
            system_state,
            conversation_history,
            tool_name,
            tool_result,
            fallback,
        ):
            assert tool_name == "end_activity"
            assert tool_result["ok"] is False
            return "That was only about ten seconds, so the five-minute run is still active."

    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        llm_provider=EarlyFinishRouter(),
        clock=clock,
        timing=VoiceTimingConfig(control_loop_ms=10),
    )
    session_id = "browser-session"

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.USER_TURN, transport=VoiceTransportKind.BROWSER, session_id=session_id, text="I'm going for a five minute run."),
    )
    now = now + timedelta(seconds=10)
    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.USER_TURN, transport=VoiceTransportKind.BROWSER, session_id=session_id, text="I finished."),
    )

    events = await orchestrator.drain_events(session_id)
    assistant_events = [event for event in events if event.event == VoiceEventType.ASSISTANT_RESPONSE]
    assert assistant_events[-1].text == "That was only about ten seconds, so the five-minute run is still active."
    state = orchestrator.sessions[session_id]
    activity = next(iter(state.active_activities.values()))
    assert activity.status == "active"
    tool_messages = [message for message in state.conversation_history if message.get("role") == "tool" and message.get("name") == "end_activity"]
    rejection = json.loads(tool_messages[-1]["content"])
    assert rejection["ok"] is False
    assert rejection["error_code"] == "activity_completion_too_early"
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_rejects_end_activity_when_stored_distance_is_physically_impossible() -> None:
    now = datetime(2026, 5, 26, 14, 0, 0)

    def clock() -> datetime:
        return now

    class ImpossibleDistanceRouter:
        def __init__(self) -> None:
            self.calls = 0

        async def route_turn(self, user_text, system_state, conversation_history):
            self.calls += 1
            if self.calls == 1:
                return {
                    "type": "tool_call",
                    "tool": "start_activity",
                    "arguments": {"activity_type": "run", "label": "5 km run", "target_distance_meters": 5000},
                    "assistant_response": "I'll start that now.",
                }
            return {
                "type": "tool_call",
                "tool": "end_activity",
                "arguments": {},
                "assistant_response": "Your 5 km run is complete. Great job!",
            }

        async def compose_native_tool_result(
            self,
            user_text,
            system_state,
            conversation_history,
            tool_name,
            tool_result,
            fallback,
        ):
            assert tool_name == "end_activity"
            assert tool_result["ok"] is False
            return "That pace would be impossible, so I'm keeping the run active."

    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        llm_provider=ImpossibleDistanceRouter(),
        clock=clock,
        timing=VoiceTimingConfig(control_loop_ms=10),
    )
    session_id = "browser-session"

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.USER_TURN, transport=VoiceTransportKind.BROWSER, session_id=session_id, text="I'm going for a 5 km run."),
    )
    now = now + timedelta(seconds=15)
    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.USER_TURN, transport=VoiceTransportKind.BROWSER, session_id=session_id, text="I finished."),
    )

    events = await orchestrator.drain_events(session_id)
    assistant_events = [event for event in events if event.event == VoiceEventType.ASSISTANT_RESPONSE]
    assert assistant_events[-1].text == "That pace would be impossible, so I'm keeping the run active."
    state = orchestrator.sessions[session_id]
    activity = next(iter(state.active_activities.values()))
    assert activity.status == "active"
    tool_messages = [message for message in state.conversation_history if message.get("role") == "tool" and message.get("name") == "end_activity"]
    rejection = json.loads(tool_messages[-1]["content"])
    assert rejection["ok"] is False
    assert rejection["error_code"] == "activity_completion_physically_implausible"
    assert rejection["implied_speed_mps"] == 333.3
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_progress_policy_can_stay_silent() -> None:
    class SilentProgressRouter:
        async def route_turn(self, user_text, system_state, conversation_history):
            return {
                "type": "tool_call",
                "tool": "start_task",
                "arguments": {"task": user_text},
                "assistant_response": "I'll check that.",
            }

        async def decide_progress(self, progress_state, conversation_history):
            return {"action": "stay_silent", "message": None, "next_check_ms": 10000}

    controller = AgentController(runtime=FakeRuntime(delay_seconds=1.0))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        llm_provider=SilentProgressRouter(),
        timing=VoiceTimingConfig(control_loop_ms=10, first_progress_check_ms=20),
    )
    session_id = "browser-session"

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.USER_TURN, transport=VoiceTransportKind.BROWSER, session_id=session_id, text="Check demo weather."),
    )
    await asyncio.sleep(0.08)
    events = await orchestrator.drain_events(session_id)

    assert all(event.event != VoiceEventType.TASK_STATUS for event in events)
    assert orchestrator.sessions[session_id].progress_check_count >= 1
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_blocks_second_codex_task_with_graceful_response() -> None:
    class AlwaysStartTaskRouter:
        async def route_turn(self, user_text, system_state, conversation_history):
            return {
                "type": "tool_call",
                "tool": "start_task",
                "arguments": {"task": user_text},
                "assistant_response": "I'll start that now.",
            }

    controller = AgentController(runtime=FakeRuntime(delay_seconds=1.0))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        llm_provider=AlwaysStartTaskRouter(),
        timing=VoiceTimingConfig(control_loop_ms=10),
    )
    session_id = "browser-session"

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.USER_TURN, transport=VoiceTransportKind.BROWSER, session_id=session_id, text="Analyze the repo."),
    )
    first_task_id = orchestrator.sessions[session_id].active_task_id
    assert first_task_id is not None

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.USER_TURN, transport=VoiceTransportKind.BROWSER, session_id=session_id, text="Now research tomorrow's weather."),
    )
    events = await orchestrator.drain_events(session_id)

    assistant_events = [event for event in events if event.event == VoiceEventType.ASSISTANT_RESPONSE]
    assert assistant_events[-1].task_id == first_task_id
    assert "already have a background task running" in assistant_events[-1].text
    assert len(controller.tasks) == 1
    assert orchestrator.sessions[session_id].active_task_id == first_task_id
    tool_messages = [
        message for message in orchestrator.sessions[session_id].conversation_history
        if message.get("role") == "tool" and message.get("name") == "start_task"
    ]
    conflict = json.loads(tool_messages[-1]["content"])
    assert conflict["ok"] is False
    assert conflict["error_code"] == "codex_task_conflict"
    assert conflict["recoverable"] is True
    assert conflict["active_task"]["task_id"] == first_task_id
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_allows_native_timer_while_codex_task_runs() -> None:
    class StartThenTimerRouter:
        def __init__(self) -> None:
            self.calls = 0

        async def route_turn(self, user_text, system_state, conversation_history):
            self.calls += 1
            if self.calls == 1:
                return {
                    "type": "tool_call",
                    "tool": "start_task",
                    "arguments": {"task": user_text},
                    "assistant_response": "I'll start that.",
                }
            assert '"can_start_new_codex_task": false' in system_state
            return {
                "type": "tool_call",
                "tool": "start_timer",
                "arguments": {"duration_ms": 300000, "label": "five-minute timer"},
                "assistant_response": "I started the timer.",
            }

    controller = AgentController(runtime=FakeRuntime(delay_seconds=1.0))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        llm_provider=StartThenTimerRouter(),
        timing=VoiceTimingConfig(control_loop_ms=10),
    )
    session_id = "browser-session"

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.USER_TURN, transport=VoiceTransportKind.BROWSER, session_id=session_id, text="Analyze the repo."),
    )
    first_task_id = orchestrator.sessions[session_id].active_task_id
    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.USER_TURN, transport=VoiceTransportKind.BROWSER, session_id=session_id, text="Set a five minute timer."),
    )

    state = orchestrator.sessions[session_id]
    assert state.active_task_id == first_task_id
    assert len(state.active_timers) == 1
    assert len(controller.tasks) == 1
    timer_tool_messages = [
        message for message in state.conversation_history
        if message.get("role") == "tool" and message.get("name") == "start_timer"
    ]
    assert timer_tool_messages
    assert timer_tool_messages[-1].get("tool_call_id", "").startswith("call_start_timer_")
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_proactively_delivers_timer_expiry_when_idle() -> None:
    now = datetime(2026, 5, 26, 14, 0, 0)

    def clock() -> datetime:
        return now

    class TimerComposer:
        async def compose_runtime_event_result(
            self,
            system_state,
            conversation_history,
            pending_item,
            event_payload,
            fallback,
        ):
            assert pending_item["kind"] == "timer"
            assert event_payload["type"] == "timer"
            return "Your one minute timer is done."

    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        llm_provider=TimerComposer(),
        clock=clock,
        timing=VoiceTimingConfig(control_loop_ms=10),
    )
    session_id = "browser-session"
    state = await orchestrator.start_session(session_id, VoiceTransportKind.BROWSER)
    await orchestrator.drain_events(session_id)

    await orchestrator._execute_native_tool(state, "start_timer", {"duration_ms": 1000, "label": "one minute"})
    now = now + timedelta(milliseconds=1200)

    delivered = await wait_for_voice_event(orchestrator, session_id, VoiceEventType.ASSISTANT_RESPONSE)

    assert delivered.text == "Your one minute timer is done."
    assert delivered.metadata["durable"] is True
    assert delivered.metadata["delivery_kind"] == "timer"
    assert state.recent_completed_events[-1]["type"] == "timer"
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_waits_until_assistant_is_idle_to_deliver_timer_expiry() -> None:
    now = datetime(2026, 5, 26, 14, 0, 0)

    def clock() -> datetime:
        return now

    class TimerComposer:
        async def compose_runtime_event_result(
            self,
            system_state,
            conversation_history,
            pending_item,
            event_payload,
            fallback,
        ):
            return "Your run timer is done."

    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        llm_provider=TimerComposer(),
        clock=clock,
        timing=VoiceTimingConfig(control_loop_ms=10),
    )
    session_id = "browser-session"
    state = await orchestrator.start_session(session_id, VoiceTransportKind.BROWSER)
    await orchestrator.drain_events(session_id)

    await orchestrator._execute_native_tool(state, "start_timer", {"duration_ms": 1000, "label": "run"})
    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.ASSISTANT_SPEECH_STARTED, transport=VoiceTransportKind.BROWSER, session_id=session_id),
    )
    now = now + timedelta(milliseconds=1200)
    await asyncio.sleep(0.05)
    events = await orchestrator.drain_events(session_id)

    assert all(event.text != "Your run timer is done." for event in events)

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.ASSISTANT_SPEECH_ENDED, transport=VoiceTransportKind.BROWSER, session_id=session_id, metadata={"completed": True}),
    )
    delivered = await wait_for_voice_event(orchestrator, session_id, VoiceEventType.ASSISTANT_RESPONSE)

    assert delivered.text == "Your run timer is done."
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_delivers_timer_expiry_before_progress_update() -> None:
    now = datetime(2026, 5, 26, 14, 0, 0)

    def clock() -> datetime:
        return now

    class ProgressRouter:
        async def route_turn(self, user_text, system_state, conversation_history):
            return {
                "type": "tool_call",
                "tool": "start_task",
                "arguments": {"task": user_text},
                "assistant_response": "I'll check that now.",
            }

        async def decide_progress(self, progress_state, conversation_history):
            return {"action": "speak_progress", "message": "Still working on it.", "next_check_ms": 1000}

        async def compose_runtime_event_result(
            self,
            system_state,
            conversation_history,
            pending_item,
            event_payload,
            fallback,
        ):
            return "Your one minute timer is done."

    controller = AgentController(runtime=FakeRuntime(delay_seconds=1.0))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        llm_provider=ProgressRouter(),
        clock=clock,
        timing=VoiceTimingConfig(control_loop_ms=10, first_progress_check_ms=20),
    )
    session_id = "browser-session"

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.USER_TURN, transport=VoiceTransportKind.BROWSER, session_id=session_id, text="Check weather."),
    )
    state = orchestrator.sessions[session_id]
    await orchestrator.drain_events(session_id)
    await orchestrator._execute_native_tool(state, "start_timer", {"duration_ms": 1000, "label": "one minute"})
    now = now + timedelta(milliseconds=1200)
    await asyncio.sleep(0.08)
    events = await orchestrator.drain_events(session_id)
    relevant = [event for event in events if event.event in {VoiceEventType.ASSISTANT_RESPONSE, VoiceEventType.TASK_STATUS}]

    assert relevant
    assert relevant[0].text == "Your one minute timer is done."
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_delivers_supervisor_composed_tool_result() -> None:
    class ComposingRouter:
        async def route_turn(self, user_text, system_state, conversation_history):
            return {
                "type": "tool_call",
                "tool": "start_task",
                "arguments": {"task": user_text},
                "assistant_response": "I'll check that now.",
            }

        async def compose_tool_result(self, user_text, system_state, conversation_history, result):
            return f"Composed answer for: {result.spoken_answer}"

    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        llm_provider=ComposingRouter(),
        timing=VoiceTimingConfig(control_loop_ms=10, first_thinking_ack_ms=5000, first_tool_status_ms=5000),
    )
    session_id = "browser-session"

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.USER_TURN,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            text="Check demo weather.",
        ),
    )

    delivered = await wait_for_voice_event(orchestrator, session_id, VoiceEventType.DELIVERY_READY)

    assert delivered.text == "Composed answer for: Demo result for: Check demo weather."
    assert any(msg.get("role") == "tool" and msg.get("tool_call_id", "").startswith("result_") for msg in orchestrator.sessions[session_id].conversation_history)
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_keeps_interrupted_delivery_as_pending_unheard() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        timing=VoiceTimingConfig(control_loop_ms=10, first_progress_check_ms=5000),
    )
    session_id = "browser-session"

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.USER_TURN, transport=VoiceTransportKind.BROWSER, session_id=session_id, text="Check Bangalore weather."),
    )
    delivered = await wait_for_voice_event(orchestrator, session_id, VoiceEventType.DELIVERY_READY)
    delivery_id = delivered.metadata["delivery_id"]

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.ASSISTANT_SPEECH_STARTED,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            metadata={"delivery_id": delivery_id},
        ),
    )
    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.ASSISTANT_SPEECH_ENDED,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            metadata={"delivery_id": delivery_id, "completed": False, "reason": "interrupted"},
        ),
    )

    payload = orchestrator._session_state_payload(orchestrator.sessions[session_id])
    assert payload["pending_unheard_items"][0]["delivery_id"] == delivery_id
    assert payload["pending_unheard_items"][0]["status"] == "interrupted"
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_removes_completed_delivery_from_pending_unheard() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        timing=VoiceTimingConfig(control_loop_ms=10, first_progress_check_ms=5000),
    )
    session_id = "browser-session"

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.USER_TURN, transport=VoiceTransportKind.BROWSER, session_id=session_id, text="Check Bangalore weather."),
    )
    delivered = await wait_for_voice_event(orchestrator, session_id, VoiceEventType.DELIVERY_READY)
    delivery_id = delivered.metadata["delivery_id"]

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.ASSISTANT_SPEECH_ENDED,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            metadata={"delivery_id": delivery_id, "completed": True},
        ),
    )

    payload = orchestrator._session_state_payload(orchestrator.sessions[session_id])
    assert payload["pending_unheard_items"] == []
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_builds_grounded_context_with_prior_completed_results() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10))
    session_id = "browser-session"
    state = await orchestrator.start_session(session_id, VoiceTransportKind.BROWSER)
    state.conversation_history.append({"role": "user", "content": "Check the weather in Delhi today."})
    orchestrator._append_tool_result_history(
        state,
        RuntimeResult(
            task_id="task_delhi",
            generation=1,
            status=RuntimeResultStatus.COMPLETED,
            spoken_answer="Delhi today is around 42 C with overcast clouds.",
            technical_summary="weather lookup",
        ),
    )
    state.conversation_history.append({"role": "assistant", "content": "Delhi today is around 42 C with overcast clouds."})

    context = orchestrator._build_task_context(state, "Compare it against Calcutta.")

    assert "Prior completed tool results to preserve when relevant" in context
    assert "Delhi today is around 42 C with overcast clouds." in context
    assert "Current user request:\nCompare it against Calcutta." in context
    assert "Conversation transcript:" in context
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_rejects_recent_assistant_echo_as_asr_user_turn() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10, turn_commit_default_wait_ms=0, turn_commit_active_task_wait_ms=0, turn_commit_hard_command_wait_ms=0))
    session_id = "browser-session"
    state = await orchestrator.start_session(session_id, VoiceTransportKind.BROWSER)

    await orchestrator._emit(
        state,
        VoiceEventType.DELIVERY_READY,
        "I have the result now. Demo result for: What's the weather like in Calcutta today?",
    )
    await orchestrator._handle_transcript_event(
        state,
        SttTranscriptEvent(
            event=SttTranscriptEventType.FINAL,
            text="I have the result now demo result for what's the weather like in Calcutta today",
            segment_id="segment",
            provider="test",
        ),
    )

    assert state.active_task_id is None
    assert not controller.tasks
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_rejects_short_prefix_echo_after_assistant_speech() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10, turn_commit_default_wait_ms=0, turn_commit_active_task_wait_ms=0, turn_commit_hard_command_wait_ms=0))
    session_id = "browser-session"
    state = await orchestrator.start_session(session_id, VoiceTransportKind.BROWSER)

    await orchestrator._emit(state, VoiceEventType.ASSISTANT_RESPONSE, "Sure, I will work on: Should I.")
    await orchestrator._handle_transcript_event(
        state,
        SttTranscriptEvent(
            event=SttTranscriptEventType.FINAL,
            text="Sure.",
            segment_id="segment",
            provider="test",
        ),
    )

    assert state.active_task_id is None
    assert not controller.tasks
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_rejects_soft_marker_only_after_assistant_speech() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10, turn_commit_default_wait_ms=0, turn_commit_active_task_wait_ms=0, turn_commit_hard_command_wait_ms=0))
    session_id = "browser-session"
    state = await orchestrator.start_session(session_id, VoiceTransportKind.BROWSER)

    await orchestrator._emit(state, VoiceEventType.ASSISTANT_RESPONSE, "Actually, I found a better answer.")
    await orchestrator._handle_transcript_event(
        state,
        SttTranscriptEvent(
            event=SttTranscriptEventType.FINAL,
            text="actually",
            segment_id="segment",
            provider="test",
        ),
    )

    assert state.active_task_id is None
    assert not controller.tasks
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_accepts_soft_marker_with_payload_when_not_echo() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10, turn_commit_default_wait_ms=0, turn_commit_active_task_wait_ms=0, turn_commit_hard_command_wait_ms=0))
    session_id = "browser-session"
    state = await orchestrator.start_session(session_id, VoiceTransportKind.BROWSER)

    await orchestrator._emit(state, VoiceEventType.ASSISTANT_RESPONSE, "Sure, I will work on the weather.")
    await orchestrator._handle_transcript_event(
        state,
        SttTranscriptEvent(
            event=SttTranscriptEventType.FINAL,
            text="actually use Kolkata",
            segment_id="segment",
            provider="test",
        ),
    )

    assert state.active_task_id is not None
    assert controller.get_status(state.active_task_id).task.original_request == "actually use Kolkata"
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_allows_hard_interrupt_command_unless_echoed() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=1.0))
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10, turn_commit_default_wait_ms=0, turn_commit_active_task_wait_ms=0, turn_commit_hard_command_wait_ms=0))
    session_id = "browser-session"
    state = await orchestrator.start_session(session_id, VoiceTransportKind.BROWSER)

    await orchestrator._handle_user_turn(state, "Analyze the repo.")
    task_id = state.active_task_id
    assert task_id is not None

    await orchestrator._emit(state, VoiceEventType.ASSISTANT_RESPONSE, "I will keep working.")
    await orchestrator._handle_transcript_event(
        state,
        SttTranscriptEvent(
            event=SttTranscriptEventType.FINAL,
            text="cancel",
            segment_id="segment",
            provider="test",
        ),
    )

    assert controller.get_status(task_id).task.status == TaskStatus.CANCELLED
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_rejects_hard_command_when_it_matches_recent_assistant_text() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=1.0))
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10, turn_commit_default_wait_ms=0, turn_commit_active_task_wait_ms=0, turn_commit_hard_command_wait_ms=0))
    session_id = "browser-session"
    state = await orchestrator.start_session(session_id, VoiceTransportKind.BROWSER)

    await orchestrator._handle_user_turn(state, "Analyze the repo.")
    task_id = state.active_task_id
    assert task_id is not None

    await orchestrator._emit(state, VoiceEventType.ASSISTANT_RESPONSE, "Cancel.")
    await orchestrator._handle_transcript_event(
        state,
        SttTranscriptEvent(
            event=SttTranscriptEventType.FINAL,
            text="cancel",
            segment_id="segment",
            provider="test",
        ),
    )

    assert controller.get_status(task_id).task.status != TaskStatus.CANCELLED
    assert state.active_task_id == task_id
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_rejects_short_echo_during_post_tts_cooldown() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0)
    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        timing=VoiceTimingConfig(control_loop_ms=10, post_tts_cooldown_ms=500),
        clock=lambda: now,
    )
    session_id = "browser-session"
    state = await orchestrator.start_session(session_id, VoiceTransportKind.BROWSER)

    await orchestrator._emit(state, VoiceEventType.ASSISTANT_RESPONSE, "Sure, I will work on the weather.")
    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.ASSISTANT_SPEECH_ENDED, transport=VoiceTransportKind.BROWSER, session_id=session_id),
    )
    assert state.voice_state == VoiceTurnState.POST_TTS_COOLDOWN

    await orchestrator._handle_transcript_event(
        state,
        SttTranscriptEvent(
            event=SttTranscriptEventType.FINAL,
            text="Sure.",
            segment_id="segment",
            provider="test",
        ),
    )

    transcript_events = [
        event for event in await orchestrator.drain_events(session_id) if event.event == VoiceEventType.TRANSCRIPT_FINAL
    ]
    assert transcript_events[-1].metadata["candidate_status"] == "rejected"
    assert transcript_events[-1].metadata["candidate_reason"] == "short_prefix_echo"
    assert state.active_task_id is None
    assert not controller.tasks
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_speech_started_during_assistant_speech_enters_barge_in_candidate_without_stopping_audio() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10, turn_commit_default_wait_ms=0, turn_commit_active_task_wait_ms=0, turn_commit_hard_command_wait_ms=0))
    session_id = "browser-session"
    state = await orchestrator.start_session(session_id, VoiceTransportKind.BROWSER)

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.ASSISTANT_SPEECH_STARTED, transport=VoiceTransportKind.BROWSER, session_id=session_id),
    )
    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.SPEECH_STARTED,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            metadata={"speech_segment_id": "barge"},
        ),
    )
    events = await orchestrator.drain_events(session_id)

    assert state.voice_state == VoiceTurnState.BARGE_IN_CANDIDATE
    assert state.assistant_speaking is True
    assert all(event.event != VoiceEventType.STOP_ASSISTANT_AUDIO for event in events)
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_accepted_barge_in_stops_assistant_audio_and_processes_turn() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10, turn_commit_default_wait_ms=0, turn_commit_active_task_wait_ms=0, turn_commit_hard_command_wait_ms=0))
    session_id = "browser-session"
    state = await orchestrator.start_session(session_id, VoiceTransportKind.BROWSER)

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.ASSISTANT_SPEECH_STARTED, transport=VoiceTransportKind.BROWSER, session_id=session_id),
    )
    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.SPEECH_STARTED,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            metadata={"speech_segment_id": "barge"},
        ),
    )
    await orchestrator._handle_transcript_event(
        state,
        SttTranscriptEvent(
            event=SttTranscriptEventType.FINAL,
            text="actually use Kolkata",
            segment_id="barge",
            provider="test",
        ),
    )

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.SPEECH_ENDED,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            metadata={"speech_segment_id": "barge"},
        ),
    )
    import asyncio
    for _ in range(50):
        if state.active_task_id is not None:
            break
        await asyncio.sleep(0.01)

    events = await orchestrator.drain_events(session_id)
    assert any(event.event == VoiceEventType.STOP_ASSISTANT_AUDIO for event in events)
    assert state.assistant_speaking is False
    assert state.active_task_id is not None
    assert controller.get_status(state.active_task_id).task.original_request == "actually use Kolkata"
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_rejected_barge_in_does_not_start_task_or_stop_assistant_audio() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10, turn_commit_default_wait_ms=0, turn_commit_active_task_wait_ms=0, turn_commit_hard_command_wait_ms=0))
    session_id = "browser-session"
    state = await orchestrator.start_session(session_id, VoiceTransportKind.BROWSER)

    await orchestrator._emit(state, VoiceEventType.ASSISTANT_RESPONSE, "Sure, I will work on the weather.")
    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.SPEECH_STARTED,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            metadata={"speech_segment_id": "barge"},
        ),
    )
    await orchestrator._handle_transcript_event(
        state,
        SttTranscriptEvent(
            event=SttTranscriptEventType.FINAL,
            text="Sure.",
            segment_id="barge",
            provider="test",
        ),
    )

    events = await orchestrator.drain_events(session_id)
    assert all(event.event != VoiceEventType.STOP_ASSISTANT_AUDIO for event in events)
    assert state.active_task_id is None
    assert not controller.tasks
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_orchestrator_blocks_delivery_during_assistant_ack_timeout() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.005))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        timing=VoiceTimingConfig(control_loop_ms=10, assistant_ack_timeout_ms=500, turn_commit_default_wait_ms=0, turn_commit_active_task_wait_ms=0, turn_commit_hard_command_wait_ms=0),
    )
    session_id = "browser-session"
    state = await orchestrator.start_session(session_id, VoiceTransportKind.BROWSER)

    # 1. Trigger user turn
    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.USER_TURN,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            text="Trigger fast task.",
        ),
    )
    init_events = await orchestrator.drain_events(session_id)

    # Allow task to finish, control loop will poll and see the queued result.
    # However, since client hasn't started speaking the acknowledgement (no ASSISTANT_SPEECH_STARTED),
    # and we are within 500ms timeout, the result delivery must be blocked.
    await asyncio.sleep(0.05)
    
    # The initial event should have the assistant response (acknowledgement)
    assert any(e.event == VoiceEventType.ASSISTANT_RESPONSE for e in init_events)

    events = await orchestrator.drain_events(session_id)
    # The subsequent queue should NOT have the delivery ready event.
    assert all(e.event != VoiceEventType.DELIVERY_READY for e in events)

    # 2. Simulate client starting the acknowledgement speech
    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.ASSISTANT_SPEECH_STARTED,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
        ),
    )
    
    # 3. Simulate client ending the acknowledgement speech
    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.ASSISTANT_SPEECH_ENDED,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
        ),
    )

    # Now the control loop should eagerly deliver the result
    delivered = await wait_for_voice_event(orchestrator, session_id, VoiceEventType.DELIVERY_READY)
    assert delivered is not None
    await orchestrator.stop_session(session_id)
