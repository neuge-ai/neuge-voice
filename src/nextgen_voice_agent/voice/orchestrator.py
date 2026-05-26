from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Any, Callable

from nextgen_voice_agent.agent.controller import AgentController, TaskConflictError
from nextgen_voice_agent.config import get_settings
from nextgen_voice_agent.models.runtime import RuntimeResult, RuntimeResultStatus
from nextgen_voice_agent.models.task import AmendTaskRequest, CancelTaskRequest, StartTaskRequest, TaskStatus
from nextgen_voice_agent.models.voice import VoiceEvent, VoiceEventType, VoiceTransportKind
from nextgen_voice_agent.voice.stt import (
    AsrMode,
    SttProvider,
    SttStream,
    SttTranscriptEvent,
    SttTranscriptEventType,
    UtteranceBuffer,
    parse_audio_frame,
)

HARD_INTERRUPT_COMMANDS = {"stop", "cancel"}
SOFT_AMENDMENT_MARKERS = {"actually", "no", "wrong", "also", "instead", "but"}
RECENT_ASSISTANT_UTTERANCE_LIMIT = 6
NATIVE_TOOL_NAMES = {
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

GENERIC_TOOL_ACKS = {
    "",
    "I'll start that now.",
    "I'll look into that.",
    "I will start that now.",
    "Done.",
}


class VoiceTurnState(StrEnum):
    LISTENING = "listening"
    USER_SPEAKING = "user_speaking"
    ASSISTANT_SPEAKING = "assistant_speaking"
    BARGE_IN_CANDIDATE = "barge_in_candidate"
    POST_TTS_COOLDOWN = "post_tts_cooldown"


@dataclass(frozen=True)
class VoiceTimingConfig:
    control_loop_ms: int = 200
    first_thinking_ack_ms: int = 1200
    first_tool_status_ms: int = 3500
    repeated_status_interval_ms: int = 12000
    first_progress_check_ms: int = 2500
    second_progress_check_ms: int = 7000
    repeated_progress_check_min_ms: int = 15000
    repeated_progress_check_max_ms: int = 30000
    long_task_checkpoint_ms: int = 30000
    min_interrupt_speech_ms: int = 250
    post_tts_cooldown_ms: int = 500
    barge_in_min_speech_ms: int = 350
    recent_assistant_window_ms: int = 5000
    token_overlap_threshold: float = 0.75
    fuzzy_similarity_threshold: float = 0.80
    short_transcript_max_words: int = 3
    assistant_ack_timeout_ms: int = 0
    turn_commit_default_wait_ms: int = 700
    turn_commit_active_task_wait_ms: int = 1300
    turn_commit_hard_command_wait_ms: int = 0
    turn_commit_max_pending_wait_ms: int = 2500


@dataclass
class SessionTimer:
    timer_id: str
    label: str
    started_at: datetime
    duration_ms: int
    reason: str | None = None
    status: str = "running"

    @property
    def ends_at(self) -> datetime:
        return self.started_at + timedelta(milliseconds=self.duration_ms)


@dataclass
class SessionActivity:
    activity_id: str
    activity_type: str
    label: str
    started_at: datetime
    target_duration_ms: int | None = None
    target_distance_meters: int | None = None
    status: str = "active"


@dataclass
class DeliveryRecord:
    delivery_id: str
    kind: str
    task_id: str
    generation: int
    text: str
    original_request: str
    emitted_at: datetime
    status: str = "emitted"
    reason: str | None = None
    spoken_at: datetime | None = None


@dataclass(frozen=True)
class AsrTurnCandidate:
    text: str
    segment_id: str
    started_at: datetime | None
    ended_at: datetime
    during_assistant_speech: bool
    during_cooldown: bool
    status: str = "rejected"
    reason: str = "unknown"


@dataclass
class VoiceSessionState:
    session_id: str
    transport: VoiceTransportKind
    voice_state: VoiceTurnState = VoiceTurnState.LISTENING
    user_speaking: bool = False
    assistant_speaking: bool = False
    active_task_id: str | None = None
    queued_result: RuntimeResult | None = None
    delivered_result_generation: int | None = None
    last_user_turn_at: datetime | None = None
    user_speech_started_at: datetime | None = None
    last_assistant_speech_at: datetime | None = None
    assistant_speech_started_at: datetime | None = None
    assistant_speech_ended_at: datetime | None = None
    candidate_speech_started_at: datetime | None = None
    candidate_speech_ended_at: datetime | None = None
    post_tts_cooldown_expires_at: datetime | None = None
    last_progress_spoken_at: datetime | None = None
    next_progress_check_at: datetime | None = None
    progress_check_count: int = 0
    progress_update_count: int = 0
    last_progress_message: str | None = None
    last_task_status_seen: str | None = None
    task_started_at: datetime | None = None
    first_thinking_ack_sent: bool = False
    first_tool_status_sent: bool = False
    stopped: bool = False
    active_utterance: UtteranceBuffer | None = None
    active_asr_stream: SttStream | None = None
    active_asr_mode: AsrMode = AsrMode.UTTERANCE_BATCH
    last_transcript: str | None = None
    last_partial_transcript: str | None = None
    pending_transcript: str = ""
    pending_transcript_since: datetime | None = None
    conversation_history: list[dict[str, Any]] = field(default_factory=list)
    active_timers: dict[str, SessionTimer] = field(default_factory=dict)
    active_activities: dict[str, SessionActivity] = field(default_factory=dict)
    recent_completed_events: list[dict[str, Any]] = field(default_factory=list)
    recent_user_claims: list[dict[str, Any]] = field(default_factory=list)
    delivery_records: dict[str, DeliveryRecord] = field(default_factory=dict)
    recent_assistant_utterances: list[tuple[datetime, str]] = field(default_factory=list)
    outbound: asyncio.Queue[VoiceEvent] = field(default_factory=asyncio.Queue)
    loop_task: asyncio.Task[None] | None = None
    processed_segment_ids: set[str] = field(default_factory=set)


class VoiceSessionOrchestrator:
    def __init__(
        self,
        controller: AgentController,
        stt_provider: SttProvider | None = None,
        llm_provider: Any = None,
        requested_asr_mode: AsrMode = AsrMode.SPEECH_GATED_STREAMING,
        timing: VoiceTimingConfig | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.controller = controller
        self.stt_provider = stt_provider
        
        if llm_provider is None:
            from nextgen_voice_agent.voice.llm_router import OrchestratorLLMProvider
            self.llm_provider = OrchestratorLLMProvider(model=get_settings().router_model)
        else:
            self.llm_provider = llm_provider
        self.requested_asr_mode = requested_asr_mode
        self.effective_asr_mode = self._resolve_asr_mode()
        self.timing = timing or VoiceTimingConfig()
        self.clock = clock or datetime.now
        self.sessions: dict[str, VoiceSessionState] = {}
        self.event_listeners: set[Callable[[str, VoiceEvent], None]] = set()

    async def start_session(self, session_id: str, transport_kind: VoiceTransportKind) -> VoiceSessionState:
        existing = self.sessions.get(session_id)
        if existing and not existing.stopped:
            return existing

        state = VoiceSessionState(session_id=session_id, transport=transport_kind)
        self.sessions[session_id] = state
        state.loop_task = asyncio.create_task(self._control_loop(state))
        await self._emit(state, VoiceEventType.SESSION_STARTED, "Voice session started.")
        return state

    async def stop_session(self, session_id: str) -> None:
        state = self.sessions.get(session_id)
        if state is None:
            return
        state.stopped = True
        if state.loop_task:
            state.loop_task.cancel()
            try:
                await state.loop_task
            except asyncio.CancelledError:
                pass
        await self._emit(state, VoiceEventType.SESSION_STOPPED, "Voice session stopped.")

    async def handle_voice_event(self, session_id: str, event: VoiceEvent) -> None:
        state = await self.start_session(session_id, event.transport)

        if event.event == VoiceEventType.SPEECH_STARTED:
            now = self.clock()
            state.user_speaking = True
            state.user_speech_started_at = now
            state.candidate_speech_started_at = now
            state.candidate_speech_ended_at = None
            if state.assistant_speaking:
                state.voice_state = VoiceTurnState.BARGE_IN_CANDIDATE
            else:
                state.voice_state = VoiceTurnState.USER_SPEAKING
            segment_id = str(event.metadata.get("speech_segment_id") or f"{session_id}-{int(self.clock().timestamp() * 1000)}")
            state.active_utterance = UtteranceBuffer(segment_id)
            state.active_asr_mode = self.effective_asr_mode
            state.active_asr_stream = await self._start_asr_stream(state, segment_id)
            return

        if event.event == VoiceEventType.USER_TURN_AUDIO:
            await self._handle_audio_frame(state, event)
            return

        if event.event == VoiceEventType.SPEECH_ENDED:
            state.user_speaking = False
            state.candidate_speech_ended_at = self.clock()
            await self._finalize_utterance(state)
            if state.voice_state == VoiceTurnState.USER_SPEAKING:
                state.voice_state = VoiceTurnState.LISTENING
            elif state.voice_state == VoiceTurnState.BARGE_IN_CANDIDATE and state.assistant_speaking:
                state.voice_state = VoiceTurnState.ASSISTANT_SPEAKING
            return

        if event.event == VoiceEventType.INTERRUPTION:
            state.user_speaking = True
            if state.assistant_speaking:
                state.voice_state = VoiceTurnState.BARGE_IN_CANDIDATE
                if self._candidate_speech_duration_ms(state) >= self.timing.barge_in_min_speech_ms:
                    await self._stop_assistant_audio(state)
            return

        if event.event == VoiceEventType.ASSISTANT_SPEECH_STARTED:
            now = self.clock()
            state.assistant_speaking = True
            state.voice_state = VoiceTurnState.ASSISTANT_SPEAKING
            state.last_assistant_speech_at = now
            state.assistant_speech_started_at = now
            self._mark_delivery_speaking(state, event.metadata)
            state.post_tts_cooldown_expires_at = None
            return

        if event.event == VoiceEventType.ASSISTANT_SPEECH_ENDED:
            now = self.clock()
            state.assistant_speaking = False
            state.last_assistant_speech_at = now
            state.assistant_speech_ended_at = now
            self._mark_delivery_speech_ended(state, event.metadata)
            state.post_tts_cooldown_expires_at = now + timedelta(milliseconds=self.timing.post_tts_cooldown_ms)
            state.voice_state = VoiceTurnState.POST_TTS_COOLDOWN
            return

        if event.event in {VoiceEventType.IDLE_STATE, VoiceEventType.CLIENT_IDLE}:
            state.user_speaking = False
            state.assistant_speaking = False
            state.voice_state = VoiceTurnState.LISTENING
            state.post_tts_cooldown_expires_at = None
            return

        if event.event == VoiceEventType.USER_TURN and event.text:
            await self._handle_user_turn(state, event.text)
            return

        return

    async def get_transcript(self, session_id: str) -> dict[str, str | None]:
        state = self.sessions.get(session_id)
        if state is None:
            return {"session_id": session_id, "last_transcript": None, "last_partial_transcript": None}
        return {
            "session_id": session_id,
            "last_transcript": state.last_transcript,
            "last_partial_transcript": state.last_partial_transcript,
        }

    async def handle_model_event(self, session_id: str, event: VoiceEvent) -> None:
        state = await self.start_session(session_id, event.transport)
        if event.event == VoiceEventType.ASSISTANT_RESPONSE:
            now = self.clock()
            state.assistant_speaking = True
            state.voice_state = VoiceTurnState.ASSISTANT_SPEAKING
            state.last_assistant_speech_at = now
            state.assistant_speech_started_at = now
        return

    async def drain_events(self, session_id: str) -> list[VoiceEvent]:
        state = self.sessions[session_id]
        events: list[VoiceEvent] = []
        while not state.outbound.empty():
            events.append(state.outbound.get_nowait())
        return events

    async def _maybe_commit_turn(self, state: VoiceSessionState) -> None:
        if not state.pending_transcript or state.pending_transcript_since is None:
            return

        elapsed_ms = self._elapsed_ms(state.pending_transcript_since)
        if state.user_speaking:
            return

        delay = self._classify_commit_delay(state)
        if elapsed_ms >= delay:
            text = state.pending_transcript.strip()
            state.pending_transcript = ""
            state.pending_transcript_since = None
            if text:
                await self._handle_user_turn(state, text)

    def _classify_commit_delay(self, state: VoiceSessionState) -> int:
        normalized = _normalize_turn_text(state.pending_transcript)
        if not normalized:
            return self.timing.turn_commit_default_wait_ms
            
        if _is_hard_interrupt_command(normalized):
            return self.timing.turn_commit_hard_command_wait_ms
            
        if state.active_task_id:
            return self.timing.turn_commit_active_task_wait_ms
            
        return self.timing.turn_commit_default_wait_ms

    async def _handle_user_turn(self, state: VoiceSessionState, text: str) -> None:
        state.last_user_turn_at = self.clock()

        # A new user turn invalidates any result that was sitting in the delivery queue
        if state.queued_result is not None:
            self._append_tool_result_history(state, state.queued_result)
            state.queued_result = None
            state.active_task_id = None
        state.delivered_result_generation = None
        
        logging.info(f"User Turn Committed: {text}")
        self._append_history(state, "user", text)
        self._record_user_claim(state, text)
        system_state_str = self._build_system_state(state)
        
        decision = await self.llm_provider.route_turn(text, system_state_str, state.conversation_history[:-1])
        decision_type = decision.get("type")
        if decision_type == "assistant_response":
            response_text = decision.get("response") or "I understand."
            self._append_history(state, "assistant", response_text)
            await self._emit(state, VoiceEventType.ASSISTANT_RESPONSE, response_text, task_id=state.active_task_id)
            return

        if decision_type != "tool_call":
            await self._emit(state, VoiceEventType.ERROR, f"Unknown LLM decision type: {decision_type}")
            return

        tool_name = decision.get("tool")
        args = decision.get("arguments", {})
        
        if tool_name == "start_task":
            try:
                task_text = args.get("task", text)
                assistant_response = (decision.get("assistant_response") or "").strip() or "I'll start that now."
                if self._has_active_codex_task(state):
                    conflict = self._codex_task_conflict_payload(state, str(task_text))
                    self._append_history(state, "tool", json.dumps(conflict), extra={
                        "role": "tool",
                        "tool_call_id": f"call_conflict_{int(self.clock().timestamp() * 1000)}",
                        "name": "start_task",
                    })
                    response_text = await self._compose_control_answer(
                        state,
                        text,
                        "codex_task_conflict",
                        conflict,
                        str(conflict["message"]),
                    )
                    self._append_history(state, "assistant", response_text)
                    await self._emit(state, VoiceEventType.ASSISTANT_RESPONSE, response_text, task_id=state.active_task_id)
                    return
                context = self._build_task_context(state, text)
                logging.info("Voice router starting task with grounded request: %s", task_text)
                logging.debug("Voice task context for Codex:\n%s", context)
                response = await self.controller.start_task(StartTaskRequest(task=task_text, context=context))
                if assistant_response in GENERIC_TOOL_ACKS:
                    assistant_response = await self._compose_control_answer(
                        state,
                        text,
                        "start_task_acknowledgement",
                        response.model_dump(mode="json"),
                        response.acknowledgement,
                    )
                
                state.active_task_id = response.task.task_id
                state.task_started_at = self.clock()
                state.last_progress_spoken_at = None
                state.next_progress_check_at = state.task_started_at + timedelta(milliseconds=self.timing.first_progress_check_ms)
                state.progress_check_count = 0
                state.progress_update_count = 0
                state.last_progress_message = None
                state.last_task_status_seen = response.task.user_visible_status
                state.delivered_result_generation = None
                
                self._append_history(state, "assistant", None, extra={
                    "role": "assistant", 
                    "tool_calls": [{"id": f"call_{state.active_task_id}", "type": "function", "function": {"name": "start_task", "arguments": json.dumps(args)}}]
                })
                self._append_history(state, "tool", json.dumps(response.model_dump(mode="json")), extra={
                    "role": "tool",
                    "tool_call_id": f"call_{state.active_task_id}",
                    "name": "start_task",
                })
                self._append_history(state, "assistant", assistant_response)
                
                await self._emit(state, VoiceEventType.ASSISTANT_RESPONSE, assistant_response, task_id=state.active_task_id)
            except TaskConflictError as exc:
                conflict = self._codex_task_conflict_payload(state, str(args.get("task", text)))
                conflict["details"] = str(exc)
                self._append_history(state, "tool", json.dumps(conflict), extra={
                    "role": "tool",
                    "tool_call_id": f"call_conflict_{int(self.clock().timestamp() * 1000)}",
                    "name": "start_task",
                })
                response_text = await self._compose_control_answer(
                    state,
                    text,
                    "codex_task_conflict",
                    conflict,
                    str(conflict["message"]),
                )
                self._append_history(state, "assistant", response_text)
                await self._emit(state, VoiceEventType.ASSISTANT_RESPONSE, response_text, task_id=state.active_task_id)
                
        elif tool_name == "amend_task":
            task_id = args.get("task_id")
            amendment = args.get("amendment")
            if task_id and amendment:
                try:
                    status = await self.controller.amend_task(task_id, AmendTaskRequest(amendment=amendment))
                    msg = await self._compose_control_answer(
                        state,
                        text,
                        "amend_task",
                        status.model_dump(mode="json"),
                        "I updated that task.",
                    )
                    self._append_history(state, "assistant", None, extra={
                        "role": "assistant", 
                        "tool_calls": [{"id": f"call_{task_id}_amend", "type": "function", "function": {"name": "amend_task", "arguments": json.dumps(args)}}]
                    })
                    self._append_history(state, "tool", json.dumps(status.model_dump(mode="json")), extra={
                        "role": "tool",
                        "tool_call_id": f"call_{task_id}_amend",
                        "name": "amend_task",
                    })
                    self._append_history(state, "assistant", msg)
                    await self._emit(state, VoiceEventType.ASSISTANT_RESPONSE, msg, task_id=task_id)
                except Exception as exc:
                    await self._emit(state, VoiceEventType.ERROR, str(exc))
            else:
                await self._emit(state, VoiceEventType.ERROR, "Missing task_id or amendment for amend_task")
                
        elif tool_name == "cancel_task":
            task_id = args.get("task_id")
            if task_id:
                try:
                    response = await self.controller.cancel_task(task_id, CancelTaskRequest(reason="User voice cancellation"))
                    msg = await self._compose_control_answer(
                        state,
                        text,
                        "cancel_task",
                        response.model_dump(mode="json"),
                        "I stopped that task.",
                    )
                    self._append_history(state, "assistant", None, extra={
                        "role": "assistant", 
                        "tool_calls": [{"id": f"call_{task_id}_cancel", "type": "function", "function": {"name": "cancel_task", "arguments": json.dumps(args)}}]
                    })
                    self._append_history(state, "tool", json.dumps(response.model_dump(mode="json")), extra={
                        "role": "tool",
                        "tool_call_id": f"call_{task_id}_cancel",
                        "name": "cancel_task",
                    })
                    self._append_history(state, "assistant", msg)
                    await self._emit(state, VoiceEventType.ASSISTANT_RESPONSE, msg, task_id=task_id)
                    if state.active_task_id == task_id:
                        state.active_task_id = None
                except Exception as exc:
                    await self._emit(state, VoiceEventType.ERROR, str(exc))
        elif isinstance(tool_name, str) and tool_name in NATIVE_TOOL_NAMES:
            try:
                result = await self._execute_native_tool(state, tool_name, args)
            except Exception as exc:
                await self._emit(state, VoiceEventType.ERROR, str(exc))
                return
            tool_call_id = f"call_{tool_name}_{int(self.clock().timestamp() * 1000)}"
            self._append_history(state, "assistant", None, extra={
                "role": "assistant",
                "tool_calls": [{"id": tool_call_id, "type": "function", "function": {"name": tool_name, "arguments": json.dumps(args)}}],
            })
            self._append_history(state, "tool", json.dumps(result), extra={
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": tool_name,
            })
            assistant_response = (decision.get("assistant_response") or "").strip()
            if result.get("ok") is False:
                assistant_response = await self._compose_native_tool_answer(
                    state,
                    text,
                    tool_name,
                    result,
                    "That completion does not match the tracked activity yet.",
                )
            elif assistant_response in GENERIC_TOOL_ACKS:
                assistant_response = await self._compose_native_tool_answer(
                    state,
                    text,
                    tool_name,
                    result,
                    str(result.get("message") or "Done.").strip() or "Done.",
                )
            self._append_history(state, "assistant", assistant_response)
            await self._emit(state, VoiceEventType.ASSISTANT_RESPONSE, assistant_response, task_id=state.active_task_id)
                    
        else:
            await self._emit(state, VoiceEventType.ERROR, f"Unknown LLM decision: {tool_name}")

    async def _handle_audio_frame(self, state: VoiceSessionState, event: VoiceEvent) -> None:
        if state.active_utterance is None:
            segment_id = str(event.metadata.get("speech_segment_id") or "default")
            state.active_utterance = UtteranceBuffer(segment_id)
        try:
            frame = parse_audio_frame(event.audio_ref, event.metadata)
            state.active_utterance.append(frame)
            if state.active_asr_stream is not None:
                transcript_events = await state.active_asr_stream.append(frame)
                for transcript_event in transcript_events:
                    await self._handle_transcript_event(state, transcript_event)
        except (ValueError, TypeError) as exc:
            await self._emit(state, VoiceEventType.STT_ERROR, f"I could not read that audio chunk: {exc}")

    async def _finalize_utterance(self, state: VoiceSessionState) -> None:
        if state.active_utterance is None:
            return
        segment_id = state.active_utterance.speech_segment_id
        frames = state.active_utterance.finalize()
        state.active_utterance = None
        if not frames or self.stt_provider is None:
            return
        if state.active_asr_stream is not None:
            stream = state.active_asr_stream
            state.active_asr_stream = None
            try:
                await self._handle_transcript_event(state, await stream.commit())
                return
            except Exception as e:
                logging.debug(f"Streaming transcription failed (likely background noise): {e}")
        try:
            result = await self.stt_provider.transcribe_utterance(frames)
        except Exception as e:
            logging.debug(f"Utterance transcription failed (likely background noise): {e}")
            return
        transcript = result.text.strip()
        if not transcript:
            return
        if segment_id in state.processed_segment_ids:
            return
        state.processed_segment_ids.add(segment_id)
        state.last_transcript = transcript
        candidate = self._validate_asr_candidate(state, transcript, segment_id)
        await self._emit_transcript_final(state, transcript, {"provider": result.provider, **result.metadata}, candidate)
        if candidate.status != "accepted":
            return
        if candidate.during_assistant_speech or candidate.during_cooldown:
            await self._stop_assistant_audio(state)
        
        state.pending_transcript = f"{state.pending_transcript} {transcript}".strip()
        state.pending_transcript_since = self.clock()
        if self._classify_commit_delay(state) == 0:
            await self._maybe_commit_turn(state)

    async def _start_asr_stream(self, state: VoiceSessionState, segment_id: str) -> SttStream | None:
        if self.stt_provider is None or self.effective_asr_mode != AsrMode.SPEECH_GATED_STREAMING:
            return None
        try:
            return await self.stt_provider.start_stream(segment_id)
        except Exception:
            await self._emit(
                state,
                VoiceEventType.STT_ERROR,
                "Streaming transcription is unavailable. Using buffered transcription for this turn.",
                metadata={"fallback": "utterance_batch"},
            )
            state.active_asr_mode = AsrMode.UTTERANCE_BATCH
            return None

    async def _handle_transcript_event(self, state: VoiceSessionState, event: SttTranscriptEvent) -> None:
        if state.active_utterance and event.segment_id != state.active_utterance.speech_segment_id:
            return
        transcript = event.text.strip()
        if event.event == SttTranscriptEventType.PARTIAL:
            if not transcript:
                return
            state.last_partial_transcript = transcript
            await self._emit(
                state,
                VoiceEventType.TRANSCRIPT_PARTIAL,
                transcript,
                metadata={"provider": event.provider, "confidence": event.confidence, **event.metadata},
            )
            await self._maybe_cancel_from_partial(state, transcript, event.confidence)
            return

        if event.event == SttTranscriptEventType.ERROR:
            await self._emit(state, VoiceEventType.STT_ERROR, transcript or "Transcription failed.")
            return

        if event.event == SttTranscriptEventType.FINAL:
            if not transcript:
                return
            if event.segment_id in state.processed_segment_ids:
                return
            state.processed_segment_ids.add(event.segment_id)
            state.last_transcript = transcript
            candidate = self._validate_asr_candidate(state, transcript, event.segment_id)
            await self._emit_transcript_final(
                state,
                transcript,
                {"provider": event.provider, "confidence": event.confidence, **event.metadata},
                candidate,
            )
            if candidate.status != "accepted":
                return
            if candidate.during_assistant_speech or candidate.during_cooldown:
                await self._stop_assistant_audio(state)
            
            state.pending_transcript = f"{state.pending_transcript} {transcript}".strip()
            state.pending_transcript_since = self.clock()
            if self._classify_commit_delay(state) == 0:
                await self._maybe_commit_turn(state)

    async def _maybe_cancel_from_partial(self, state: VoiceSessionState, transcript: str, confidence: float | None) -> None:
        if confidence is not None and confidence < 0.75:
            return
        normalized = transcript.strip().lower().rstrip(".!")
        cancellation_phrases = {"stop", "stop that", "cancel", "cancel that", "never mind", "pause", "don't do that"}
        if normalized not in cancellation_phrases:
            return
        if self._matches_recent_assistant_text(state, _normalize_turn_text(transcript)):
            return
        if state.assistant_speaking:
            await self._stop_assistant_audio(state)
        if state.active_task_id:
            task_id = state.active_task_id
            response = await self.controller.cancel_task(task_id, CancelTaskRequest(reason=f"Partial voice cancellation: {transcript}"))
            reply = await self._compose_control_answer(
                state,
                transcript,
                "cancel_task",
                response.model_dump(mode="json"),
                "I stopped that task.",
            )
            self._append_history(state, "tool", json.dumps(response.model_dump(mode="json")), extra={
                "role": "tool",
                "tool_call_id": f"call_{task_id}_partial_cancel",
                "name": "cancel_task",
            })
            self._append_history(state, "assistant", reply)
            await self._emit(state, VoiceEventType.ASSISTANT_RESPONSE, reply, task_id=task_id)
            state.active_task_id = None

    async def _control_loop(self, state: VoiceSessionState) -> None:
        interval = self.timing.control_loop_ms / 1000
        try:
            while not state.stopped:
                await asyncio.sleep(interval)
                self._advance_turn_state(state)
                await self._maybe_commit_turn(state)
                await self._poll_task_result(state)
                await self._maybe_deliver_timer_alert(state)
                await self._maybe_emit_progress(state)
                await self._maybe_deliver_result(state)
        except asyncio.CancelledError:
            raise

    async def _poll_task_result(self, state: VoiceSessionState) -> None:
        if state.active_task_id is None or state.queued_result is not None:
            return
        result = self.controller.read_result(state.active_task_id)
        if result is None:
            return
        if result.status in {RuntimeResultStatus.CANCELLED, RuntimeResultStatus.STALE}:
            return
        state.queued_result = result

    async def _maybe_emit_progress(self, state: VoiceSessionState) -> None:
        if self._has_pending_timer_delivery(state):
            return
        if state.active_task_id is None or state.queued_result is not None or state.task_started_at is None:
            return

        task = self.controller.tasks.get(state.active_task_id)
        if task is None or task.status not in {TaskStatus.RUNNING, TaskStatus.AMENDING, TaskStatus.WAITING_FOR_APPROVAL}:
            return
        if state.user_speaking or state.assistant_speaking:
            return

        now = self.clock()
        meaningful_progress = task.user_visible_status != state.last_task_status_seen
        if meaningful_progress:
            state.last_task_status_seen = task.user_visible_status

        if not meaningful_progress and state.next_progress_check_at is not None and now < state.next_progress_check_at:
            return

        state.progress_check_count += 1
        decision = await self._decide_progress_action(state, task, meaningful_progress)
        next_check_ms = decision.get("next_check_ms") if isinstance(decision, dict) else None
        state.next_progress_check_at = now + timedelta(milliseconds=self._normalize_next_progress_check(next_check_ms))
        if not isinstance(decision, dict):
            return
        action = str(decision.get("action") or "stay_silent").lower()
        message = (decision.get("message") or "").strip()
        if action != "speak_progress" or not message:
            return
        if message == state.last_progress_message:
            return
        state.last_progress_message = message
        state.last_progress_spoken_at = now
        state.progress_update_count += 1
        await self._emit(state, VoiceEventType.TASK_STATUS, message, task_id=state.active_task_id)

    async def _maybe_deliver_timer_alert(self, state: VoiceSessionState) -> None:
        if state.user_speaking or state.assistant_speaking:
            return
        record = self._next_pending_timer_delivery(state)
        if record is None:
            return
        answer = await self._compose_runtime_event_answer(state, record)
        record.status = "speaking"
        record.text = answer
        self._append_history(state, "assistant", answer)
        await self._emit(
            state,
            VoiceEventType.ASSISTANT_RESPONSE,
            answer,
            metadata={
                "durable": True,
                "delivery_id": record.delivery_id,
                "utterance_id": record.delivery_id,
                "event_type": VoiceEventType.ASSISTANT_RESPONSE.value,
                "delivery_kind": record.kind,
            },
        )

    def _next_pending_timer_delivery(self, state: VoiceSessionState) -> DeliveryRecord | None:
        pending = [
            record
            for record in state.delivery_records.values()
            if record.kind == "timer" and record.status in {"emitted", "interrupted"}
        ]
        if not pending:
            return None
        pending.sort(key=lambda record: record.emitted_at)
        return pending[0]

    def _has_pending_timer_delivery(self, state: VoiceSessionState) -> bool:
        return self._next_pending_timer_delivery(state) is not None

    async def _decide_progress_action(self, state: VoiceSessionState, task: Any, meaningful_progress: bool) -> dict[str, Any]:
        decider = getattr(self.llm_provider, "decide_progress", None)
        if callable(decider):
            try:
                decision = await decider(self._build_progress_state(state, task, meaningful_progress), state.conversation_history)
                if isinstance(decision, dict):
                    return decision
            except Exception as exc:
                logging.error(f"Error deciding progress action: {exc}")
        if meaningful_progress and task.user_visible_status != state.last_progress_message:
            return {"action": "speak_progress", "message": task.user_visible_status, "next_check_ms": None}
        return {"action": "stay_silent", "message": None, "next_check_ms": None}

    def _normalize_next_progress_check(self, next_check_ms: object) -> int:
        if isinstance(next_check_ms, int | float) and next_check_ms > 0:
            return int(next_check_ms)
        if self.timing.repeated_progress_check_max_ms <= self.timing.repeated_progress_check_min_ms:
            return self.timing.repeated_progress_check_min_ms
        return random.randint(self.timing.repeated_progress_check_min_ms, self.timing.repeated_progress_check_max_ms)

    async def _maybe_deliver_result(self, state: VoiceSessionState) -> None:
        result = state.queued_result
        if result is None or state.user_speaking or state.assistant_speaking:
            return

        if self.timing.assistant_ack_timeout_ms > 0 and state.recent_assistant_utterances:
            last_emitted_at, _ = state.recent_assistant_utterances[-1]
            if state.assistant_speech_started_at is None or state.assistant_speech_started_at < last_emitted_at:
                elapsed = self._elapsed_ms(last_emitted_at)
                if elapsed < self.timing.assistant_ack_timeout_ms:
                    return

        if state.delivered_result_generation == result.generation:
            return

        answer = await self._compose_delivery_answer(state, result)
        self._append_tool_result_history(state, result)
        self._append_history(state, "assistant", answer)
        task = self.controller.tasks.get(result.task_id)
        delivery = self._create_delivery_record(
            state,
            delivery_id=f"delivery_{result.task_id}_{result.generation}",
            kind="background_task",
            task_id=result.task_id,
            generation=result.generation,
            text=answer,
            original_request=task.original_request if task else result.task_id,
        )
        await self._emit(
            state,
            VoiceEventType.DELIVERY_READY,
            answer,
            task_id=result.task_id,
            metadata={
                "durable": True,
                "delivery_id": delivery.delivery_id,
                "utterance_id": delivery.delivery_id,
                "event_type": VoiceEventType.DELIVERY_READY.value,
            },
        )
        state.delivered_result_generation = result.generation
        state.recent_completed_events.append({
            "type": "background_task",
            "task_id": result.task_id,
            "status": result.status.value,
            "completed_at": self._iso_now(),
            "delivery_id": delivery.delivery_id,
            "delivery_status": delivery.status,
        })
        state.recent_completed_events = state.recent_completed_events[-10:]
        state.queued_result = None
        state.active_task_id = None

    async def _compose_delivery_answer(self, state: VoiceSessionState, result: RuntimeResult) -> str:
        fallback = result.spoken_answer or result.error or "The task finished without a spoken answer."
        if result.status == RuntimeResultStatus.COMPLETED:
            fallback = f"I have the result now. {fallback}"

        composer = getattr(self.llm_provider, "compose_tool_result", None)
        if not callable(composer):
            return fallback

        sys_state = f"Completed Task ID: {result.task_id}\nCompleted Task Status: {result.status.value}"
        task = self.controller.tasks.get(result.task_id)
        user_text = task.original_request if task else result.task_id
        try:
            composed = await composer(
                user_text,
                sys_state,
                state.conversation_history,
                result,
            )
        except Exception as exc:
            logging.error(f"Error composing delivery answer: {exc}")
            return fallback
        return (composed or "").strip() or fallback

    async def _compose_native_tool_answer(
        self,
        state: VoiceSessionState,
        user_text: str,
        tool_name: str,
        result: dict[str, Any],
        fallback: str,
    ) -> str:
        composer = getattr(self.llm_provider, "compose_native_tool_result", None)
        if not callable(composer):
            return fallback
        try:
            composed = await composer(
                user_text,
                self._build_system_state(state),
                state.conversation_history,
                tool_name,
                result,
                fallback,
            )
        except Exception as exc:
            logging.error(f"Error composing native tool answer: {exc}")
            return fallback
        return (composed or "").strip() or fallback

    async def _compose_runtime_event_answer(self, state: VoiceSessionState, record: DeliveryRecord) -> str:
        composer = getattr(self.llm_provider, "compose_runtime_event_result", None)
        if not callable(composer):
            return record.text
        event_payload = next(
            (
                event
                for event in reversed(state.recent_completed_events)
                if event.get("delivery_id") == record.delivery_id
            ),
            {"type": record.kind, "delivery_id": record.delivery_id},
        )
        try:
            composed = await composer(
                self._build_system_state(state),
                state.conversation_history,
                self._delivery_payload(record),
                event_payload,
                record.text,
            )
        except Exception as exc:
            logging.error(f"Error composing runtime event answer: {exc}")
            return record.text
        return (composed or "").strip() or record.text

    async def _compose_control_answer(
        self,
        state: VoiceSessionState,
        user_text: str,
        action_name: str,
        payload: dict[str, Any],
        fallback: str,
    ) -> str:
        composer = getattr(self.llm_provider, "compose_control_result", None)
        if not callable(composer):
            return fallback
        try:
            composed = await composer(
                user_text,
                self._build_system_state(state),
                state.conversation_history,
                action_name,
                payload,
                fallback,
            )
        except Exception as exc:
            logging.error(f"Error composing control answer: {exc}")
            return fallback
        return (composed or "").strip() or fallback

    def _append_tool_result_history(self, state: VoiceSessionState, result: RuntimeResult) -> None:
        tool_call_id = f"result_{result.task_id}"
        if any(msg.get("role") == "tool" and msg.get("tool_call_id") == tool_call_id for msg in state.conversation_history):
            return
        self._append_history(state, "tool", json.dumps(result.model_dump(mode="json")), extra={
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": "start_task",
        })

    def _append_history(
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
        message["timestamp"] = self._iso_now()
        state.conversation_history.append(message)

    def _iso_now(self) -> str:
        return self.clock().astimezone().isoformat()

    def _build_system_state(self, state: VoiceSessionState) -> str:
        active_task_id = state.active_task_id or "None"
        return f"Active Task ID: {active_task_id}\nStructured Session State:\n{json.dumps(self._session_state_payload(state), default=str, indent=2)}"

    def _session_state_payload(self, state: VoiceSessionState) -> dict[str, Any]:
        now = self.clock()
        active_task = None
        if state.active_task_id:
            task = self.controller.tasks.get(state.active_task_id)
            if task is not None:
                active_task = self._task_payload(state, task, now)
        return {
            "current_time": self._iso_now(),
            "codex_concurrency": {
                "max_active_codex_tasks": 1,
                "active_codex_task_count": 1 if active_task else 0,
                "can_start_new_codex_task": active_task is None,
                "native_tools_available_while_codex_runs": True,
            },
            "active_timers": [self._timer_payload(timer, now) for timer in state.active_timers.values() if timer.status == "running"],
            "active_activities": [
                self._activity_payload(activity, now) for activity in state.active_activities.values() if activity.status == "active"
            ],
            "active_codex_tasks": [active_task] if active_task else [],
            "pending_unheard_items": [self._delivery_payload(record) for record in state.delivery_records.values() if record.status in {"interrupted", "emitted", "speaking"}],
            "recent_completed_events": state.recent_completed_events[-10:],
            "recent_user_claims": state.recent_user_claims[-10:],
        }

    def _create_delivery_record(
        self,
        state: VoiceSessionState,
        *,
        delivery_id: str,
        kind: str,
        task_id: str,
        generation: int,
        text: str,
        original_request: str,
    ) -> DeliveryRecord:
        record = DeliveryRecord(
            delivery_id=delivery_id,
            kind=kind,
            task_id=task_id,
            generation=generation,
            text=text,
            original_request=original_request,
            emitted_at=self.clock(),
        )
        state.delivery_records[delivery_id] = record
        return record

    def _mark_delivery_speaking(self, state: VoiceSessionState, metadata: dict[str, Any]) -> None:
        delivery_id = metadata.get("delivery_id")
        if not delivery_id:
            return
        record = state.delivery_records.get(str(delivery_id))
        if record and record.status != "spoken":
            record.status = "speaking"

    def _mark_delivery_speech_ended(self, state: VoiceSessionState, metadata: dict[str, Any]) -> None:
        delivery_id = metadata.get("delivery_id")
        if not delivery_id:
            return
        record = state.delivery_records.get(str(delivery_id))
        if record is None:
            return
        completed = metadata.get("completed")
        if completed is True:
            record.status = "spoken"
            record.reason = None
            record.spoken_at = self.clock()
            return
        record.status = "interrupted"
        record.reason = str(metadata.get("reason") or "interrupted")

    def _delivery_payload(self, record: DeliveryRecord) -> dict[str, Any]:
        return {
            "delivery_id": record.delivery_id,
            "kind": record.kind,
            "task_id": record.task_id,
            "generation": record.generation,
            "original_request": record.original_request,
            "text": record.text,
            "status": record.status,
            "reason": record.reason,
            "emitted_at": record.emitted_at.astimezone().isoformat(),
            "durable": True,
            "instruction": "This durable result was emitted but not confirmed heard by the user. Do not claim it was already delivered.",
        }

    def _has_active_codex_task(self, state: VoiceSessionState) -> bool:
        if not state.active_task_id:
            return False
        task = self.controller.tasks.get(state.active_task_id)
        return task is not None and task.status in {
            TaskStatus.RUNNING,
            TaskStatus.AMENDING,
            TaskStatus.WAITING_FOR_APPROVAL,
            TaskStatus.WAITING_FOR_TOOL,
            TaskStatus.WAITING_FOR_USER_CLARIFICATION,
        }

    def _active_task_conflict_message(self, state: VoiceSessionState) -> str:
        task = self.controller.tasks.get(state.active_task_id or "")
        if task is None:
            return "I already have a background task running. I can cancel it and start the new one, or keep the current one going."
        return (
            "I already have a background task running: "
            f"{task.original_request}. I can cancel it and start the new one, or keep this one going while we handle simpler things."
        )

    def _codex_task_conflict_payload(self, state: VoiceSessionState, attempted_task: str) -> dict[str, Any]:
        task = self.controller.tasks.get(state.active_task_id or "")
        active_task = None
        if task is not None:
            active_task = {
                "task_id": task.task_id,
                "status": task.status.value,
                "original_request": task.original_request,
                "user_visible_status": task.user_visible_status,
            }
        return {
            "ok": False,
            "error_code": "codex_task_conflict",
            "recoverable": True,
            "message": self._active_task_conflict_message(state),
            "attempted_task": attempted_task,
            "active_task": active_task,
            "allowed_next_actions": ["cancel_active_task", "keep_active_task", "use_native_tool", "answer_directly"],
        }

    def _timer_payload(self, timer: SessionTimer, now: datetime) -> dict[str, Any]:
        elapsed_ms = max(0, int((now - timer.started_at) / timedelta(milliseconds=1)))
        remaining_ms = max(0, timer.duration_ms - elapsed_ms)
        return {
            "timer_id": timer.timer_id,
            "label": timer.label,
            "started_at": timer.started_at.astimezone().isoformat(),
            "ends_at": timer.ends_at.astimezone().isoformat(),
            "elapsed_ms": elapsed_ms,
            "remaining_ms": remaining_ms,
            "status": timer.status,
            "reason": timer.reason,
        }

    def _activity_payload(self, activity: SessionActivity, now: datetime) -> dict[str, Any]:
        elapsed_ms = max(0, int((now - activity.started_at) / timedelta(milliseconds=1)))
        return {
            "activity_id": activity.activity_id,
            "type": activity.activity_type,
            "label": activity.label,
            "started_at": activity.started_at.astimezone().isoformat(),
            "elapsed_ms": elapsed_ms,
            "target_duration_ms": activity.target_duration_ms,
            "target_distance_meters": activity.target_distance_meters,
            "status": activity.status,
        }

    def _task_payload(self, state: VoiceSessionState, task: Any, now: datetime) -> dict[str, Any]:
        elapsed_ms = self._datetime_delta_ms(now, state.task_started_at or task.created_at)
        last_progress_ms_ago = self._datetime_delta_ms(now, task.last_update_at)
        return {
            "task_id": task.task_id,
            "type": "codex",
            "status": task.status.value,
            "user_visible_status": task.user_visible_status,
            "elapsed_ms": max(0, elapsed_ms),
            "last_meaningful_progress_ms_ago": max(0, last_progress_ms_ago),
            "last_spoken_update_ms_ago": int(self._elapsed_ms(state.last_progress_spoken_at)) if state.last_progress_spoken_at else None,
            "progress_update_count": state.progress_update_count,
        }

    def _build_progress_state(self, state: VoiceSessionState, task: Any, meaningful_progress: bool) -> dict[str, Any]:
        now = self.clock()
        return {
            "current_time": self._iso_now(),
            "task": self._task_payload(state, task, now),
            "conversation_state": {
                "user_speaking": state.user_speaking,
                "assistant_speaking": state.assistant_speaking,
                "user_last_spoke_ms_ago": int(self._elapsed_ms(state.last_user_turn_at)) if state.last_user_turn_at else None,
                "last_agent_message": self._last_assistant_message(state),
            },
            "meaningful_progress": meaningful_progress,
            "instruction": "Decide whether the agent should speak now or stay silent.",
        }

    async def _execute_native_tool(self, state: VoiceSessionState, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "start_timer":
            duration_ms = int(args["duration_ms"])
            timer = SessionTimer(
                timer_id=f"timer_{len(state.active_timers) + 1:03d}",
                label=str(args.get("label") or "timer"),
                duration_ms=duration_ms,
                reason=str(args["reason"]) if args.get("reason") is not None else None,
                started_at=self.clock(),
            )
            state.active_timers[timer.timer_id] = timer
            return {"timer": self._timer_payload(timer, self.clock()), "message": f"I started the {timer.label} timer."}
        if tool_name == "list_active_timers":
            return {"timers": [self._timer_payload(timer, self.clock()) for timer in state.active_timers.values() if timer.status == "running"]}
        if tool_name == "get_timer_status":
            timer = self._select_timer(state, args.get("timer_id"))
            return {"timer": self._timer_payload(timer, self.clock()), "message": self._timer_status_message(timer)}
        if tool_name == "cancel_timer":
            timer = self._select_timer(state, args.get("timer_id"))
            timer.status = "cancelled"
            return {"timer": self._timer_payload(timer, self.clock()), "message": f"I cancelled the {timer.label} timer."}
        if tool_name == "start_activity":
            activity = SessionActivity(
                activity_id=f"activity_{len(state.active_activities) + 1:03d}",
                activity_type=str(args.get("activity_type") or "activity"),
                label=str(args.get("label") or args.get("activity_type") or "activity"),
                target_duration_ms=int(args["target_duration_ms"]) if args.get("target_duration_ms") is not None else None,
                target_distance_meters=int(args["target_distance_meters"]) if args.get("target_distance_meters") is not None else None,
                started_at=self.clock(),
            )
            state.active_activities[activity.activity_id] = activity
            return {"activity": self._activity_payload(activity, self.clock()), "message": f"I started tracking {activity.label}."}
        if tool_name == "list_active_activities":
            return {"activities": [self._activity_payload(activity, self.clock()) for activity in state.active_activities.values() if activity.status == "active"]}
        if tool_name == "get_activity_status":
            activity = self._select_activity(state, args.get("activity_id"))
            return {"activity": self._activity_payload(activity, self.clock()), "message": f"{activity.label} is still active."}
        if tool_name == "end_activity":
            activity = self._select_activity(state, args.get("activity_id"))
            rejection = self._reject_obviously_invalid_activity_completion(activity, self.clock())
            if rejection is not None:
                return rejection
            activity.status = "completed"
            payload = self._activity_payload(activity, self.clock())
            state.recent_completed_events.append({"type": "activity", "activity": payload, "completed_at": self._iso_now()})
            state.recent_completed_events = state.recent_completed_events[-10:]
            return {"activity": payload, "message": f"I marked {activity.label} complete."}
        if tool_name == "cancel_activity":
            activity = self._select_activity(state, args.get("activity_id"))
            activity.status = "cancelled"
            return {"activity": self._activity_payload(activity, self.clock()), "message": f"I cancelled tracking {activity.label}."}
        if tool_name == "list_active_background_tasks":
            return {"tasks": [self._task_payload(state, task, self.clock()) for task in self.controller.tasks.values() if task.status in {TaskStatus.RUNNING, TaskStatus.AMENDING, TaskStatus.WAITING_FOR_APPROVAL}]}
        if tool_name == "get_background_task_status":
            task_id = str(args.get("task_id") or state.active_task_id or "")
            status = self.controller.get_status(task_id)
            return {"task": status.task.model_dump(mode="json"), "progress": status.progress, "message": status.progress or status.task.user_visible_status}
        if tool_name == "cancel_background_task":
            task_id = str(args.get("task_id") or state.active_task_id or "")
            response = await self.controller.cancel_task(task_id, CancelTaskRequest(reason=str(args.get("reason") or "User voice cancellation")))
            if state.active_task_id == task_id:
                state.active_task_id = None
            return response.model_dump(mode="json") | {"message": "I stopped that task."}
        raise ValueError(f"Unknown native tool: {tool_name}")

    def _select_timer(self, state: VoiceSessionState, timer_id: object) -> SessionTimer:
        if timer_id:
            timer = state.active_timers.get(str(timer_id))
            if timer:
                return timer
            raise ValueError(f"Timer {timer_id} was not found.")
        active = [timer for timer in state.active_timers.values() if timer.status == "running"]
        if len(active) == 1:
            return active[0]
        raise ValueError("I need to know which timer.")

    def _select_activity(self, state: VoiceSessionState, activity_id: object) -> SessionActivity:
        if activity_id:
            activity = state.active_activities.get(str(activity_id))
            if activity:
                return activity
            raise ValueError(f"Activity {activity_id} was not found.")
        active = [activity for activity in state.active_activities.values() if activity.status == "active"]
        if len(active) == 1:
            return active[0]
        raise ValueError("I need to know which activity.")

    def _reject_obviously_invalid_activity_completion(
        self,
        activity: SessionActivity,
        now: datetime,
    ) -> dict[str, Any] | None:
        payload = self._activity_payload(activity, now)
        elapsed_ms = int(payload["elapsed_ms"])
        if activity.target_duration_ms is not None and elapsed_ms < activity.target_duration_ms:
            return {
                "ok": False,
                "error_code": "activity_completion_too_early",
                "reason": "The tracked activity duration has not elapsed yet.",
                "activity": payload,
                "elapsed_ms": elapsed_ms,
                "target_duration_ms": activity.target_duration_ms,
            }
        if activity.target_distance_meters is None or elapsed_ms <= 0:
            return None
        elapsed_seconds = elapsed_ms / 1000
        speed_mps = activity.target_distance_meters / elapsed_seconds
        if speed_mps <= 12:
            return None
        return {
            "ok": False,
            "error_code": "activity_completion_physically_implausible",
            "reason": "The tracked distance and elapsed time imply an impossible pace.",
            "activity": payload,
            "elapsed_ms": elapsed_ms,
            "target_distance_meters": activity.target_distance_meters,
            "implied_speed_mps": round(speed_mps, 1),
        }

    def _timer_status_message(self, timer: SessionTimer) -> str:
        payload = self._timer_payload(timer, self.clock())
        remaining = int(payload["remaining_ms"] / 1000)
        if remaining <= 0:
            return f"The {timer.label} timer is done."
        return f"The {timer.label} timer still has about {remaining} seconds left."

    def _record_user_claim(self, state: VoiceSessionState, text: str) -> None:
        normalized = _normalize_turn_text(text)
        claim_type = None
        if any(word in normalized.split() for word in {"finished", "done", "completed"}):
            claim_type = "completion_claim"
        elif any(phrase in normalized for phrase in {"i am going", "i'm going", "starting", "start"}):
            claim_type = "start_claim"
        if claim_type is None:
            return
        state.recent_user_claims.append({"text": text, "timestamp": self._iso_now(), "type": claim_type})
        state.recent_user_claims = state.recent_user_claims[-10:]

    def _last_assistant_message(self, state: VoiceSessionState) -> str | None:
        for message in reversed(state.conversation_history):
            if message.get("role") == "assistant" and isinstance(message.get("content"), str):
                return message["content"]
        return None

    @staticmethod
    def _format_pace(seconds_per_km: float) -> str:
        minutes = int(seconds_per_km // 60)
        seconds = int(seconds_per_km % 60)
        return f"{minutes}:{seconds:02d} per km"

    @staticmethod
    def _datetime_delta_ms(later: datetime, earlier: datetime) -> int:
        if later.tzinfo is None and earlier.tzinfo is not None:
            earlier = earlier.replace(tzinfo=None)
        elif later.tzinfo is not None and earlier.tzinfo is None:
            later = later.replace(tzinfo=None)
        return int((later - earlier) / timedelta(milliseconds=1))

    def _build_task_context(self, state: VoiceSessionState, current_user_text: str) -> str:
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

    async def _emit(
        self,
        state: VoiceSessionState,
        event_type: VoiceEventType,
        text: str,
        task_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        event_metadata = {
            "user_speaking": state.user_speaking,
            "assistant_speaking": state.assistant_speaking,
            "voice_state": state.voice_state,
        }
        if metadata:
            event_metadata.update(metadata)
        event = VoiceEvent(
            event=event_type,
            transport=state.transport,
            session_id=state.session_id,
            text=text,
            task_id=task_id,
            metadata=event_metadata,
        )
        await state.outbound.put(event)
        
        # Notify listener callbacks
        for listener in self.event_listeners:
            try:
                listener(state.session_id, event)
            except Exception:
                pass

        if event_type in {VoiceEventType.ASSISTANT_RESPONSE, VoiceEventType.DELIVERY_READY, VoiceEventType.TASK_STATUS}:
            self._remember_assistant_utterance(state, text)

    def _elapsed_ms(self, since: datetime) -> float:
        return (self.clock() - since) / timedelta(milliseconds=1)

    def _resolve_asr_mode(self) -> AsrMode:
        if (
            self.requested_asr_mode == AsrMode.SPEECH_GATED_STREAMING
            and self.stt_provider is not None
            and self.stt_provider.supports_streaming
        ):
            return AsrMode.SPEECH_GATED_STREAMING
        return AsrMode.UTTERANCE_BATCH

    async def _emit_transcript_final(
        self,
        state: VoiceSessionState,
        transcript: str,
        metadata: dict[str, object],
        candidate: AsrTurnCandidate,
    ) -> None:
        await self._emit(
            state,
            VoiceEventType.TRANSCRIPT_FINAL,
            transcript,
            metadata={
                **metadata,
                "candidate_status": candidate.status,
                "candidate_reason": candidate.reason,
                "speech_segment_id": candidate.segment_id,
                "during_assistant_speech": candidate.during_assistant_speech,
                "during_cooldown": candidate.during_cooldown,
            },
        )

    async def _stop_assistant_audio(self, state: VoiceSessionState) -> None:
        if state.assistant_speaking:
            state.assistant_speaking = False
        state.voice_state = VoiceTurnState.USER_SPEAKING if state.user_speaking else VoiceTurnState.LISTENING
        state.post_tts_cooldown_expires_at = None
        await self._emit(state, VoiceEventType.STOP_ASSISTANT_AUDIO, "Stopping assistant audio.")

    def _advance_turn_state(self, state: VoiceSessionState) -> None:
        if state.voice_state == VoiceTurnState.POST_TTS_COOLDOWN and state.post_tts_cooldown_expires_at:
            if self.clock() >= state.post_tts_cooldown_expires_at:
                state.voice_state = VoiceTurnState.LISTENING
                state.post_tts_cooldown_expires_at = None
        self._expire_timers(state)

    def _expire_timers(self, state: VoiceSessionState) -> None:
        now = self.clock()
        for timer in state.active_timers.values():
            if timer.status != "running" or now < timer.ends_at:
                continue
            timer.status = "completed"
            self._remember_completed_timer(state, timer)

    def _remember_completed_timer(self, state: VoiceSessionState, timer: SessionTimer) -> None:
        timer_payload = self._timer_payload(timer, self.clock())
        delivery_id = f"delivery_{timer.timer_id}_completed"
        if delivery_id not in state.delivery_records:
            self._create_delivery_record(
                state,
                delivery_id=delivery_id,
                kind="timer",
                task_id=timer.timer_id,
                generation=0,
                text=f"The {timer.label} timer is done.",
                original_request=timer.label,
            )
        if any(
            event.get("type") == "timer" and event.get("timer", {}).get("timer_id") == timer.timer_id
            for event in state.recent_completed_events
        ):
            return
        state.recent_completed_events.append(
            {
                "type": "timer",
                "timer": timer_payload,
                "completed_at": self._iso_now(),
                "delivery_id": delivery_id,
                "delivery_status": state.delivery_records[delivery_id].status,
            }
        )
        state.recent_completed_events = state.recent_completed_events[-10:]

    def _candidate_speech_duration_ms(self, state: VoiceSessionState) -> float:
        if state.candidate_speech_started_at is None:
            return 0
        ended_at = state.candidate_speech_ended_at or self.clock()
        return (ended_at - state.candidate_speech_started_at) / timedelta(milliseconds=1)

    def _validate_asr_candidate(self, state: VoiceSessionState, text: str, segment_id: str) -> AsrTurnCandidate:
        now = self.clock()
        started_at = state.candidate_speech_started_at or state.user_speech_started_at
        ended_at = state.candidate_speech_ended_at or now
        during_assistant = state.assistant_speaking or state.voice_state == VoiceTurnState.BARGE_IN_CANDIDATE
        during_cooldown = self._in_post_tts_cooldown(state)
        candidate = AsrTurnCandidate(
            text=text,
            segment_id=segment_id,
            started_at=started_at,
            ended_at=ended_at,
            during_assistant_speech=during_assistant,
            during_cooldown=during_cooldown,
        )
        accepted, reason = self._should_accept_asr_user_turn(state, text, during_assistant, during_cooldown)
        return AsrTurnCandidate(
            text=candidate.text,
            segment_id=candidate.segment_id,
            started_at=candidate.started_at,
            ended_at=candidate.ended_at,
            during_assistant_speech=candidate.during_assistant_speech,
            during_cooldown=candidate.during_cooldown,
            status="accepted" if accepted else "rejected",
            reason=reason,
        )

    def _should_accept_asr_user_turn(
        self,
        state: VoiceSessionState,
        text: str,
        during_assistant: bool | None = None,
        during_cooldown: bool | None = None,
    ) -> tuple[bool, str]:
        normalized = _normalize_turn_text(text)
        if not normalized:
            return False, "empty_transcript"

        recent = self._assistant_recently_spoke(state)
        during_assistant = state.assistant_speaking if during_assistant is None else during_assistant
        during_cooldown = self._in_post_tts_cooldown(state) if during_cooldown is None else during_cooldown
        echo_reason = self._assistant_echo_reason(state, normalized)
        echo = echo_reason is not None
        if _is_hard_interrupt_command(normalized):
            return (False, echo_reason or "matches_recent_assistant_text") if echo else (True, "hard_interrupt_command")

        if recent and _is_soft_amendment_marker_only(normalized):
            return False, "ambiguous_soft_marker_after_tts"

        if (during_assistant or during_cooldown or recent) and echo:
            return False, echo_reason or "matches_recent_assistant_text"

        return True, "validated_user_turn"

    def _assistant_recently_spoke(self, state: VoiceSessionState) -> bool:
        if state.assistant_speaking:
            return True
        if self._in_post_tts_cooldown(state):
            return True
        last_spoken_at = state.last_assistant_speech_at
        if state.recent_assistant_utterances:
            last_spoken_at = max(last_spoken_at or datetime.min, state.recent_assistant_utterances[-1][0])
        if last_spoken_at is None:
            return False
        return self._elapsed_ms(last_spoken_at) <= self.timing.recent_assistant_window_ms

    def _in_post_tts_cooldown(self, state: VoiceSessionState) -> bool:
        expires_at = state.post_tts_cooldown_expires_at
        return expires_at is not None and self.clock() < expires_at

    def _matches_recent_assistant_text(self, state: VoiceSessionState, normalized_text: str) -> bool:
        return self._assistant_echo_reason(state, normalized_text) is not None

    def _assistant_echo_reason(self, state: VoiceSessionState, normalized_text: str) -> str | None:
        self._prune_recent_assistant_utterances(state)
        for _, assistant_text in state.recent_assistant_utterances:
            reason = _assistant_echo_match_reason(normalized_text, _normalize_turn_text(assistant_text), self.timing)
            if reason:
                return reason
        return None

    def _remember_assistant_utterance(self, state: VoiceSessionState, text: str) -> None:
        if not text.strip():
            return
        state.recent_assistant_utterances.append((self.clock(), text))
        state.recent_assistant_utterances = state.recent_assistant_utterances[-RECENT_ASSISTANT_UTTERANCE_LIMIT:]
        self._prune_recent_assistant_utterances(state)

    def _prune_recent_assistant_utterances(self, state: VoiceSessionState) -> None:
        state.recent_assistant_utterances = [
            item
            for item in state.recent_assistant_utterances
            if self._elapsed_ms(item[0]) <= self.timing.recent_assistant_window_ms
        ]


def _normalize_turn_text(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", text.lower()).split())


def _is_hard_interrupt_command(normalized: str) -> bool:
    return normalized in HARD_INTERRUPT_COMMANDS


def _is_soft_amendment_marker_only(normalized: str) -> bool:
    return normalized in SOFT_AMENDMENT_MARKERS


def _assistant_echo_match_reason(
    normalized_text: str,
    normalized_assistant_text: str,
    timing: VoiceTimingConfig,
) -> str | None:
    if not normalized_text or not normalized_assistant_text:
        return None
    if normalized_text == normalized_assistant_text:
        return "matches_recent_assistant_text"
    text_words = normalized_text.split()
    assistant_words = normalized_assistant_text.split()
    if len(text_words) <= timing.short_transcript_max_words and assistant_words[: len(text_words)] == text_words:
        return "short_prefix_echo"
    if len(normalized_text) >= 12 and normalized_text in normalized_assistant_text:
        return "matches_recent_assistant_text"
    if len(normalized_assistant_text) >= 12 and normalized_assistant_text in normalized_text:
        return "contains_recent_assistant_text"

    similarity = SequenceMatcher(None, normalized_text, normalized_assistant_text).ratio()
    if similarity >= timing.fuzzy_similarity_threshold:
        return "fuzzy_recent_assistant_match"

    text_tokens = set(text_words)
    assistant_tokens = set(assistant_words)
    if len(text_tokens) < 3 or len(assistant_tokens) < 3:
        return None
    overlap = len(text_tokens & assistant_tokens) / min(len(text_tokens), len(assistant_tokens))
    if overlap >= timing.token_overlap_threshold:
        return "token_overlap_recent_assistant"
    return None
