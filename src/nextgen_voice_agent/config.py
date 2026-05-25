from pydantic import AliasChoices, Field
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the local agent service."""

    app_name: str = "NextGen Voice Agent"
    environment: str = "development"
    runtime: str = Field(default="fake", description="Runtime adapter: fake or codex_cli.")
    codex_command: str = "codex"
    openai_api_key: SecretStr | None = None
    openai_realtime_model: str = "gpt-realtime-mini"
    openai_realtime_voice: str = "alloy"
    stt_provider: str = Field(default="fake", description="STT provider: fake or nvidia_parakeet.")
    asr_mode: str = Field(default="speech_gated_streaming", description="ASR mode: speech_gated_streaming or utterance_batch.")
    fake_stt_transcript: str = "Check this week's weather and average noon and evening temperatures."
    nvidia_api_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("NVA_NVIDIA_API_KEY", "NVIDIA_API_KEY"))
    nvidia_riva_server: str = "grpc.nvcf.nvidia.com:443"
    nvidia_parakeet_function_id: str | None = Field(
        default="d8dd4e9b-fbf5-4fb0-9dba-8cf436c8d965",
        validation_alias=AliasChoices(
            "NVA_NVIDIA_PARAKEET_FUNCTION_ID",
            "NVA_NVIDIA_PARAKET_FUNCTION_ID",
            "NVIDIA_PARAKEET_FUNCTION_ID",
            "NVIDIA_PARAKET_FUNCTION_ID",
        ),
    )
    tts_provider: str = "nvidia_magpie"
    nvidia_magpie_function_id: str | None = Field(
        default="877104f7-e885-42b9-8de8-f6e4c6303969",
        validation_alias=AliasChoices("NVA_NVIDIA_MAGPIE_FUNCTION_ID", "NVIDIA_MAGPIE_FUNCTION_ID"),
    )
    nvidia_magpie_voice: str = "Magpie-Multilingual.EN-US.Aria"
    nvidia_magpie_language_code: str = "en-US"
    nvidia_magpie_sample_rate_hz: int = 22050

    model_config = SettingsConfigDict(env_prefix="NVA_", env_file=".env", extra="ignore")


def get_settings() -> Settings:
    return Settings()
