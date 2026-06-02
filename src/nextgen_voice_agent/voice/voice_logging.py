from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("nextgen_voice_agent.voice")


def log_voice_event(
    event_name: str,
    *,
    session_id: str | None = None,
    transport: str | None = None,
    event_type: str | None = None,
    task_id: str | None = None,
    speech_segment_id: str | None = None,
    delivery_id: str | None = None,
    voice_state: str | None = None,
    duration_ms: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    fields: dict[str, Any] = {"voice_event": event_name}
    if session_id is not None:
        fields["session_id"] = session_id
    if transport is not None:
        fields["transport"] = transport
    if event_type is not None:
        fields["event_type"] = event_type
    if task_id is not None:
        fields["task_id"] = task_id
    if speech_segment_id is not None:
        fields["speech_segment_id"] = speech_segment_id
    if delivery_id is not None:
        fields["delivery_id"] = delivery_id
    if voice_state is not None:
        fields["voice_state"] = voice_state
    if duration_ms is not None:
        fields["duration_ms"] = duration_ms
    if extra:
        fields.update(extra)
    logger.info(event_name, extra=fields)
