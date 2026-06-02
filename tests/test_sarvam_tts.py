import pytest

from nextgen_voice_agent.config import Settings
from nextgen_voice_agent.voice.tts import SarvamTtsProvider, TtsRequest, TtsProviderError


@pytest.mark.asyncio
async def test_sarvam_tts_provider_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    monkeypatch.delenv("NVA_SARVAM_API_KEY", raising=False)
    monkeypatch.setattr("nextgen_voice_agent.voice.tts.get_secret", lambda _key: None)
    provider = SarvamTtsProvider(Settings())

    with pytest.raises(TtsProviderError) as exc_info:
        await provider.synthesize(TtsRequest(text="Hello world"))

    assert exc_info.value.stage == "configuration"
    assert "required for Sarvam TTS" in exc_info.value.message


@pytest.mark.asyncio
async def test_sarvam_tts_provider_rejects_empty_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nextgen_voice_agent.voice.tts.get_secret", lambda key: "fake_key" if key == "sarvam_api_key" else None)
    provider = SarvamTtsProvider(Settings())

    with pytest.raises(TtsProviderError) as exc_info:
        await provider.synthesize(TtsRequest(text="   "))

    assert exc_info.value.stage == "validation"
    assert "Cannot synthesize empty text" in exc_info.value.message
