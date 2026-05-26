from __future__ import annotations

import asyncio
import base64
import io
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from nextgen_voice_agent.config import Settings


@dataclass(frozen=True)
class TtsRequest:
    text: str
    voice: str = "default"
    style: str = "natural"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TtsResult:
    provider: str
    audio_ref: str | None = None
    audio_mime_type: str | None = None
    text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TtsProviderError(RuntimeError):
    def __init__(self, message: str, *, provider: str, stage: str) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.stage = stage


class TtsProvider(ABC):
    name = "base"

    @abstractmethod
    async def synthesize(self, request: TtsRequest) -> TtsResult:
        """Synthesize or route one assistant speech chunk."""


class BrowserDevTtsProvider(TtsProvider):
    name = "browser_dev"

    async def synthesize(self, request: TtsRequest) -> TtsResult:
        return TtsResult(provider=self.name, text=request.text, metadata={"voice": request.voice, "style": request.style})


class NvidiaMagpieTtsProvider(TtsProvider):
    name = "nvidia_magpie"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def synthesize(self, request: TtsRequest) -> TtsResult:
        if self.settings.nvidia_api_key is None:
            raise TtsProviderError(
                "NVA_NVIDIA_API_KEY or NVIDIA_API_KEY is required for NVIDIA Magpie TTS.",
                provider=self.name,
                stage="configuration",
            )
        if not request.text.strip():
            raise TtsProviderError("Cannot synthesize empty text.", provider=self.name, stage="validation")
        try:
            audio = await asyncio.wait_for(
                asyncio.to_thread(self._synthesize_blocking, request.text),
                timeout=15.0
            )
        except asyncio.TimeoutError as exc:
            raise TtsProviderError(
                "NVIDIA Magpie synthesis timed out after 15 seconds.",
                provider=self.name,
                stage="synthesis",
            ) from exc
        except TtsProviderError:
            raise
        except Exception as exc:
            raise TtsProviderError(
                f"NVIDIA Magpie synthesis failed: {_exception_message(exc)}",
                provider=self.name,
                stage="synthesis",
            ) from exc
        if not audio:
            raise TtsProviderError("NVIDIA Magpie returned empty audio.", provider=self.name, stage="synthesis")
        wav_audio = _wav_from_pcm(audio, sample_rate_hz=self.settings.nvidia_magpie_sample_rate_hz)
        return TtsResult(
            provider=self.name,
            audio_ref=f"data:audio/wav;base64,{base64.b64encode(wav_audio).decode('ascii')}",
            audio_mime_type="audio/wav",
            metadata={
                "voice": self.settings.nvidia_magpie_voice,
                "language_code": self.settings.nvidia_magpie_language_code,
                "sample_rate_hz": self.settings.nvidia_magpie_sample_rate_hz,
            },
        )

    def _synthesize_blocking(self, text: str) -> bytes:
        try:
            import riva.client
        except ImportError as exc:
            raise TtsProviderError(
                "Install nvidia-riva-client to use NVIDIA Magpie TTS.",
                provider=self.name,
                stage="dependency",
            ) from exc

        auth = riva.client.Auth(
            use_ssl=True,
            uri=self.settings.nvidia_riva_server,
            metadata_args=[
                ["function-id", self.settings.nvidia_magpie_function_id or ""],
                ["authorization", f"Bearer {self.settings.nvidia_api_key.get_secret_value()}"],
            ],
        )
        service = riva.client.SpeechSynthesisService(auth)
        response = service.synthesize(
            text=text,
            voice_name=self.settings.nvidia_magpie_voice,
            language_code=self.settings.nvidia_magpie_language_code,
            encoding=riva.client.AudioEncoding.LINEAR_PCM,
            sample_rate_hz=self.settings.nvidia_magpie_sample_rate_hz,
        )
        return bytes(response.audio)


class ElevenLabsTtsProvider(TtsProvider):
    name = "elevenlabs"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def synthesize(self, request: TtsRequest) -> TtsResult:
        if self.settings.elevenlabs_api_key is None:
            raise TtsProviderError(
                "NVA_ELEVENLABS_API_KEY or ELEVENLABS_API_KEY is required for ElevenLabs TTS.",
                provider=self.name,
                stage="configuration",
            )
        if not request.text.strip():
            raise TtsProviderError("Cannot synthesize empty text.", provider=self.name, stage="validation")

        try:
            audio_bytes = await asyncio.to_thread(self._synthesize_blocking, request.text)
        except Exception as exc:
            raise TtsProviderError(
                f"ElevenLabs synthesis failed: {exc}",
                provider=self.name,
                stage="synthesis",
            ) from exc

        return TtsResult(
            provider=self.name,
            audio_ref=f"data:audio/mpeg;base64,{base64.b64encode(audio_bytes).decode('ascii')}",
            audio_mime_type="audio/mpeg",
            metadata={
                "voice_id": self.settings.elevenlabs_voice_id,
                "model_id": self.settings.elevenlabs_model_id,
                "output_format": self.settings.elevenlabs_output_format,
            },
        )

    def _synthesize_blocking(self, text: str) -> bytes:
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=self.settings.elevenlabs_api_key.get_secret_value())
        audio_generator = client.text_to_speech.convert(
            text=text,
            voice_id=self.settings.elevenlabs_voice_id,
            model_id=self.settings.elevenlabs_model_id,
            output_format=self.settings.elevenlabs_output_format,
        )
        return b"".join(audio_generator)


class SarvamTtsProvider(TtsProvider):
    name = "sarvam"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def synthesize(self, request: TtsRequest) -> TtsResult:
        if self.settings.sarvam_api_key is None:
            raise TtsProviderError(
                "NVA_SARVAM_API_KEY or SARVAM_API_KEY is required for Sarvam TTS.",
                provider=self.name,
                stage="configuration",
            )
        if not request.text.strip():
            raise TtsProviderError("Cannot synthesize empty text.", provider=self.name, stage="validation")

        try:
            audio_b64 = await asyncio.to_thread(self._synthesize_blocking, request.text)
        except Exception as exc:
            raise TtsProviderError(
                f"Sarvam synthesis failed: {exc}",
                provider=self.name,
                stage="synthesis",
            ) from exc

        return TtsResult(
            provider=self.name,
            audio_ref=f"data:audio/wav;base64,{audio_b64}",
            audio_mime_type="audio/wav",
            metadata={
                "target_language_code": self.settings.sarvam_target_language_code,
                "model": self.settings.sarvam_model,
                "speaker": self.settings.sarvam_speaker,
            },
        )

    def _synthesize_blocking(self, text: str) -> str:
        from sarvamai import SarvamAI
        client = SarvamAI(api_subscription_key=self.settings.sarvam_api_key.get_secret_value())
        response = client.text_to_speech.convert(
            text=text,
            target_language_code=self.settings.sarvam_target_language_code,
            model=self.settings.sarvam_model,
            speaker=self.settings.sarvam_speaker,
        )
        return response.audios[0]


def _exception_message(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    details = getattr(exc, "details", None)
    if callable(code) and callable(details):
        try:
            return f"{code().name}: {details()}"
        except Exception:
            pass
    return str(exc) or exc.__class__.__name__


def _wav_from_pcm(pcm: bytes, sample_rate_hz: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate_hz)
        wav.writeframes(pcm)
    return buffer.getvalue()


def create_tts_provider(settings: Settings) -> TtsProvider:
    if settings.tts_provider == "nvidia_magpie":
        return NvidiaMagpieTtsProvider(settings)
    if settings.tts_provider == "browser_dev":
        return BrowserDevTtsProvider()
    if settings.tts_provider == "elevenlabs":
        return ElevenLabsTtsProvider(settings)
    if settings.tts_provider == "sarvam":
        return SarvamTtsProvider(settings)
    supported = ", ".join(("nvidia_magpie", "browser_dev", "elevenlabs", "sarvam"))
    raise RuntimeError(f"Unsupported TTS provider {settings.tts_provider!r}. Supported providers: {supported}.")
