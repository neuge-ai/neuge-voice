from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from nextgen_voice_agent.models.voice import VoiceEvent, VoiceTransportKind
from nextgen_voice_agent.transports.base import VoiceTransport
from nextgen_voice_agent.voice.vad import NoopVadEngine, VadEngine


class PhoneVoiceTransport(VoiceTransport):
    """Scaffold for future telephony media streams.

    Phone deployments usually provide server-side audio streams, so VAD or turn
    detection will run in this service, a media gateway, or the voice model.
    """

    kind = VoiceTransportKind.PHONE

    def __init__(self, vad_engine: VadEngine | None = None) -> None:
        self._events: asyncio.Queue[VoiceEvent] = asyncio.Queue()
        self.vad_engine = vad_engine or NoopVadEngine()

    async def receive(self, event: VoiceEvent) -> None:
        await self._events.put(event)

    async def events(self) -> AsyncIterator[VoiceEvent]:
        while True:
            yield await self._events.get()
