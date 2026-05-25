from __future__ import annotations

import base64

import pytest

from nextgen_voice_agent.agent.controller import AgentController
from nextgen_voice_agent.models.task import StartTaskRequest, TaskStatus
from nextgen_voice_agent.models.voice import VoiceEvent, VoiceEventType, VoiceTransportKind
from nextgen_voice_agent.runtimes.fake import FakeRuntime
from nextgen_voice_agent.voice.orchestrator import VoiceSessionOrchestrator, VoiceTimingConfig
from nextgen_voice_agent.voice.stt import AsrMode, FakeSttProvider, parse_audio_frame


def pcm_ref() -> str:
    return base64.b64encode(b"\x00\x00\x01\x00").decode("ascii")


@pytest.mark.asyncio
async def test_pcm_audio_turn_routes_fake_transcript_into_task_flow() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        stt_provider=FakeSttProvider("Analyze the demo weather."),
        timing=VoiceTimingConfig(control_loop_ms=10, first_thinking_ack_ms=5000, first_tool_status_ms=5000),
    )
    session_id = "browser-session"
    segment_id = "segment-1"

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.SPEECH_STARTED,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            metadata={"speech_segment_id": segment_id},
        ),
    )
    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.USER_TURN_AUDIO,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            audio_ref=pcm_ref(),
            metadata={
                "speech_segment_id": segment_id,
                "sample_rate": 16000,
                "channels": 1,
                "encoding": "pcm_s16le",
                "sequence": 1,
            },
        ),
    )
    outbound = await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.SPEECH_ENDED,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            metadata={"speech_segment_id": segment_id},
        ),
    )

    assert any(event.event == VoiceEventType.TRANSCRIPT_FINAL and event.text == "Analyze the demo weather." for event in outbound)
    task_id = orchestrator.sessions[session_id].active_task_id
    assert task_id is not None
    assert controller.get_status(task_id).task.status in {TaskStatus.RUNNING, TaskStatus.COMPLETED}
    assert (await orchestrator.get_transcript(session_id))["last_transcript"] == "Analyze the demo weather."
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_streaming_asr_emits_partial_before_final_without_starting_task() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        stt_provider=FakeSttProvider("Look up the weather this week."),
        requested_asr_mode=AsrMode.SPEECH_GATED_STREAMING,
    )
    session_id = "browser-session"
    segment_id = "segment-1"

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.SPEECH_STARTED,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            metadata={"speech_segment_id": segment_id},
        ),
    )
    partial_events = await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.USER_TURN_AUDIO,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            audio_ref=pcm_ref(),
            metadata={"speech_segment_id": segment_id, "sequence": 1},
        ),
    )

    assert any(event.event == VoiceEventType.TRANSCRIPT_PARTIAL for event in partial_events)
    assert orchestrator.sessions[session_id].active_task_id is None

    final_events = await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.SPEECH_ENDED,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            metadata={"speech_segment_id": segment_id},
        ),
    )

    assert any(event.event == VoiceEventType.TRANSCRIPT_FINAL for event in final_events)
    assert orchestrator.sessions[session_id].active_task_id is not None
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_batch_asr_mode_preserves_buffer_then_transcribe_behavior() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=0.01))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        stt_provider=FakeSttProvider("Analyze this batch utterance."),
        requested_asr_mode=AsrMode.UTTERANCE_BATCH,
    )
    session_id = "browser-session"
    segment_id = "segment-1"

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.SPEECH_STARTED,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            metadata={"speech_segment_id": segment_id},
        ),
    )
    audio_events = await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.USER_TURN_AUDIO,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            audio_ref=pcm_ref(),
            metadata={"speech_segment_id": segment_id, "sequence": 1},
        ),
    )

    assert all(event.event != VoiceEventType.TRANSCRIPT_PARTIAL for event in audio_events)
    final_events = await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.SPEECH_ENDED,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            metadata={"speech_segment_id": segment_id},
        ),
    )

    assert any(event.event == VoiceEventType.TRANSCRIPT_FINAL for event in final_events)
    assert orchestrator.effective_asr_mode == AsrMode.UTTERANCE_BATCH
    await orchestrator.stop_session(session_id)


@pytest.mark.asyncio
async def test_partial_cancellation_stops_active_task_before_final() -> None:
    controller = AgentController(runtime=FakeRuntime(delay_seconds=1.0))
    start = await controller.start_task(StartTaskRequest(task="Long task."))
    orchestrator = VoiceSessionOrchestrator(
        controller=controller,
        stt_provider=FakeSttProvider("stop that"),
        requested_asr_mode=AsrMode.SPEECH_GATED_STREAMING,
    )
    session_id = "browser-session"
    segment_id = "segment-1"
    state = await orchestrator.start_session(session_id, VoiceTransportKind.BROWSER)
    state.active_task_id = start.task.task_id

    await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.SPEECH_STARTED,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            metadata={"speech_segment_id": segment_id},
        ),
    )
    events = await orchestrator.handle_voice_event(
        session_id,
        VoiceEvent(
            event=VoiceEventType.USER_TURN_AUDIO,
            transport=VoiceTransportKind.BROWSER,
            session_id=session_id,
            audio_ref=pcm_ref(),
            metadata={"speech_segment_id": segment_id, "sequence": 1},
        ),
    )

    assert any(event.event == VoiceEventType.TRANSCRIPT_PARTIAL for event in events)
    assert controller.get_status(start.task.task_id).task.status == TaskStatus.CANCELLED
    await orchestrator.stop_session(session_id)


def test_parse_audio_frame_accepts_base64_pcm_contract() -> None:
    frame = parse_audio_frame(
        pcm_ref(),
        {
            "speech_segment_id": "segment-1",
            "sample_rate": 16000,
            "channels": 1,
            "encoding": "pcm_s16le",
            "sequence": 7,
        },
    )

    assert frame.payload == b"\x00\x00\x01\x00"
    assert frame.sample_rate == 16000
    assert frame.channels == 1
    assert frame.encoding == "pcm_s16le"
    assert frame.sequence == 7
    assert frame.speech_segment_id == "segment-1"
