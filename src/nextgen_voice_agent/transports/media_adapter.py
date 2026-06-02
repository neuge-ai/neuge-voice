from __future__ import annotations

from typing import Protocol

from nextgen_voice_agent.models.voice import VoiceEvent
from nextgen_voice_agent.voice.session_context import VoiceSessionContext
from nextgen_voice_agent.voice.stt import AudioFrame


class VoiceMediaAdapter(Protocol):
    """Target shape for browser WebRTC and future phone media adapters."""

    async def start_session(self, context: VoiceSessionContext) -> None: ...

    async def stop_session(self, session_id: str, reason: str) -> None: ...

    async def receive_audio_frame(self, session_id: str, frame: AudioFrame) -> None: ...

    async def send_event(self, session_id: str, event: VoiceEvent) -> None: ...
