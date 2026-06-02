from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Callable

from nextgen_voice_agent.agent.controller import AgentController, TaskConflictError
from nextgen_voice_agent.config import get_settings
from nextgen_voice_agent.models.runtime import RuntimeResult, RuntimeResultStatus
from nextgen_voice_agent.models.task import AmendTaskRequest, CancelTaskRequest, StartTaskRequest, TaskStatus
from nextgen_voice_agent.models.voice import VoiceEvent, VoiceEventType, VoiceTransportKind
from nextgen_voice_agent.models.voice_metadata import (
    AssistantSpeechEndedMetadata,
    AssistantSpeechStartedMetadata,
    DurableDeliveryMetadata,
    parse_metadata,
)
from nextgen_voice_agent.voice.llm_router import BRAIN_GLITCH_MESSAGE
from nextgen_voice_agent.voice.session_context import VoiceSessionContext
from nextgen_voice_agent.voice.stt import (
    AsrMode,
    AudioFrame,
    SttProvider,
    SttStream,
    SttTranscriptEvent,
    SttTranscriptEventType,
    UtteranceBuffer,
    parse_audio_frame,
)
from nextgen_voice_agent.voice.voice_logging import log_voice_event
from nextgen_voice_agent.voice.conversation_history import ConversationHistoryManager
from nextgen_voice_agent.voice.delivery_manager import DeliveryManager, DeliveryManagerCallbacks
from nextgen_voice_agent.voice.interrupt_manager import InterruptManager, InterruptManagerCallbacks
from nextgen_voice_agent.voice.interrupt_types import ActiveVadClassification, InterruptCandidate, PostVadClassification
from nextgen_voice_agent.voice.turn_commit import TurnCommitter
from nextgen_voice_agent.voice.turn_text import (
    HARD_INTERRUPT_COMMANDS,
    SOFT_AMENDMENT_MARKERS,
    is_hard_interrupt_command,
    is_soft_amendment_marker_only,
    normalize_turn_text,
)
from nextgen_voice_agent.voice.native_tool_executor import NativeToolExecutor
from nextgen_voice_agent.voice.session_entities import DeliveryRecord, SessionActivity, SessionTimer, format_ms_to_human
from nextgen_voice_agent.voice.tool_catalog import NATIVE_TOOL_NAMES

RECENT_ASSISTANT_UTTERANCE_LIMIT = 6

USER_TURN_FOLLOWUP_INSTRUCTION = "User just spoke. Latest tool result is in history. Say the reply."
BACKGROUND_RESEARCH_INSTRUCTION = "Background research finished; result is in history. Say the reply."
BACKGROUND_TIMER_INSTRUCTION = "Timer completed; facts are in state/history. Say the reply."


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
    vad_investigation_max_ms: int = 900
    echo_validation_ms: int = 400
    post_vad_buffer_ms: int = 400
    min_interrupt_words: int = 2
    min_interrupt_chars: int = 6
    response_lead_in_ms: int = 0


@dataclass
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
    playback_paused: bool = False
    pause_resume_timer_expires_at: datetime | None = None
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
    utterance_open: bool = False
    finalizing_utterance: bool = False
    assistant_audio_paused_for_interrupt: bool = False
    interrupt_investigation_active: bool = False
    non_echo_speech_detected_during_investigation: bool = False
    echo_like_detected_at: datetime | None = None
    transcript_seen_during_investigation: bool = False
    vad_investigation_expires_at: datetime | None = None
    post_vad_buffer_expires_at: datetime | None = None
    post_vad_commit_pending: bool = False
    post_vad_commit_text: str = ""
    interrupt_candidate: InterruptCandidate = field(default_factory=InterruptCandidate)
    last_activity_at: datetime | None = None


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
        self._settings = get_settings()
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
        self._native_tool_executor = NativeToolExecutor(controller=self.controller, clock=self.clock)
        self._conversation_history = ConversationHistoryManager(
            controller=self.controller,
            clock=self.clock,
            session_state_payload=self._session_state_payload,
        )
        self._delivery_manager = DeliveryManager(
            controller=self.controller,
            clock=self.clock,
            timing=self.timing,
            callbacks=DeliveryManagerCallbacks(
                speak_background_event=self._speak_background_event,
                append_history=self._append_history,
                emit=self._emit,
                iso_now=self._iso_now,
                elapsed_ms=self._elapsed_ms,
            ),
        )
        self._interrupt_manager = InterruptManager(
            timing=self.timing,
            clock=self.clock,
            callbacks=InterruptManagerCallbacks(
                emit=self._emit,
                stop_assistant_audio=self._stop_assistant_audio,
                apply_authoritative_user_transcript=self._apply_authoritative_user_transcript,
            ),
            assistant_echo_reason=self._assistant_echo_reason,
        )
        self._turn_committer = TurnCommitter(
            timing=self.timing,
            clock=self.clock,
            elapsed_ms=self._elapsed_ms,
            handle_user_turn=self._handle_user_turn,
        )

    async def execute_native_tool(
        self,
        session_id: str,
        transport: VoiceTransportKind,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        state = await self.start_session(session_id, transport)
        return await self._native_tool_executor.execute(state, tool_name, args)

    async def start_session(self, session_id: str, transport_kind: VoiceTransportKind) -> VoiceSessionState:
        existing = self.sessions.get(session_id)
        if existing and not existing.stopped:
            return existing

        state = VoiceSessionState(session_id=session_id, transport=transport_kind)
        state.last_activity_at = self.clock()
        self.sessions[session_id] = state
        state.loop_task = asyncio.create_task(self._control_loop(state))
        await self._emit(state, VoiceEventType.SESSION_STARTED, "Voice session started.")
        log_voice_event(
            "session_started",
            session_id=session_id,
            transport=transport_kind.value,
        )
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
        self.sessions.pop(session_id, None)
        log_voice_event("session_stopped", session_id=session_id, transport=state.transport.value)

    async def ensure_session(self, context: VoiceSessionContext) -> str:
        await self.start_session(context.session_id, context.transport)
        state = self.sessions[context.session_id]
        state.last_activity_at = self.clock()
        log_voice_event(
            "session_ensured",
            session_id=context.session_id,
            transport=context.transport.value,
        )
        return context.session_id

    async def ingest_audio_frame(self, session_id: str, frame: AudioFrame) -> None:
        import base64

        state = self.sessions.get(session_id)
        if state is None:
            raise KeyError(f"Unknown voice session: {session_id}")
        event = VoiceEvent(
            event=VoiceEventType.USER_TURN_AUDIO,
            transport=state.transport,
            session_id=session_id,
            audio_ref=base64.b64encode(frame.payload).decode("ascii"),
            metadata={
                "sample_rate": frame.sample_rate,
                "channels": frame.channels,
                "encoding": frame.encoding,
                "sequence": frame.sequence,
                "speech_segment_id": frame.speech_segment_id,
                **frame.metadata,
            },
        )
        await self.handle_voice_event(session_id, event)

    async def end_session(self, session_id: str, reason: str) -> None:
        log_voice_event("session_ending", session_id=session_id, extra={"reason": reason})
        await self.stop_session(session_id)

    def has_session(self, session_id: str) -> bool:
        state = self.sessions.get(session_id)
        return state is not None and not state.stopped

    def get_active_speech_segment_id(self, session_id: str) -> str:
        state = self.sessions.get(session_id)
        if state and state.active_utterance:
            return state.active_utterance.speech_segment_id
        return "default"

    def list_active_native_tools(self) -> list[dict[str, str]]:
        tools: list[dict[str, str]] = []
        for session in self.sessions.values():
            for timer in session.active_timers.values():
                if timer.status == "running":
                    tools.append({
                        "id": timer.timer_id,
                        "type": "timer",
                        "title": getattr(timer, "ui_title", None) or "Timer",
                        "started_at": timer.started_at.astimezone().isoformat(),
                    })
            for activity in session.active_activities.values():
                if activity.status == "active":
                    tools.append({
                        "id": activity.activity_id,
                        "type": "activity",
                        "title": getattr(activity, "ui_title", None) or "Activity",
                        "started_at": activity.started_at.astimezone().isoformat(),
                    })
        return tools

    async def handle_voice_event(self, session_id: str, event: VoiceEvent) -> None:
        state = await self.start_session(session_id, event.transport)
        state.last_activity_at = self.clock()

        if event.event == VoiceEventType.SPEECH_STARTED:
            now = self.clock()
            state.user_speaking = True
            state.user_speech_started_at = now
            state.candidate_speech_started_at = now
            state.candidate_speech_ended_at = None
            state.pause_resume_timer_expires_at = None
            state.utterance_open = True
            segment_id = str(event.metadata.get("speech_segment_id") or f"{session_id}-{int(self.clock().timestamp() * 1000)}")
            state.active_utterance = UtteranceBuffer(segment_id)
            state.active_asr_mode = self.effective_asr_mode
            state.active_asr_stream = await self._start_asr_stream(state, segment_id)
            if state.assistant_speaking or state.assistant_audio_paused_for_interrupt:
                state.voice_state = VoiceTurnState.BARGE_IN_CANDIDATE
                await self._start_interrupt_investigation(state)
            else:
                state.voice_state = VoiceTurnState.USER_SPEAKING
            return

        if event.event == VoiceEventType.USER_TURN_AUDIO:
            await self._handle_audio_frame(state, event)
            return

        if event.event == VoiceEventType.SPEECH_ENDED:
            state.user_speaking = False
            state.candidate_speech_ended_at = self.clock()
            if state.interrupt_investigation_active:
                state.post_vad_commit_pending = True
                state.post_vad_buffer_expires_at = self.clock() + timedelta(milliseconds=self.timing.post_vad_buffer_ms)
            await self._finalize_utterance(state)
            if state.interrupt_investigation_active:
                if state.voice_state == VoiceTurnState.BARGE_IN_CANDIDATE and state.assistant_speaking:
                    state.voice_state = VoiceTurnState.ASSISTANT_SPEAKING
            elif state.voice_state == VoiceTurnState.USER_SPEAKING:
                state.voice_state = VoiceTurnState.LISTENING
            return

        if event.event == VoiceEventType.ASSISTANT_SPEECH_STARTED:
            now = self.clock()
            state.assistant_speaking = True
            state.voice_state = VoiceTurnState.ASSISTANT_SPEAKING
            state.last_assistant_speech_at = now
            state.assistant_speech_started_at = now
            parsed = parse_metadata(event.event, event.metadata)
            if isinstance(parsed, AssistantSpeechStartedMetadata):
                self._mark_delivery_speaking(state, parsed)
            else:
                self._mark_delivery_speaking(state, event.metadata)
            state.post_tts_cooldown_expires_at = None
            return

        if event.event == VoiceEventType.ASSISTANT_SPEECH_ENDED:
            now = self.clock()
            state.assistant_speaking = False
            state.playback_paused = False
            state.pause_resume_timer_expires_at = None
            state.last_assistant_speech_at = now
            state.assistant_speech_ended_at = now
            parsed = parse_metadata(event.event, event.metadata)
            if isinstance(parsed, AssistantSpeechEndedMetadata):
                self._mark_delivery_speech_ended(state, parsed)
            else:
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
        await self._turn_committer.maybe_commit_turn(state)

    def _classify_commit_delay(self, state: VoiceSessionState) -> int:
        return self._turn_committer.classify_commit_delay(state)

    async def _handle_user_turn(self, state: VoiceSessionState, text: str) -> None:
        self._clear_interrupt_investigation(state)
        state.last_user_turn_at = self.clock()

        # A new user turn invalidates any result that was sitting in the delivery queue
        if state.queued_result is not None:
            self._append_tool_result_history(state, state.queued_result)
            state.queued_result = None
            state.active_task_id = None
        state.delivered_result_generation = None
        
        log_voice_event(
            "turn_committed",
            session_id=state.session_id,
            transport=state.transport.value,
            extra={"transcript_preview": text[:120]},
        )
        self._append_history(state, "user", text)
        self._record_user_claim(state, text)
        system_state_str = self._build_system_state(state)
        
        decision = await self.llm_provider.route_turn(text, system_state_str, state.conversation_history[:-1])
        decision_type = decision.get("type")
        log_voice_event(
            "route_decision",
            session_id=state.session_id,
            transport=state.transport.value,
            extra={"decision_type": decision_type},
        )
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
            task_text = args.get("task", text)
            tool_call_id = f"call_start_{int(self.clock().timestamp() * 1000)}"
            try:
                if self._has_active_codex_task(state):
                    conflict = self._codex_task_conflict_payload(state, str(task_text))
                    await self._complete_tool_turn(
                        state,
                        user_text=text,
                        tool_name="start_task",
                        tool_call_id=tool_call_id,
                        tool_args=args,
                        tool_result=conflict,
                        task_id=state.active_task_id,
                    )
                    return
                context = self._build_task_context(state, text)
                logging.info("Voice router starting task with grounded request: %s", task_text)
                logging.debug("Voice task context for Codex:\n%s", context)
                response = await self.controller.start_task(StartTaskRequest(task=task_text, context=context))
                state.active_task_id = response.task.task_id
                state.task_started_at = self.clock()
                state.last_progress_spoken_at = None
                state.next_progress_check_at = state.task_started_at + timedelta(milliseconds=self.timing.first_progress_check_ms)
                state.progress_check_count = 0
                state.progress_update_count = 0
                state.last_progress_message = None
                state.last_task_status_seen = response.task.user_visible_status
                state.delivered_result_generation = None
                tool_call_id = f"call_{state.active_task_id}"
                await self._complete_tool_turn(
                    state,
                    user_text=text,
                    tool_name="start_task",
                    tool_call_id=tool_call_id,
                    tool_args=args,
                    tool_result=response.model_dump(mode="json"),
                    task_id=state.active_task_id,
                )
            except TaskConflictError as exc:
                conflict = self._codex_task_conflict_payload(state, str(args.get("task", text)))
                conflict["details"] = str(exc)
                await self._complete_tool_turn(
                    state,
                    user_text=text,
                    tool_name="start_task",
                    tool_call_id=tool_call_id,
                    tool_args=args,
                    tool_result=conflict,
                    task_id=state.active_task_id,
                )
            except Exception as exc:
                await self._emit_tool_failure_response(state, text, "start_task", tool_call_id, exc, args)

        elif tool_name == "amend_task":
            task_id = args.get("task_id")
            amendment = args.get("amendment")
            tool_call_id = f"call_{task_id or 'unknown'}_amend_{int(self.clock().timestamp() * 1000)}"
            if not task_id or not amendment:
                await self._emit_tool_failure_response(
                    state,
                    text,
                    "amend_task",
                    tool_call_id,
                    ValueError("Missing task_id or amendment for amend_task"),
                    args,
                )
                return
            try:
                status = await self.controller.amend_task(task_id, AmendTaskRequest(amendment=amendment))
                await self._complete_tool_turn(
                    state,
                    user_text=text,
                    tool_name="amend_task",
                    tool_call_id=tool_call_id,
                    tool_args=args,
                    tool_result=status.model_dump(mode="json"),
                    task_id=task_id,
                )
            except Exception as exc:
                await self._emit_tool_failure_response(state, text, "amend_task", tool_call_id, exc, args)

        elif tool_name == "cancel_task":
            task_id = args.get("task_id")
            tool_call_id = f"call_{task_id or 'unknown'}_cancel_{int(self.clock().timestamp() * 1000)}"
            if not task_id:
                await self._emit_tool_failure_response(
                    state,
                    text,
                    "cancel_task",
                    tool_call_id,
                    ValueError("Missing task_id for cancel_task"),
                    args,
                )
                return
            try:
                response = await self.controller.cancel_task(task_id, CancelTaskRequest(reason="User voice cancellation"))
                await self._complete_tool_turn(
                    state,
                    user_text=text,
                    tool_name="cancel_task",
                    tool_call_id=tool_call_id,
                    tool_args=args,
                    tool_result=response.model_dump(mode="json"),
                    task_id=task_id,
                )
                if state.active_task_id == task_id:
                    state.active_task_id = None
            except Exception as exc:
                await self._emit_tool_failure_response(state, text, "cancel_task", tool_call_id, exc, args)

        elif isinstance(tool_name, str) and tool_name in NATIVE_TOOL_NAMES:
            tool_call_id = f"call_{tool_name}_{int(self.clock().timestamp() * 1000)}"
            try:
                result = await self._execute_native_tool(state, tool_name, args)
            except Exception as exc:
                await self._emit_tool_failure_response(state, text, tool_name, tool_call_id, exc, args)
                return
            await self._complete_tool_turn(
                state,
                user_text=text,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_args=args,
                tool_result=result,
                task_id=state.active_task_id,
            )
                    
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
            state.utterance_open = False
            return
        segment_id = state.active_utterance.speech_segment_id
        frames = state.active_utterance.finalize()
        state.active_utterance = None
        state.finalizing_utterance = True
        try:
            if not frames or self.stt_provider is None:
                return
            if state.active_asr_stream is not None:
                stream = state.active_asr_stream
                state.active_asr_stream = None
                try:
                    await self._handle_transcript_event(state, await stream.commit(), authoritative=True)
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
            await self._handle_transcript_event(
                state,
                SttTranscriptEvent(
                    event=SttTranscriptEventType.FINAL,
                    text=transcript,
                    segment_id=segment_id,
                    confidence=result.confidence,
                    provider=result.provider,
                    metadata=result.metadata,
                ),
                authoritative=True,
            )
        finally:
            state.finalizing_utterance = False
            state.utterance_open = False

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

    async def _handle_transcript_event(
        self,
        state: VoiceSessionState,
        event: SttTranscriptEvent,
        *,
        authoritative: bool = False,
    ) -> None:
        if state.active_utterance and event.segment_id != state.active_utterance.speech_segment_id:
            if not authoritative:
                return
        transcript = event.text.strip()
        is_authoritative = authoritative or state.finalizing_utterance or not state.utterance_open

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
            if state.interrupt_investigation_active:
                self._update_active_vad_candidate(state, transcript, "partial", event.confidence)
                await self._maybe_resume_interrupt_investigation(state)
            return

        if event.event == SttTranscriptEventType.ERROR:
            await self._emit(state, VoiceEventType.STT_ERROR, transcript or "Transcription failed.")
            return

        if event.event == SttTranscriptEventType.FINAL:
            if not transcript:
                return
            if is_authoritative:
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
                if state.interrupt_investigation_active and state.post_vad_commit_pending:
                    state.post_vad_commit_text = transcript
                    state.interrupt_candidate = InterruptCandidate(
                        text=transcript,
                        source="commit_final",
                        last_active_vad_classification=state.interrupt_candidate.last_active_vad_classification,
                        last_stt_confidence=event.confidence,
                    )
                    return
                if candidate.status != "accepted":
                    return
                await self._apply_authoritative_user_transcript(state, transcript)
                return

            state.last_transcript = transcript
            await self._emit(
                state,
                VoiceEventType.TRANSCRIPT_FINAL,
                transcript,
                metadata={
                    "provider": event.provider,
                    "confidence": event.confidence,
                    "provisional": True,
                    **event.metadata,
                },
            )
            if state.interrupt_investigation_active:
                self._update_active_vad_candidate(state, transcript, "stream_final", event.confidence)
                await self._maybe_resume_interrupt_investigation(state)
            return

    async def _apply_authoritative_user_transcript(self, state: VoiceSessionState, transcript: str) -> None:
        state.pending_transcript = f"{state.pending_transcript} {transcript}".strip()
        state.pending_transcript_since = self.clock()
        if self._classify_commit_delay(state) == 0:
            await self._maybe_commit_turn(state)

    async def _control_loop(self, state: VoiceSessionState) -> None:
        interval = self.timing.control_loop_ms / 1000
        try:
            while not state.stopped:
                await asyncio.sleep(interval)
                await self._advance_turn_state(state)
                await self._advance_interrupt_investigation(state)
                await self._maybe_commit_turn(state)
                await self._poll_task_result(state)
                await self._maybe_deliver_timer_alert(state)
                await self._maybe_emit_progress(state)
                await self._maybe_deliver_result(state)
                await self._maybe_expire_idle_session(state)
                self._prune_delivery_records(state)
        except asyncio.CancelledError:
            raise

    async def _maybe_expire_idle_session(self, state: VoiceSessionState) -> None:
        if state.user_speaking or state.assistant_speaking or state.active_task_id:
            return
        last_activity = state.last_activity_at
        if last_activity is None:
            return
        idle_limit = timedelta(minutes=self._settings.voice_session_idle_timeout_minutes)
        if self.clock() - last_activity < idle_limit:
            return
        await self.stop_session(state.session_id)

    def _prune_delivery_records(self, state: VoiceSessionState) -> None:
        ttl = timedelta(minutes=self._settings.delivery_record_ttl_minutes)
        cutoff = self.clock() - ttl
        expired_ids = [
            delivery_id
            for delivery_id, record in state.delivery_records.items()
            if record.emitted_at < cutoff and record.status in {"spoken", "interrupted"}
        ]
        for delivery_id in expired_ids:
            state.delivery_records.pop(delivery_id, None)

    async def _poll_task_result(self, state: VoiceSessionState) -> None:
        await self._delivery_manager.poll_task_result(state)

    async def _maybe_emit_progress(self, state: VoiceSessionState) -> None:
        await self._delivery_manager.maybe_emit_progress(state)

    async def _maybe_deliver_timer_alert(self, state: VoiceSessionState) -> None:
        await self._delivery_manager.maybe_deliver_timer_alert(state)

    def _has_pending_timer_delivery(self, state: VoiceSessionState) -> bool:
        return self._delivery_manager.has_pending_timer_delivery(state)

    async def _maybe_deliver_result(self, state: VoiceSessionState) -> None:
        await self._delivery_manager.maybe_deliver_result(
            state,
            append_tool_result_history=self._append_tool_result_history,
        )

    async def _speak_user_turn_followup(self, state: VoiceSessionState, user_text: str) -> str:
        return await self._speak_from_state(state, USER_TURN_FOLLOWUP_INSTRUCTION, user_text)

    async def _speak_background_event(
        self,
        state: VoiceSessionState,
        user_text: str | None,
        instruction: str,
    ) -> str:
        return await self._speak_from_state(state, instruction, user_text)

    async def _speak_from_state(
        self,
        state: VoiceSessionState,
        instruction: str,
        user_text: str | None,
    ) -> str:
        speaker = getattr(self.llm_provider, "speak_from_state", None)
        if not callable(speaker):
            raise RuntimeError("speak_from_state is not available on llm_provider")
        try:
            return await speaker(
                instruction,
                user_text,
                self._build_system_state(state),
                state.conversation_history,
            )
        except Exception as exc:
            logging.error("speak_from_state failed: %s", exc)
            return BRAIN_GLITCH_MESSAGE

    def _tool_failure_payload(self, exc: Exception | str, tool_name: str) -> dict[str, Any]:
        message = str(exc)
        return {
            "ok": False,
            "message": message,
            "error_code": type(exc).__name__ if isinstance(exc, Exception) else "tool_failure",
            "recoverable": True,
            "tool_name": tool_name,
            "allowed_next_actions": [],
        }

    async def _complete_tool_turn(
        self,
        state: VoiceSessionState,
        *,
        user_text: str,
        tool_name: str,
        tool_call_id: str,
        tool_args: dict[str, Any],
        tool_result: dict[str, Any],
        task_id: str | None = None,
    ) -> str:
        self._append_history(state, "assistant", None, extra={
            "role": "assistant",
            "tool_calls": [{
                "id": tool_call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(tool_args),
                },
            }],
        })
        self._append_history(state, "tool", json.dumps(tool_result), extra={
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
        })
        response_text = await self._speak_user_turn_followup(state, user_text)
        self._append_history(state, "assistant", response_text)
        await self._emit(state, VoiceEventType.ASSISTANT_RESPONSE, response_text, task_id=task_id)
        return response_text

    async def _emit_durable_spoken_delivery(
        self,
        state: VoiceSessionState,
        *,
        instruction: str,
        user_text: str | None,
        event_type: VoiceEventType,
        task_id: str | None,
        delivery_id: str,
        delivery_kind: str,
        extra_metadata: dict[str, object] | None = None,
    ) -> str:
        return await self._delivery_manager.emit_durable_spoken_delivery(
            state,
            instruction=instruction,
            user_text=user_text,
            event_type=event_type,
            task_id=task_id,
            delivery_id=delivery_id,
            delivery_kind=delivery_kind,
            extra_metadata=extra_metadata,
        )

    async def _emit_tool_failure_response(
        self,
        state: VoiceSessionState,
        user_text: str,
        tool_name: str,
        tool_call_id: str,
        exc: Exception | str,
        args: dict[str, Any],
    ) -> None:
        payload = self._tool_failure_payload(exc, tool_name)
        await self._complete_tool_turn(
            state,
            user_text=user_text,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_args=args,
            tool_result=payload,
            task_id=state.active_task_id,
        )

    def _append_tool_result_history(self, state: VoiceSessionState, result: RuntimeResult) -> None:
        self._conversation_history.append_tool_result_history(state, result)

    def _append_history(
        self,
        state: VoiceSessionState,
        role: str,
        content: str | None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self._conversation_history.append_history(state, role, content, extra)

    def _iso_now(self) -> str:
        return self._conversation_history.iso_now()

    def _build_system_state(self, state: VoiceSessionState) -> str:
        return self._conversation_history.build_system_state(state)

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
                "can_amend_active_codex_task": active_task is not None and bool(active_task.get("amendable")),
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
        return self._delivery_manager.create_delivery_record(
            state,
            delivery_id=delivery_id,
            kind=kind,
            task_id=task_id,
            generation=generation,
            text=text,
            original_request=original_request,
        )

    def _mark_delivery_speaking(
        self,
        state: VoiceSessionState,
        metadata: dict[str, Any] | AssistantSpeechStartedMetadata,
    ) -> None:
        delivery_id = metadata.delivery_id if isinstance(metadata, AssistantSpeechStartedMetadata) else metadata.get("delivery_id")
        if not delivery_id:
            utterance_id = metadata.utterance_id if isinstance(metadata, AssistantSpeechStartedMetadata) else metadata.get("utterance_id")
            delivery_id = utterance_id
        if not delivery_id:
            return
        record = state.delivery_records.get(str(delivery_id))
        if record and record.status != "spoken":
            record.status = "speaking"

    def _mark_delivery_speech_ended(
        self,
        state: VoiceSessionState,
        metadata: dict[str, Any] | AssistantSpeechEndedMetadata,
    ) -> None:
        delivery_id = metadata.delivery_id if isinstance(metadata, AssistantSpeechEndedMetadata) else metadata.get("delivery_id")
        if not delivery_id:
            utterance_id = metadata.utterance_id if isinstance(metadata, AssistantSpeechEndedMetadata) else metadata.get("utterance_id")
            delivery_id = utterance_id
        if not delivery_id:
            return
        record = state.delivery_records.get(str(delivery_id))
        if record is None:
            return
        completed = metadata.completed if isinstance(metadata, AssistantSpeechEndedMetadata) else metadata.get("completed")
        if completed is True:
            record.status = "spoken"
            record.reason = None
            record.spoken_at = self.clock()
            return
        reason = metadata.reason if isinstance(metadata, AssistantSpeechEndedMetadata) else metadata.get("reason")
        record.status = "interrupted"
        record.reason = str(reason or "interrupted")

    def _delivery_payload(self, record: DeliveryRecord) -> dict[str, Any]:
        return self._delivery_manager.delivery_payload(record)

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
            f"{task.original_request}. If this is a change to that task, I can add it; otherwise I can cancel it and start the new one, or keep this one going while we handle simpler things."
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
            "allowed_next_actions": [
                "amend_active_task",
                "cancel_active_task",
                "keep_active_task",
                "use_native_tool",
                "answer_directly",
            ],
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
            "elapsed_time_human": format_ms_to_human(elapsed_ms),
            "remaining_ms": remaining_ms,
            "remaining_time_human": format_ms_to_human(remaining_ms),
            "status": timer.status,
            "reason": timer.reason,
            "ui_title": timer.ui_title,
        }

    def _activity_payload(self, activity: SessionActivity, now: datetime) -> dict[str, Any]:
        elapsed_ms = max(0, int((now - activity.started_at) / timedelta(milliseconds=1)))
        return {
            "activity_id": activity.activity_id,
            "type": activity.activity_type,
            "label": activity.label,
            "started_at": activity.started_at.astimezone().isoformat(),
            "elapsed_ms": elapsed_ms,
            "elapsed_time_human": format_ms_to_human(elapsed_ms),
            "target_duration_ms": activity.target_duration_ms,
            "target_distance_meters": activity.target_distance_meters,
            "status": activity.status,
            "ui_title": activity.ui_title,
        }

    def _task_payload(self, state: VoiceSessionState, task: Any, now: datetime) -> dict[str, Any]:
        elapsed_ms = self._datetime_delta_ms(now, state.task_started_at or task.created_at)
        last_progress_ms_ago = self._datetime_delta_ms(now, task.last_update_at)
        return {
            "task_id": task.task_id,
            "type": "codex",
            "status": task.status.value,
            "original_request": task.original_request,
            "user_visible_status": task.user_visible_status,
            "amendable": bool(getattr(task, "amendable", False)),
            "elapsed_ms": max(0, elapsed_ms),
            "last_meaningful_progress_ms_ago": max(0, last_progress_ms_ago),
            "last_spoken_update_ms_ago": int(self._elapsed_ms(state.last_progress_spoken_at)) if state.last_progress_spoken_at else None,
            "progress_update_count": state.progress_update_count,
            "ui_title": getattr(task, "ui_title", None),
        }

    async def _execute_native_tool(self, state: VoiceSessionState, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        return await self._native_tool_executor.execute(state, tool_name, args)

    def _record_user_claim(self, state: VoiceSessionState, text: str) -> None:
        normalized = normalize_turn_text(text)
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
        return self._conversation_history.build_task_context(state, current_user_text)

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
            if metadata.get("durable") is True:
                DurableDeliveryMetadata.model_validate(metadata)
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
        state.playback_paused = False
        state.pause_resume_timer_expires_at = None
        self._clear_interrupt_investigation(state)
        state.voice_state = VoiceTurnState.USER_SPEAKING if state.user_speaking else VoiceTurnState.LISTENING
        state.post_tts_cooldown_expires_at = None
        await self._emit(state, VoiceEventType.STOP_ASSISTANT_AUDIO, "Stopping assistant audio.")

    async def _start_interrupt_investigation(self, state: VoiceSessionState) -> None:
        if state.interrupt_investigation_active:
            return
        now = self.clock()
        state.interrupt_investigation_active = True
        state.assistant_audio_paused_for_interrupt = True
        state.playback_paused = True
        state.non_echo_speech_detected_during_investigation = False
        state.echo_like_detected_at = None
        state.transcript_seen_during_investigation = False
        state.interrupt_candidate = InterruptCandidate()
        state.vad_investigation_expires_at = now + timedelta(milliseconds=self.timing.vad_investigation_max_ms)
        state.post_vad_commit_pending = False
        state.post_vad_commit_text = ""
        state.post_vad_buffer_expires_at = None
        await self._emit(state, VoiceEventType.PAUSE_ASSISTANT_AUDIO, "Pausing assistant audio for interrupt investigation.")

    def _clear_interrupt_investigation(self, state: VoiceSessionState) -> None:
        state.interrupt_investigation_active = False
        state.assistant_audio_paused_for_interrupt = False
        state.playback_paused = False
        state.non_echo_speech_detected_during_investigation = False
        state.echo_like_detected_at = None
        state.transcript_seen_during_investigation = False
        state.vad_investigation_expires_at = None
        state.post_vad_buffer_expires_at = None
        state.post_vad_commit_pending = False
        state.post_vad_commit_text = ""
        state.interrupt_candidate = InterruptCandidate()
        state.pause_resume_timer_expires_at = None

    def _classify_active_vad_text(
        self,
        state: VoiceSessionState,
        text: str,
        stt_confidence: float | None,
    ) -> ActiveVadClassification:
        return self._interrupt_manager.classify_active_vad_text(state, text, stt_confidence)

    def _classify_post_vad_commit(self, state: VoiceSessionState, text: str) -> PostVadClassification:
        return self._interrupt_manager.classify_post_vad_commit(state, text)

    def _update_active_vad_candidate(
        self,
        state: VoiceSessionState,
        text: str,
        source: str,
        confidence: float | None,
    ) -> None:
        classification = self._classify_active_vad_text(state, text, confidence)
        state.interrupt_candidate = InterruptCandidate(
            text=text,
            source=source,
            last_active_vad_classification=classification,
            last_stt_confidence=confidence,
        )
        if text.strip():
            state.transcript_seen_during_investigation = True
        if classification == ActiveVadClassification.ECHO_LIKE:
            if state.echo_like_detected_at is None:
                state.echo_like_detected_at = self.clock()
        elif classification == ActiveVadClassification.DIVERGENT_TEXT:
            state.non_echo_speech_detected_during_investigation = True
            state.echo_like_detected_at = None

    def _interrupt_resume_allowed(self, state: VoiceSessionState) -> bool:
        if state.non_echo_speech_detected_during_investigation:
            return False
        if not state.transcript_seen_during_investigation:
            return False
        if state.echo_like_detected_at is None:
            return False
        return self.clock() >= state.echo_like_detected_at + timedelta(milliseconds=self.timing.echo_validation_ms)

    async def _maybe_resume_interrupt_investigation(self, state: VoiceSessionState) -> None:
        if not state.interrupt_investigation_active or state.post_vad_commit_pending:
            return
        if state.non_echo_speech_detected_during_investigation:
            return
        now = self.clock()
        if not state.transcript_seen_during_investigation:
            if state.vad_investigation_expires_at and now >= state.vad_investigation_expires_at:
                await self._resume_interrupt_investigation(state)
            return
        if self._interrupt_resume_allowed(state):
            await self._resume_interrupt_investigation(state)

    async def _resume_interrupt_investigation(self, state: VoiceSessionState) -> None:
        if not state.assistant_audio_paused_for_interrupt:
            self._clear_interrupt_investigation(state)
            return
        await self._emit(state, VoiceEventType.RESUME_ASSISTANT_AUDIO, "Resuming assistant audio (false alarm).")
        self._clear_interrupt_investigation(state)
        if state.user_speaking:
            state.voice_state = VoiceTurnState.BARGE_IN_CANDIDATE if state.assistant_speaking else VoiceTurnState.USER_SPEAKING
        elif state.assistant_speaking:
            state.voice_state = VoiceTurnState.ASSISTANT_SPEAKING

    async def _resolve_post_vad_interrupt(self, state: VoiceSessionState) -> None:
        text = state.post_vad_commit_text.strip()
        classification = self._classify_post_vad_commit(state, text)
        state.post_vad_commit_pending = False
        state.post_vad_commit_text = ""
        state.post_vad_buffer_expires_at = None
        if classification == PostVadClassification.GENUINE:
            await self._stop_assistant_audio(state)
            if text:
                await self._apply_authoritative_user_transcript(state, text)
            return
        await self._resume_interrupt_investigation(state)

    async def _advance_interrupt_investigation(self, state: VoiceSessionState) -> None:
        if not state.interrupt_investigation_active:
            return
        await self._maybe_resume_interrupt_investigation(state)
        if state.post_vad_commit_pending and state.post_vad_buffer_expires_at:
            if self.clock() >= state.post_vad_buffer_expires_at:
                await self._resolve_post_vad_interrupt(state)

    async def _advance_turn_state(self, state: VoiceSessionState) -> None:
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
        normalized = normalize_turn_text(text)
        if not normalized:
            return False, "empty_transcript"

        recent = self._assistant_recently_spoke(state)
        during_assistant = state.assistant_speaking if during_assistant is None else during_assistant
        during_cooldown = self._in_post_tts_cooldown(state) if during_cooldown is None else during_cooldown
        echo_reason = self._assistant_echo_reason(state, normalized)
        echo = echo_reason is not None
        if is_hard_interrupt_command(normalized):
            return (False, echo_reason or "matches_recent_assistant_text") if echo else (True, "hard_interrupt_command")

        if recent and is_soft_amendment_marker_only(normalized):
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
            reason = InterruptManager.assistant_echo_match_reason(
                normalized_text,
                normalize_turn_text(assistant_text),
                self.timing,
            )
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
