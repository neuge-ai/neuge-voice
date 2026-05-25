from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from nextgen_voice_agent.models.voice import VoiceEvent, VoiceTransportKind
from nextgen_voice_agent.transports.base import VoiceTransport


class BrowserVoiceTransport(VoiceTransport):
    """Browser transport boundary for WebSocket/Realtime clients."""

    kind = VoiceTransportKind.BROWSER

    def __init__(self) -> None:
        self._events: asyncio.Queue[VoiceEvent] = asyncio.Queue()

    async def receive(self, event: VoiceEvent) -> None:
        await self._events.put(event)

    async def events(self) -> AsyncIterator[VoiceEvent]:
        while True:
            yield await self._events.get()

