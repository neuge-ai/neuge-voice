from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from nextgen_voice_agent.agent.controller import AgentController
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
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10))
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
async def test_orchestrator_rejects_recent_assistant_echo_as_asr_user_turn() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10))
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
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10))
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
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10))
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
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10))
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
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10))
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
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10))
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
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10))
    session_id = "browser-session"
    state = await orchestrator.start_session(session_id, VoiceTransportKind.BROWSER)

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(event=VoiceEventType.ASSISTANT_SPEECH_STARTED, transport=VoiceTransportKind.BROWSER, session_id=session_id),
    )
    events = await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.SPEECH_STARTED,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            metadata={"speech_segment_id": "barge"},
        ),
    )

    assert state.voice_state == VoiceTurnState.BARGE_IN_CANDIDATE
    assert state.assistant_speaking is True
    assert all(event.event != VoiceEventType.STOP_ASSISTANT_AUDIO for event in events)
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_accepted_barge_in_stops_assistant_audio_and_processes_turn() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10))
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

    events = await orchestrator.drain_events(session_id)
    assert any(event.event == VoiceEventType.STOP_ASSISTANT_AUDIO for event in events)
    assert state.assistant_speaking is False
    assert state.active_task_id is not None
    assert controller.get_status(state.active_task_id).task.original_request == "actually use Kolkata"
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_rejected_barge_in_does_not_start_task_or_stop_assistant_audio() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(controller=controller, timing=VoiceTimingConfig(control_loop_ms=10))
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
