from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from nextgen_voice_agent.models.voice import VoiceEvent


class VoiceTransport(ABC):
    @abstractmethod
    async def receive(self, event: VoiceEvent) -> None:
        """Receive a voice event from a client or media gateway."""

    @abstractmethod
    async def events(self) -> AsyncIterator[VoiceEvent]:
        """Yield outbound voice events."""

