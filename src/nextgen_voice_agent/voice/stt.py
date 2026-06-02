from __future__ import annotations

import base64
import queue
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from nextgen_voice_agent.config import Settings, get_secret


@dataclass(frozen=True)
class AudioFrame:
    payload: bytes
    sample_rate: int
    channels: int
    encoding: str
    sequence: int
    speech_segment_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SttResult:
    text: str
    confidence: float | None = None
    provider: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)


class AsrMode(StrEnum):
    SPEECH_GATED_STREAMING = "speech_gated_streaming"
    UTTERANCE_BATCH = "utterance_batch"


class SttTranscriptEventType(StrEnum):
    PARTIAL = "partial"
    FINAL = "final"
    ERROR = "error"


@dataclass(frozen=True)
class SttTranscriptEvent:
    event: SttTranscriptEventType
    text: str
    segment_id: str
    confidence: float | None = None
    provider: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)


class SttStream(ABC):
    segment_id: str

    @abstractmethod
    async def append(self, frame: AudioFrame) -> list[SttTranscriptEvent]:
        """Append one audio frame and return currently available transcript events."""

    @abstractmethod
    async def commit(self) -> SttTranscriptEvent:
        """Finalize the stream and return the authoritative final transcript."""

    @abstractmethod
    async def cancel(self) -> None:
        """Cancel the stream."""


class SttProvider(ABC):
    name = "base"
    supports_streaming = False
    supports_batch = True

    @abstractmethod
    async def transcribe_utterance(self, frames: list[AudioFrame]) -> SttResult:
        """Transcribe one VAD-delimited utterance."""

    async def start_stream(self, segment_id: str) -> SttStream:
        raise NotImplementedError(f"{self.name} does not support streaming ASR.")

    def warm_up(self) -> None:
        """Initialize HTTP/2 or gRPC clients in the background."""
        pass


class FakeSttStream(SttStream):
    def __init__(self, segment_id: str, transcript: str, provider: str) -> None:
        self.segment_id = segment_id
        self.transcript = transcript
        self.provider = provider
        self.frames: list[AudioFrame] = []
        self._partial_sent = False
        self._cancelled = False

    async def append(self, frame: AudioFrame) -> list[SttTranscriptEvent]:
        if self._cancelled or frame.speech_segment_id != self.segment_id:
            return []
        self.frames.append(frame)
        if self._partial_sent:
            return []
        self._partial_sent = True
        return [
            SttTranscriptEvent(
                event=SttTranscriptEventType.PARTIAL,
                text=self._partial_text(),
                segment_id=self.segment_id,
                confidence=0.85,
                provider=self.provider,
                metadata={"frames": len(self.frames)},
            )
        ]

    async def commit(self) -> SttTranscriptEvent:
        if self._cancelled:
            return SttTranscriptEvent(
                event=SttTranscriptEventType.ERROR,
                text="Stream was cancelled.",
                segment_id=self.segment_id,
                provider=self.provider,
            )
        return SttTranscriptEvent(
            event=SttTranscriptEventType.FINAL,
            text=self.transcript if self.frames else "",
            segment_id=self.segment_id,
            confidence=1.0 if self.frames else 0.0,
            provider=self.provider,
            metadata={"frames": len(self.frames), "bytes": sum(len(frame.payload) for frame in self.frames)},
        )

    async def cancel(self) -> None:
        self._cancelled = True

    def _partial_text(self) -> str:
        words = self.transcript.split()
        return " ".join(words[: max(1, min(len(words), 4))])


class FakeSttProvider(SttProvider):
    name = "fake"
    supports_streaming = True

    def __init__(self, transcript: str) -> None:
        self.transcript = transcript

    async def transcribe_utterance(self, frames: list[AudioFrame]) -> SttResult:
        if not frames:
            return SttResult(text="", confidence=0.0, provider=self.name)
        return SttResult(
            text=self.transcript,
            confidence=1.0,
            provider=self.name,
            metadata={"frames": len(frames), "bytes": sum(len(frame.payload) for frame in frames)},
        )

    async def start_stream(self, segment_id: str) -> SttStream:
        return FakeSttStream(segment_id=segment_id, transcript=self.transcript, provider=self.name)


class NvidiaNimSttProvider(SttProvider):
    name = "nvidia_nim"
    supports_streaming = True

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def transcribe_utterance(self, frames: list[AudioFrame]) -> SttResult:
        if not frames:
            return SttResult(text="", confidence=0.0, provider=self.name)
        stream = await self.start_stream(frames[0].speech_segment_id)
        for frame in frames:
            await stream.append(frame)
        result = await stream.commit()
        if result.event == SttTranscriptEventType.ERROR:
            raise RuntimeError(result.text)
        return SttResult(
            text=result.text,
            confidence=result.confidence,
            provider=self.name,
            metadata={"frames": len(frames), "bytes": sum(len(frame.payload) for frame in frames)},
        )

    async def start_stream(self, segment_id: str) -> SttStream:
        return NvidiaNimSttStream(segment_id=segment_id, provider=self.name, service=self._asr_service())

    def warm_up(self) -> None:
        if get_secret("nvidia_api_key"):
            try:
                self._asr_service()
            except Exception:
                pass

    def _asr_service(self):
        try:
            import riva.client
        except ImportError as exc:
            raise RuntimeError("Install nvidia-riva-client to use NVIDIA NIM STT.") from exc
        api_key = get_secret("nvidia_api_key")
        if api_key is None:
            raise RuntimeError("NVA_NVIDIA_API_KEY or NVIDIA_API_KEY is required for NVIDIA NIM STT.")
        auth = riva.client.Auth(
            use_ssl=True,
            uri=self.settings.nvidia_riva_server,
            metadata_args=[
                ["function-id", self.settings.nvidia_nim_stt_function_id or ""],
                ["authorization", f"Bearer {api_key}"],
            ],
        )
        return riva.client.ASRService(auth)


class NvidiaNimSttStream(SttStream):
    def __init__(self, segment_id: str, provider: str, service) -> None:
        self.segment_id = segment_id
        self.provider = provider
        self.service = service
        self._audio_queue: queue.Queue[bytes | None] = queue.Queue()
        self._event_queue: queue.Queue[SttTranscriptEvent] = queue.Queue()
        self._frames: list[AudioFrame] = []
        self._last_transcript_event: SttTranscriptEvent | None = None
        self._started = False
        self._closed = False
        self._worker: threading.Thread | None = None

    async def append(self, frame: AudioFrame) -> list[SttTranscriptEvent]:
        if self._closed or frame.speech_segment_id != self.segment_id:
            return []
        if not self._started:
            self._start_worker(frame)
        self._frames.append(frame)
        self._audio_queue.put(frame.payload)
        return self._drain_events()

    async def commit(self) -> SttTranscriptEvent:
        self._closed = True
        if not self._started:
            return SttTranscriptEvent(
                event=SttTranscriptEventType.FINAL,
                text="",
                segment_id=self.segment_id,
                confidence=0.0,
                provider=self.provider,
            )
        self._audio_queue.put(None)
        if self._worker is not None:
            self._worker.join(timeout=15)
        events = self._drain_events()
        for event in reversed(events):
            if event.event == SttTranscriptEventType.FINAL:
                return event
        for event in reversed(events):
            if event.event == SttTranscriptEventType.PARTIAL:
                return self._promote_partial(event)
        if self._last_transcript_event is not None:
            if self._last_transcript_event.event == SttTranscriptEventType.FINAL:
                return self._last_transcript_event
            return self._promote_partial(self._last_transcript_event)
        for event in reversed(events):
            if event.event == SttTranscriptEventType.ERROR:
                return event
        return SttTranscriptEvent(
            event=SttTranscriptEventType.ERROR,
            text="NVIDIA NIM stream ended without a transcript.",
            segment_id=self.segment_id,
            provider=self.provider,
        )

    async def cancel(self) -> None:
        self._closed = True
        self._audio_queue.put(None)

    def _start_worker(self, frame: AudioFrame) -> None:
        self._started = True
        self._worker = threading.Thread(target=self._run_stream, args=(frame,), daemon=True)
        self._worker.start()

    def _run_stream(self, first_frame: AudioFrame) -> None:
        try:
            import riva.client

            config = _recognition_config_for_frame(first_frame)
            streaming_config = riva.client.StreamingRecognitionConfig(config=config, interim_results=True)
            for response in self.service.streaming_response_generator(self._audio_iter(), streaming_config):
                for event in _streaming_events(response, self.segment_id, self.provider):
                    self._remember_transcript(event)
                    self._event_queue.put(event)
        except Exception as exc:
            self._event_queue.put(
                SttTranscriptEvent(
                    event=SttTranscriptEventType.ERROR,
                    text=str(exc),
                    segment_id=self.segment_id,
                    provider=self.provider,
                )
            )

    def _audio_iter(self):
        while True:
            chunk = self._audio_queue.get()
            if chunk is None:
                return
            yield chunk

    def _drain_events(self) -> list[SttTranscriptEvent]:
        events: list[SttTranscriptEvent] = []
        while True:
            try:
                event = self._event_queue.get_nowait()
                self._remember_transcript(event)
                events.append(event)
            except queue.Empty:
                return events

    def _remember_transcript(self, event: SttTranscriptEvent) -> None:
        if event.event in {SttTranscriptEventType.PARTIAL, SttTranscriptEventType.FINAL} and event.text.strip():
            self._last_transcript_event = event

    def _promote_partial(self, event: SttTranscriptEvent) -> SttTranscriptEvent:
        return SttTranscriptEvent(
            event=SttTranscriptEventType.FINAL,
            text=event.text,
            segment_id=self.segment_id,
            confidence=event.confidence,
            provider=self.provider,
            metadata={**event.metadata, "promoted_from_partial": True},
        )


def _recognition_config_for_frame(frame: AudioFrame):
    try:
        import riva.client
    except ImportError as exc:
        raise RuntimeError("Install nvidia-riva-client to use NVIDIA NIM STT.") from exc
    if frame.encoding != "pcm_s16le":
        raise RuntimeError(f"NVIDIA NIM provider expects pcm_s16le audio, got {frame.encoding}.")
    if frame.channels != 1:
        raise RuntimeError(f"NVIDIA NIM provider expects mono audio, got {frame.channels} channels.")
    return riva.client.RecognitionConfig(
        encoding=riva.client.AudioEncoding.LINEAR_PCM,
        sample_rate_hertz=frame.sample_rate,
        language_code="en-US",
        audio_channel_count=frame.channels,
        max_alternatives=1,
        enable_automatic_punctuation=True,
        verbatim_transcripts=False,
    )


def _streaming_events(response, segment_id: str, provider: str) -> list[SttTranscriptEvent]:
    events: list[SttTranscriptEvent] = []
    for result in response.results:
        if not result.alternatives:
            continue
        alternative = result.alternatives[0]
        transcript = alternative.transcript
        if not transcript:
            continue
        events.append(
            SttTranscriptEvent(
                event=SttTranscriptEventType.FINAL if result.is_final else SttTranscriptEventType.PARTIAL,
                text=transcript,
                segment_id=segment_id,
                confidence=alternative.confidence or None,
                provider=provider,
                metadata={
                    "stability": result.stability,
                    "audio_processed": result.audio_processed,
                },
            )
        )
    return events

class UtteranceBuffer:
    def __init__(self, speech_segment_id: str) -> None:
        self.speech_segment_id = speech_segment_id
        self.frames: list[AudioFrame] = []

    def append(self, frame: AudioFrame) -> None:
        if frame.speech_segment_id == self.speech_segment_id:
            self.frames.append(frame)

    def finalize(self) -> list[AudioFrame]:
        by_sequence: dict[int, AudioFrame] = {}
        for frame in self.frames:
            by_sequence.setdefault(frame.sequence, frame)
        return [by_sequence[sequence] for sequence in sorted(by_sequence)]


def parse_asr_mode(value: str) -> AsrMode:
    try:
        return AsrMode(value)
    except ValueError:
        return AsrMode.SPEECH_GATED_STREAMING


def create_stt_provider(settings: Settings) -> SttProvider:
    from nextgen_voice_agent.providers.registry import validate_stt_provider_id

    validate_stt_provider_id(settings.stt_provider)
    if settings.stt_provider == "nvidia_nim":
        return NvidiaNimSttProvider(settings)
    return FakeSttProvider(settings.fake_stt_transcript)


def parse_audio_frame(audio_ref: str | None, metadata: dict[str, Any]) -> AudioFrame:
    if not audio_ref:
        raise ValueError("Audio frame is missing audio_ref.")

    if "," in audio_ref and audio_ref.startswith("data:"):
        _, encoded = audio_ref.split(",", 1)
    else:
        encoded = audio_ref

    payload = base64.b64decode(encoded)
    return AudioFrame(
        payload=payload,
        sample_rate=int(metadata.get("sample_rate", 16000)),
        channels=int(metadata.get("channels", 1)),
        encoding=str(metadata.get("encoding", "pcm_s16le")),
        sequence=int(metadata.get("sequence", 0)),
        speech_segment_id=str(metadata.get("speech_segment_id", "default")),
        metadata=metadata,
    )
