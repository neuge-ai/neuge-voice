import os
import tomllib
from pathlib import Path
from pydantic import BaseModel, Field
import keyring

CONFIG_DIR = Path.home() / ".config" / "neuge-voice"
CONFIG_FILE = CONFIG_DIR / "config.toml"


class Settings(BaseModel):
    """Runtime settings for the local agent service."""

    app_name: str = "Neuge Voice"
    environment: str = "development"
    runtime: str = Field(default="fake", description="Runtime adapter: fake or codex_app_server.")
    codex_command: str = "codex"
    codex_model: str = "gpt-5.4-mini"
    openai_realtime_model: str = "gpt-realtime-mini"
    openai_realtime_voice: str = "alloy"
    router_model: str = "groq/qwen/qwen3-32b"
    stt_provider: str = Field(default="fake", description="STT provider: fake or nvidia_nim.")
    asr_mode: str = Field(default="speech_gated_streaming", description="ASR mode: speech_gated_streaming or utterance_batch.")
    fake_stt_transcript: str = "Check this week's weather and average noon and evening temperatures."
    nvidia_riva_server: str = "grpc.nvcf.nvidia.com:443"
    nvidia_nim_stt_function_id: str | None = Field(default="d8dd4e9b-fbf5-4fb0-9dba-8cf436c8d965")
    tts_provider: str = "nvidia_nim"
    nvidia_nim_tts_function_id: str | None = Field(default="877104f7-e885-42b9-8de8-f6e4c6303969")
    nvidia_nim_tts_voice: str = "Magpie-Multilingual.EN-US.Aria"
    nvidia_nim_tts_language_code: str = "en-US"
    nvidia_nim_tts_sample_rate_hz: int = 22050
    elevenlabs_voice_id: str = "ErXwobaYiN019PkySvjV"
    elevenlabs_model_id: str = "eleven_multilingual_v2"
    elevenlabs_output_format: str = "mp3_44100_128"
    sarvam_target_language_code: str = "en-IN"
    sarvam_model: str = "bulbul:v3"
    sarvam_speaker: str = "priya"


def get_secret(key_name: str) -> str | None:
    try:
        # Check environment variable first as fallback
        env_val = os.getenv(f"NVA_{key_name.upper()}")
        if env_val:
            return env_val
        return keyring.get_password("NeugeVoice", key_name)
    except Exception:
        return None

def set_secret(key_name: str, value: str) -> None:
    keyring.set_password("NeugeVoice", key_name, value)


def load_toml_config() -> dict:
    if not CONFIG_FILE.exists():
        default_config = {key: "default" for key in Settings.model_fields.keys()}
        try:
            save_toml_config(default_config)
        except Exception:
            pass
        return default_config
    try:
        with open(CONFIG_FILE, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def save_toml_config(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    import tomli_w
    with open(CONFIG_FILE, "wb") as f:
        tomli_w.dump(data, f)


def get_settings() -> Settings:
    toml_data = load_toml_config()
    # Only pass fields that exist in Settings and are not set to "default"
    valid_keys = Settings.model_fields.keys()
    filtered_data = {k: v for k, v in toml_data.items() if k in valid_keys and v != "default"}
    return Settings(**filtered_data)

def inject_llm_secrets() -> None:
    groq_key = get_secret("groq_api_key")
    if groq_key:
        os.environ["GROQ_API_KEY"] = groq_key
        
    openai_key = get_secret("openai_api_key")
    if openai_key:
        os.environ["OPENAI_API_KEY"] = openai_key
