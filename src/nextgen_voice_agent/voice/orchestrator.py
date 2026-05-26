from __future__ import annotations

import asyncio
import json
import logging
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
            state.post_tts_cooldown_expires_at = None
            return

        if event.event == VoiceEventType.ASSISTANT_SPEECH_ENDED:
            now = self.clock()
            state.assistant_speaking = False
            state.last_assistant_speech_at = now
            state.assistant_speech_ended_at = now
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
        state.conversation_history.append({"role": "user", "content": text})
        
        sys_state = []
        if state.active_task_id:
            sys_state.append(f"Active Task ID: {state.active_task_id}")
            status = self.controller.get_status(state.active_task_id)
            sys_state.append(f"Active Task Status: {status.task.status.value}")
            sys_state.append(f"Active Task Progress: {status.progress or status.task.user_visible_status}")
        else:
            sys_state.append("Active Task ID: None")
            
        system_state_str = "\n".join(sys_state)
        
        decision = await self.llm_provider.route_turn(text, system_state_str, state.conversation_history[:-1])
        decision_type = decision.get("type")
        if decision_type == "assistant_response":
            response_text = decision.get("response") or "I understand."
            state.conversation_history.append({"role": "assistant", "content": response_text})
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
                context = "Conversation History:\n" + "\n".join(
                    [f"{msg['role'].capitalize()}: {msg.get('content', str(msg))}" for msg in state.conversation_history]
                )
                response = await self.controller.start_task(StartTaskRequest(task=task_text, context=context))
                
                state.active_task_id = response.task.task_id
                state.task_started_at = self.clock()
                state.first_thinking_ack_sent = False
                state.first_tool_status_sent = False
                state.last_progress_spoken_at = None
                state.delivered_result_generation = None
                
                state.conversation_history.append({
                    "role": "assistant", 
                    "tool_calls": [{"id": f"call_{state.active_task_id}", "type": "function", "function": {"name": "start_task", "arguments": json.dumps(args)}}]
                })
                state.conversation_history.append({
                    "role": "tool",
                    "tool_call_id": f"call_{state.active_task_id}",
                    "name": "start_task",
                    "content": f"Started Task ID: {state.active_task_id}"
                })
                state.conversation_history.append({"role": "assistant", "content": assistant_response})
                
                await self._emit(state, VoiceEventType.ASSISTANT_RESPONSE, assistant_response, task_id=state.active_task_id)
            except TaskConflictError as exc:
                await self._emit(state, VoiceEventType.ERROR, str(exc))
                
        elif tool_name == "amend_task":
            task_id = args.get("task_id")
            amendment = args.get("amendment")
            if task_id and amendment:
                try:
                    await self.controller.amend_task(task_id, AmendTaskRequest(amendment=amendment))
                    msg = f"I am amending task {task_id}."
                    state.conversation_history.append({
                        "role": "assistant", 
                        "tool_calls": [{"id": f"call_{task_id}_amend", "type": "function", "function": {"name": "amend_task", "arguments": json.dumps(args)}}]
                    })
                    state.conversation_history.append({
                        "role": "tool",
                        "tool_call_id": f"call_{task_id}_amend",
                        "name": "amend_task",
                        "content": f"Amended Task ID: {task_id}"
                    })
                    state.conversation_history.append({"role": "assistant", "content": msg})
                    await self._emit(state, VoiceEventType.ASSISTANT_RESPONSE, msg, task_id=task_id)
                except Exception as exc:
                    await self._emit(state, VoiceEventType.ERROR, str(exc))
            else:
                await self._emit(state, VoiceEventType.ERROR, "Missing task_id or amendment for amend_task")
                
        elif tool_name == "cancel_task":
            task_id = args.get("task_id")
            if task_id:
                try:
                    await self.controller.cancel_task(task_id, CancelTaskRequest(reason="User voice cancellation"))
                    msg = f"I stopped task {task_id}."
                    state.conversation_history.append({
                        "role": "assistant", 
                        "tool_calls": [{"id": f"call_{task_id}_cancel", "type": "function", "function": {"name": "cancel_task", "arguments": json.dumps(args)}}]
                    })
                    state.conversation_history.append({
                        "role": "tool",
                        "tool_call_id": f"call_{task_id}_cancel",
                        "name": "cancel_task",
                        "content": f"Cancelled Task ID: {task_id}"
                    })
                    state.conversation_history.append({"role": "assistant", "content": msg})
                    await self._emit(state, VoiceEventType.ASSISTANT_RESPONSE, msg, task_id=task_id)
                    if state.active_task_id == task_id:
                        state.active_task_id = None
                except Exception as exc:
                    await self._emit(state, VoiceEventType.ERROR, str(exc))
                    
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
            await self.controller.cancel_task(task_id, CancelTaskRequest(reason=f"Partial voice cancellation: {transcript}"))
            await self._emit(state, VoiceEventType.ASSISTANT_RESPONSE, "I stopped that task.", task_id=task_id)
            state.active_task_id = None

    async def _control_loop(self, state: VoiceSessionState) -> None:
        interval = self.timing.control_loop_ms / 1000
        try:
            while not state.stopped:
                await asyncio.sleep(interval)
                self._advance_turn_state(state)
                await self._maybe_commit_turn(state)
                await self._poll_task_result(state)
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
        if state.active_task_id is None or state.queued_result is not None or state.task_started_at is None:
            return

        task = self.controller.tasks.get(state.active_task_id)
        if task is None or task.status not in {TaskStatus.RUNNING, TaskStatus.AMENDING, TaskStatus.WAITING_FOR_APPROVAL}:
            return

        elapsed_ms = self._elapsed_ms(state.task_started_at)
        if not state.first_thinking_ack_sent and elapsed_ms >= self.timing.first_thinking_ack_ms:
            state.first_thinking_ack_sent = True
            state.last_progress_spoken_at = self.clock()
            await self._emit(state, VoiceEventType.TASK_STATUS, "I am working on it.", task_id=state.active_task_id)
            return

        if not state.first_tool_status_sent and elapsed_ms >= self.timing.first_tool_status_ms:
            state.first_tool_status_sent = True
            state.last_progress_spoken_at = self.clock()
            await self._emit(state, VoiceEventType.TASK_STATUS, task.user_visible_status, task_id=state.active_task_id)
            return

        if state.last_progress_spoken_at and self._elapsed_ms(state.last_progress_spoken_at) >= self.timing.repeated_status_interval_ms:
            state.last_progress_spoken_at = self.clock()
            await self._emit(state, VoiceEventType.TASK_STATUS, task.user_visible_status, task_id=state.active_task_id)

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
        state.conversation_history.append({"role": "assistant", "content": answer})
        await self._emit(state, VoiceEventType.DELIVERY_READY, answer, task_id=result.task_id)
        state.delivered_result_generation = result.generation
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

    def _append_tool_result_history(self, state: VoiceSessionState, result: RuntimeResult) -> None:
        tool_call_id = f"result_{result.task_id}"
        if any(msg.get("role") == "tool" and msg.get("tool_call_id") == tool_call_id for msg in state.conversation_history):
            return
        state.conversation_history.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": "start_task",
            "content": json.dumps(result.model_dump(mode="json")),
        })

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
