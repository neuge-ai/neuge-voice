"""Barge-in and echo classification.

InterruptManager uses injected callbacks for emit, stop/resume audio, and applying
authoritative transcripts so it never imports VoiceSessionOrchestrator (no import cycles).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Awaitable, Callable

from nextgen_voice_agent.voice.interrupt_types import ActiveVadClassification, PostVadClassification
from nextgen_voice_agent.voice.turn_text import is_hard_interrupt_command, normalize_turn_text

if TYPE_CHECKING:
    from nextgen_voice_agent.voice.orchestrator import (
        VoiceSessionState,
        VoiceTimingConfig,
    )


@dataclass(frozen=True)
class InterruptManagerCallbacks:
    emit: Callable[..., Awaitable[None]]
    stop_assistant_audio: Callable[..., Awaitable[None]]
    apply_authoritative_user_transcript: Callable[..., Awaitable[None]]


class InterruptManager:
    def __init__(
        self,
        *,
        timing: VoiceTimingConfig,
        clock: Callable,
        callbacks: InterruptManagerCallbacks,
        assistant_echo_reason: Callable[[VoiceSessionState, str], str | None],
    ) -> None:
        self._timing = timing
        self._clock = clock
        self._callbacks = callbacks
        self._assistant_echo_reason = assistant_echo_reason

    def classify_active_vad_text(
        self,
        state: VoiceSessionState,
        text: str,
        stt_confidence: float | None,
    ) -> ActiveVadClassification:
        del stt_confidence
        if not text.strip():
            return ActiveVadClassification.NO_TRANSCRIPT
        normalized = normalize_turn_text(text)
        if self._assistant_echo_reason(state, normalized):
            return ActiveVadClassification.ECHO_LIKE
        return ActiveVadClassification.DIVERGENT_TEXT

    def classify_post_vad_commit(self, state: VoiceSessionState, text: str) -> PostVadClassification:
        normalized = normalize_turn_text(text)
        if not normalized:
            return PostVadClassification.NO_TRANSCRIPT
        if self._assistant_echo_reason(state, normalized):
            return PostVadClassification.ECHO
        if is_hard_interrupt_command(normalized):
            return PostVadClassification.GENUINE
        if self._passes_min_evidence(normalized):
            return PostVadClassification.GENUINE
        return PostVadClassification.WEAK

    def _passes_min_evidence(self, normalized: str) -> bool:
        words = normalized.split()
        return len(words) >= self._timing.min_interrupt_words or len(normalized) >= self._timing.min_interrupt_chars

    @staticmethod
    def assistant_echo_match_reason(
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
