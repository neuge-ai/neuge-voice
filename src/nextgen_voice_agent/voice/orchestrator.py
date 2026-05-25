from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Callable

from nextgen_voice_agent.agent.controller import AgentController, TaskConflictError
from nextgen_voice_agent.models.runtime import RuntimeResult, RuntimeResultStatus
from nextgen_voice_agent.models.task import CancelTaskRequest, StartTaskRequest, TaskStatus
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

HARD_INTERRUPT_COMMANDS = {"stop", "cancel", "pause", "wait", "hold on", "never mind", "nevermind"}
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
    recent_assistant_utterances: list[tuple[datetime, str]] = field(default_factory=list)
    outbound: asyncio.Queue[VoiceEvent] = field(default_factory=asyncio.Queue)
    loop_task: asyncio.Task[None] | None = None


class VoiceSessionOrchestrator:
    def __init__(
        self,
        controller: AgentController,
        stt_provider: SttProvider | None = None,
        requested_asr_mode: AsrMode = AsrMode.SPEECH_GATED_STREAMING,
        timing: VoiceTimingConfig | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.controller = controller
        self.stt_provider = stt_provider
        self.requested_asr_mode = requested_asr_mode
        self.effective_asr_mode = self._resolve_asr_mode()
        self.timing = timing or VoiceTimingConfig()
        self.clock = clock or datetime.now
        self.sessions: dict[str, VoiceSessionState] = {}

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

    async def handle_voice_event(self, session_id: str, event: VoiceEvent) -> list[VoiceEvent]:
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
            return await self.drain_events(session_id)

        if event.event == VoiceEventType.USER_TURN_AUDIO:
            await self._handle_audio_frame(state, event)
            return await self.drain_events(session_id)

        if event.event == VoiceEventType.SPEECH_ENDED:
            state.user_speaking = False
            state.candidate_speech_ended_at = self.clock()
            await self._finalize_utterance(state)
            if state.voice_state == VoiceTurnState.USER_SPEAKING:
                state.voice_state = VoiceTurnState.LISTENING
            elif state.voice_state == VoiceTurnState.BARGE_IN_CANDIDATE and state.assistant_speaking:
                state.voice_state = VoiceTurnState.ASSISTANT_SPEAKING
            return await self.drain_events(session_id)

        if event.event == VoiceEventType.INTERRUPTION:
            state.user_speaking = True
            if state.assistant_speaking:
                state.voice_state = VoiceTurnState.BARGE_IN_CANDIDATE
                if self._candidate_speech_duration_ms(state) >= self.timing.barge_in_min_speech_ms:
                    await self._stop_assistant_audio(state)
            return await self.drain_events(session_id)

        if event.event == VoiceEventType.ASSISTANT_SPEECH_STARTED:
            now = self.clock()
            state.assistant_speaking = True
            state.voice_state = VoiceTurnState.ASSISTANT_SPEAKING
            state.last_assistant_speech_at = now
            state.assistant_speech_started_at = now
            state.post_tts_cooldown_expires_at = None
            return await self.drain_events(session_id)

        if event.event == VoiceEventType.ASSISTANT_SPEECH_ENDED:
            now = self.clock()
            state.assistant_speaking = False
            state.last_assistant_speech_at = now
            state.assistant_speech_ended_at = now
            state.post_tts_cooldown_expires_at = now + timedelta(milliseconds=self.timing.post_tts_cooldown_ms)
            state.voice_state = VoiceTurnState.POST_TTS_COOLDOWN
            return await self.drain_events(session_id)

        if event.event in {VoiceEventType.IDLE_STATE, VoiceEventType.CLIENT_IDLE}:
            state.user_speaking = False
            state.assistant_speaking = False
            state.voice_state = VoiceTurnState.LISTENING
            state.post_tts_cooldown_expires_at = None
            return await self.drain_events(session_id)

        if event.event == VoiceEventType.USER_TURN and event.text:
            await self._handle_user_turn(state, event.text)
            return await self.drain_events(session_id)

        return await self.drain_events(session_id)

    async def get_transcript(self, session_id: str) -> dict[str, str | None]:
        state = self.sessions.get(session_id)
        if state is None:
            return {"session_id": session_id, "last_transcript": None, "last_partial_transcript": None}
        return {
            "session_id": session_id,
            "last_transcript": state.last_transcript,
            "last_partial_transcript": state.last_partial_transcript,
        }

    async def handle_model_event(self, session_id: str, event: VoiceEvent) -> list[VoiceEvent]:
        state = await self.start_session(session_id, event.transport)
        if event.event == VoiceEventType.ASSISTANT_RESPONSE:
            now = self.clock()
            state.assistant_speaking = True
            state.voice_state = VoiceTurnState.ASSISTANT_SPEAKING
            state.last_assistant_speech_at = now
            state.assistant_speech_started_at = now
        return await self.drain_events(session_id)

    async def drain_events(self, session_id: str) -> list[VoiceEvent]:
        state = self.sessions[session_id]
        events: list[VoiceEvent] = []
        while not state.outbound.empty():
            events.append(state.outbound.get_nowait())
        return events

    async def _handle_user_turn(self, state: VoiceSessionState, text: str) -> None:
        state.last_user_turn_at = self.clock()
        normalized = text.strip().lower()

        if state.active_task_id and any(phrase in normalized for phrase in ["cancel", "stop", "forget that"]):
            await self.controller.cancel_task(state.active_task_id, CancelTaskRequest(reason=text))
            await self._emit(state, VoiceEventType.ASSISTANT_RESPONSE, "I stopped that task.", task_id=state.active_task_id)
            state.active_task_id = None
            return

        if state.active_task_id and any(phrase in normalized for phrase in ["status", "how is", "progress"]):
            status = self.controller.get_status(state.active_task_id)
            await self._emit(state, VoiceEventType.TASK_STATUS, status.progress or status.task.user_visible_status)
            return

        if state.active_task_id:
            await self._emit(
                state,
                VoiceEventType.ASSISTANT_RESPONSE,
                "A task is already running. Say cancel if you want me to stop it first.",
                task_id=state.active_task_id,
            )
            return

        try:
            response = await self.controller.start_task(StartTaskRequest(task=text))
        except TaskConflictError as exc:
            await self._emit(state, VoiceEventType.ERROR, str(exc))
            return

        state.active_task_id = response.task.task_id
        state.task_started_at = self.clock()
        state.first_thinking_ack_sent = False
        state.first_tool_status_sent = False
        state.last_progress_spoken_at = None
        state.delivered_result_generation = None
        await self._emit(state, VoiceEventType.ASSISTANT_RESPONSE, response.acknowledgement, task_id=response.task.task_id)

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
            except Exception:
                await self._emit(
                    state,
                    VoiceEventType.STT_ERROR,
                    "Streaming transcription failed. Falling back to buffered transcription.",
                    metadata={"fallback": "utterance_batch"},
                )
        try:
            result = await self.stt_provider.transcribe_utterance(frames)
        except Exception:
            await self._emit(state, VoiceEventType.STT_ERROR, "I missed that. Could you say it again?")
            return
        transcript = result.text.strip()
        if not transcript:
            return
        state.last_transcript = transcript
        candidate = self._validate_asr_candidate(
            state,
            transcript,
            state.active_utterance.speech_segment_id if state.active_utterance else "unknown",
        )
        await self._emit_transcript_final(state, transcript, {"provider": result.provider, **result.metadata}, candidate)
        if candidate.status != "accepted":
            return
        if candidate.during_assistant_speech or candidate.during_cooldown:
            await self._stop_assistant_audio(state)
        await self._handle_user_turn(state, transcript)

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
            await self._handle_user_turn(state, transcript)

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
        if state.delivered_result_generation == result.generation:
            return

        intro = "I have the result now. " if result.status == RuntimeResultStatus.COMPLETED else ""
        answer = result.spoken_answer or result.error or "The task finished without a spoken answer."
        await self._emit(state, VoiceEventType.DELIVERY_READY, f"{intro}{answer}", task_id=result.task_id)
        state.delivered_result_generation = result.generation
        state.queued_result = None
        state.active_task_id = None

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
        await state.outbound.put(
            VoiceEvent(
                event=event_type,
                transport=state.transport,
                session_id=state.session_id,
                text=text,
                task_id=task_id,
                metadata=event_metadata,
            )
        )
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
