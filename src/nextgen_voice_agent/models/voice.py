from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from nextgen_voice_agent.models.task import utc_now
from datetime import datetime


class VoiceTransportKind(StrEnum):
    BROWSER = "browser"
    PHONE = "phone"


class VoiceEventType(StrEnum):
    CALL_CONNECTED = "call_connected"
    CALL_DISCONNECTED = "call_disconnected"
    INBOUND_AUDIO_FRAME = "inbound_audio_frame"
    OUTBOUND_AUDIO_FRAME = "outbound_audio_frame"
    SESSION_STARTED = "session_started"
    SESSION_STOPPED = "session_stopped"
    SPEECH_STARTED = "speech_started"
    SPEECH_ENDED = "speech_ended"
    USER_TURN = "user_turn"
    USER_TURN_AUDIO = "user_turn_audio"
    INTERRUPTION = "interruption"
    ASSISTANT_RESPONSE = "assistant_response"
    ASSISTANT_SPEECH_STARTED = "assistant_speech_started"
    ASSISTANT_SPEECH_ENDED = "assistant_speech_ended"
    STOP_ASSISTANT_AUDIO = "stop_assistant_audio"
    DELIVERY_READY = "delivery_ready"
    TASK_STATUS = "task_status"
    TRANSCRIPT_PARTIAL = "transcript_partial"
    TRANSCRIPT_FINAL = "transcript_final"
    STT_ERROR = "stt_error"
    TTS_STARTED = "tts_started"
    TTS_ENDED = "tts_ended"
    IDLE_STATE = "idle_state"
    CLIENT_IDLE = "client_idle"
    ERROR = "error"


class VoiceEvent(BaseModel):
    event: VoiceEventType
    transport: VoiceTransportKind
    session_id: str
    text: str | None = None
    audio_ref: str | None = None
    task_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
