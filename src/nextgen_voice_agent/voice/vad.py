from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class VadDecision:
    speech_detected: bool
    confidence: float = 0.0


class VadEngine(ABC):
    @abstractmethod
    async def process_frame(self, pcm: bytes, sample_rate: int) -> VadDecision:
        """Classify one audio frame."""


class NoopVadEngine(VadEngine):
    async def process_frame(self, pcm: bytes, sample_rate: int) -> VadDecision:
        return VadDecision(speech_detected=False, confidence=0.0)
