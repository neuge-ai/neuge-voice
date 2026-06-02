from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable

from nextgen_voice_agent.voice.turn_text import is_hard_interrupt_command, normalize_turn_text

if TYPE_CHECKING:
    from nextgen_voice_agent.voice.orchestrator import VoiceSessionState, VoiceTimingConfig


class TurnCommitter:
    """Pending transcript commit timing (user turn routing stays in orchestrator)."""

    def __init__(
        self,
        *,
        timing: VoiceTimingConfig,
        clock: Callable,
        elapsed_ms: Callable,
        handle_user_turn: Callable[..., Awaitable[None]],
    ) -> None:
        self._timing = timing
        self._clock = clock
        self._elapsed_ms = elapsed_ms
        self._handle_user_turn = handle_user_turn

    async def maybe_commit_turn(self, state: VoiceSessionState) -> None:
        if not state.pending_transcript or state.pending_transcript_since is None:
            return
        elapsed_ms = self._elapsed_ms(state.pending_transcript_since)
        if state.user_speaking:
            return
        delay = self.classify_commit_delay(state)
        if elapsed_ms >= delay:
            text = state.pending_transcript.strip()
            state.pending_transcript = ""
            state.pending_transcript_since = None
            if text:
                await self._handle_user_turn(state, text)

    def classify_commit_delay(self, state: VoiceSessionState) -> int:
        normalized = normalize_turn_text(state.pending_transcript)
        if not normalized:
            return self._timing.turn_commit_default_wait_ms
        if is_hard_interrupt_command(normalized):
            return self._timing.turn_commit_hard_command_wait_ms
        if state.active_task_id:
            return self._timing.turn_commit_active_task_wait_ms
        return self._timing.turn_commit_default_wait_ms
