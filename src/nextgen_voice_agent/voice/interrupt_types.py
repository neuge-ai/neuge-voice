from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ActiveVadClassification(StrEnum):
    NO_TRANSCRIPT = "no_transcript"
    ECHO_LIKE = "echo_like"
    DIVERGENT_TEXT = "divergent_text"


class PostVadClassification(StrEnum):
    NO_TRANSCRIPT = "no_transcript"
    ECHO = "echo"
    WEAK = "weak"
    GENUINE = "genuine"


@dataclass
class InterruptCandidate:
    text: str = ""
    source: str = "none"
    last_active_vad_classification: ActiveVadClassification = ActiveVadClassification.NO_TRANSCRIPT
    last_stt_confidence: float | None = None
