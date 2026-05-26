import pytest
from pydantic import SecretStr

from nextgen_voice_agent.config import Settings
from nextgen_voice_agent.voice.tts import SarvamTtsProvider, TtsRequest, TtsProviderError


@pytest.mark.asyncio
async def test_sarvam_tts_provider_requires_api_key(monkeypatch):
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    monkeypatch.delenv("NVA_SARVAM_API_KEY", raising=False)
    settings = Settings(sarvam_api_key=None, _env_file=None)
    provider = SarvamTtsProvider(settings)

    with pytest.raises(TtsProviderError) as exc_info:
        await provider.synthesize(TtsRequest(text="Hello world"))

    assert exc_info.value.stage == "configuration"
    assert "required for Sarvam TTS" in exc_info.value.message


@pytest.mark.asyncio
async def test_sarvam_tts_provider_rejects_empty_text():
    settings = Settings(sarvam_api_key=SecretStr("fake_key"))
    provider = SarvamTtsProvider(settings)

    with pytest.raises(TtsProviderError) as exc_info:
        await provider.synthesize(TtsRequest(text="   "))

    assert exc_info.value.stage == "validation"
    assert "Cannot synthesize empty text" in exc_info.value.message
