from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from nextgen_voice_agent.models.voice import VoiceEventType


class DurableDeliveryMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    durable: bool = True
    delivery_id: str
    utterance_id: str | None = None
    event_type: str | None = None
    delivery_kind: str | None = None


class AssistantSpeechStartedMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    delivery_id: str | None = None
    utterance_id: str | None = None
    source: str | None = None
    completed: bool | None = None
    reason: str | None = None


class AssistantSpeechEndedMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    delivery_id: str | None = None
    utterance_id: str | None = None
    source: str | None = None
    completed: bool | None = None
    reason: str | None = None


class TranscriptFinalMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    candidate_status: str | None = None
    candidate_reason: str | None = None
    speech_segment_id: str | None = None
    during_assistant_speech: bool | None = None
    during_cooldown: bool | None = None


_METADATA_BY_EVENT: dict[VoiceEventType, type[BaseModel]] = {
    VoiceEventType.ASSISTANT_SPEECH_STARTED: AssistantSpeechStartedMetadata,
    VoiceEventType.ASSISTANT_SPEECH_ENDED: AssistantSpeechEndedMetadata,
    VoiceEventType.TRANSCRIPT_FINAL: TranscriptFinalMetadata,
}


def parse_metadata(event_type: VoiceEventType | str, metadata: dict[str, Any] | None) -> BaseModel | None:
    if not metadata:
        return None
    if metadata.get("durable") is True:
        return DurableDeliveryMetadata.model_validate(metadata)
    if isinstance(event_type, str):
        try:
            event_type = VoiceEventType(event_type)
        except ValueError:
            return None
    model = _METADATA_BY_EVENT.get(event_type)
    if model is None:
        return None
    return model.model_validate(metadata)
