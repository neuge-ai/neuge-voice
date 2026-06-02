from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from nextgen_voice_agent.agent.controller import AgentController
from nextgen_voice_agent.models.runtime import RuntimeResult, RuntimeResultStatus
from nextgen_voice_agent.models.task import TaskStatus
from nextgen_voice_agent.models.voice import VoiceEventType

from nextgen_voice_agent.voice.session_entities import DeliveryRecord

if TYPE_CHECKING:
    from nextgen_voice_agent.voice.orchestrator import VoiceSessionState, VoiceTimingConfig

BACKGROUND_RESEARCH_INSTRUCTION = "Background research finished; result is in history. Say the reply."
BACKGROUND_TIMER_INSTRUCTION = "Timer completed; facts are in state/history. Say the reply."


@dataclass(frozen=True)
class DeliveryManagerCallbacks:
    speak_background_event: Callable[..., Awaitable[str]]
    append_history: Callable[..., None]
    emit: Callable[..., Awaitable[None]]
    iso_now: Callable[[], str]
    elapsed_ms: Callable[[datetime], float]


class DeliveryManager:
    """Queued Codex results, timer alerts, and durable spoken delivery."""

    def __init__(
        self,
        *,
        controller: AgentController,
        clock: Callable[[], datetime],
        timing: VoiceTimingConfig,
        callbacks: DeliveryManagerCallbacks,
    ) -> None:
        self._controller = controller
        self._clock = clock
        self._timing = timing
        self._callbacks = callbacks

    async def poll_task_result(self, state: VoiceSessionState) -> None:
        if state.active_task_id is None or state.queued_result is not None:
            return
        result = self._controller.read_result(state.active_task_id)
        if result is None:
            return
        if result.status in {RuntimeResultStatus.CANCELLED, RuntimeResultStatus.STALE}:
            return
        state.queued_result = result

    async def maybe_emit_progress(self, state: VoiceSessionState) -> None:
        if self.has_pending_timer_delivery(state):
            return
        if state.active_task_id is None or state.queued_result is not None or state.task_started_at is None:
            return
        task = self._controller.tasks.get(state.active_task_id)
        if task is None or task.status not in {TaskStatus.RUNNING, TaskStatus.AMENDING, TaskStatus.WAITING_FOR_APPROVAL}:
            return
        if state.user_speaking or state.assistant_speaking:
            return
        now = self._clock()
        meaningful_progress = task.user_visible_status != state.last_task_status_seen
        if meaningful_progress:
            state.last_task_status_seen = task.user_visible_status
        if state.next_progress_check_at is not None and now < state.next_progress_check_at:
            return
        state.progress_check_count += 1
        state.next_progress_check_at = now + timedelta(milliseconds=self._normalize_next_progress_check(None))

    async def maybe_deliver_timer_alert(self, state: VoiceSessionState) -> None:
        if state.user_speaking or state.assistant_speaking:
            return
        record = self._next_pending_timer_delivery(state)
        if record is None:
            return
        answer = await self.emit_durable_spoken_delivery(
            state,
            instruction=BACKGROUND_TIMER_INSTRUCTION,
            user_text=record.original_request,
            event_type=VoiceEventType.ASSISTANT_RESPONSE,
            task_id=None,
            delivery_id=record.delivery_id,
            delivery_kind=record.kind,
        )
        record.status = "speaking"
        record.text = answer

    async def maybe_deliver_result(
        self,
        state: VoiceSessionState,
        *,
        append_tool_result_history: Callable[[VoiceSessionState, RuntimeResult], None],
    ) -> None:
        result = state.queued_result
        if result is None or state.user_speaking or state.assistant_speaking:
            return
        if self._timing.assistant_ack_timeout_ms > 0 and state.recent_assistant_utterances:
            last_emitted_at, _ = state.recent_assistant_utterances[-1]
            if state.assistant_speech_started_at is None or state.assistant_speech_started_at < last_emitted_at:
                elapsed = self._callbacks.elapsed_ms(last_emitted_at)
                if elapsed < self._timing.assistant_ack_timeout_ms:
                    return
        if state.delivered_result_generation == result.generation:
            return
        append_tool_result_history(state, result)
        task = self._controller.tasks.get(result.task_id)
        user_text = task.original_request if task else result.task_id
        delivery = self.create_delivery_record(
            state,
            delivery_id=f"delivery_{result.task_id}_{result.generation}",
            kind="background_task",
            task_id=result.task_id,
            generation=result.generation,
            text="",
            original_request=task.original_request if task else result.task_id,
        )
        answer = await self.emit_durable_spoken_delivery(
            state,
            instruction=BACKGROUND_RESEARCH_INSTRUCTION,
            user_text=user_text,
            event_type=VoiceEventType.DELIVERY_READY,
            task_id=result.task_id,
            delivery_id=delivery.delivery_id,
            delivery_kind=delivery.kind,
        )
        delivery.text = answer
        state.delivered_result_generation = result.generation
        state.recent_completed_events.append({
            "type": "background_task",
            "task_id": result.task_id,
            "status": result.status.value,
            "completed_at": self._callbacks.iso_now(),
            "delivery_id": delivery.delivery_id,
            "delivery_status": delivery.status,
        })
        state.recent_completed_events = state.recent_completed_events[-10:]
        state.queued_result = None
        state.active_task_id = None

    async def emit_durable_spoken_delivery(
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
        spoken_text = await self._callbacks.speak_background_event(state, user_text, instruction)
        self._callbacks.append_history(state, "assistant", spoken_text)
        metadata: dict[str, object] = {
            "durable": True,
            "delivery_id": delivery_id,
            "utterance_id": delivery_id,
            "event_type": event_type.value,
            "delivery_kind": delivery_kind,
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        await self._callbacks.emit(state, event_type, spoken_text, task_id=task_id, metadata=metadata)
        return spoken_text

    def create_delivery_record(
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
            emitted_at=self._clock(),
        )
        state.delivery_records[delivery_id] = record
        return record

    def delivery_payload(self, record: DeliveryRecord) -> dict[str, Any]:
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

    def has_pending_timer_delivery(self, state: VoiceSessionState) -> bool:
        return self._next_pending_timer_delivery(state) is not None

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

    def _normalize_next_progress_check(self, next_check_ms: object) -> int:
        if isinstance(next_check_ms, int | float) and next_check_ms > 0:
            return int(next_check_ms)
        if self._timing.repeated_progress_check_max_ms <= self._timing.repeated_progress_check_min_ms:
            return self._timing.repeated_progress_check_min_ms
        return random.randint(self._timing.repeated_progress_check_min_ms, self._timing.repeated_progress_check_max_ms)
