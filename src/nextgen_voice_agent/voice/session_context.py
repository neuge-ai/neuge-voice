from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from nextgen_voice_agent.models.task import utc_now
from nextgen_voice_agent.models.voice import VoiceTransportKind


class VoiceSessionContext(BaseModel):
    session_id: str
    transport: VoiceTransportKind
    external_session_id: str | None = None
    client_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
