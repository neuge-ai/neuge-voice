import pytest
from unittest.mock import MagicMock, patch
from pydantic import SecretStr

from nextgen_voice_agent.config import Settings
from nextgen_voice_agent.voice.tts import ElevenLabsTtsProvider, TtsRequest, TtsProviderError


@pytest.mark.asyncio
async def test_elevenlabs_tts_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        tts_provider="elevenlabs",
    )
    # Explicitly bypass Pydantic loader to ensure key is missing for the test
    object.__setattr__(settings, "elevenlabs_api_key", None)
    provider = ElevenLabsTtsProvider(settings)

    with pytest.raises(TtsProviderError) as exc_info:
        await provider.synthesize(TtsRequest(text="Hello"))

    assert "api_key" in str(exc_info.value).lower()
    assert exc_info.value.stage == "configuration"


@pytest.mark.asyncio
async def test_elevenlabs_tts_empty_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVA_ELEVENLABS_API_KEY", "mock_key")
    settings = Settings(
        tts_provider="elevenlabs",
    )
    provider = ElevenLabsTtsProvider(settings)

    with pytest.raises(TtsProviderError) as exc_info:
        await provider.synthesize(TtsRequest(text="  "))

    assert "empty text" in str(exc_info.value).lower()
    assert exc_info.value.stage == "validation"


@pytest.mark.asyncio
@patch("elevenlabs.client.ElevenLabs")
async def test_elevenlabs_tts_success(mock_client_class: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVA_ELEVENLABS_API_KEY", "mock_key")
    settings = Settings(
        tts_provider="elevenlabs",
        elevenlabs_voice_id="aria",
        elevenlabs_model_id="multilingual",
        elevenlabs_output_format="mp3_44100_128",
    )
    
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    mock_client.text_to_speech.convert.return_value = [b"chunk1", b"chunk2"]

    provider = ElevenLabsTtsProvider(settings)
    result = await provider.synthesize(TtsRequest(text="Hello ElevenLabs"))

    assert result.provider == "elevenlabs"
    assert result.audio_ref is not None
    assert result.audio_ref.startswith("data:audio/mpeg;base64,")
    assert result.audio_mime_type == "audio/mpeg"
    assert result.metadata["voice_id"] == "aria"
    assert result.metadata["model_id"] == "multilingual"
    assert result.metadata["output_format"] == "mp3_44100_128"

    mock_client.text_to_speech.convert.assert_called_once_with(
        text="Hello ElevenLabs",
        voice_id="aria",
        model_id="multilingual",
        output_format="mp3_44100_128",
    )
