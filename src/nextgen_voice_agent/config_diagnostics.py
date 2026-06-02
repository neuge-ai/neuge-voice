from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel

from nextgen_voice_agent.config import CONFIG_FILE, Settings
from nextgen_voice_agent.providers.registry import (
    LLM_PROVIDER_IDS,
    STT_PROVIDER_IDS,
    TTS_PROVIDER_IDS,
    resolve_active_config,
)


class ConfigDiagnostic(BaseModel):
    severity: Literal["info", "warning", "error"]
    code: str
    message: str
    provider: str | None = None
    key: str | None = None


def _keyring_available() -> bool:
    try:
        import keyring  # noqa: F401

        return True
    except Exception:
        return False


def collect_config_diagnostics(
    settings: Settings,
    get_secret: Callable[[str], str | None],
) -> list[ConfigDiagnostic]:
    diagnostics: list[ConfigDiagnostic] = []

    if not CONFIG_FILE.exists():
        diagnostics.append(
            ConfigDiagnostic(
                severity="info",
                code="config_created",
                message=f"Config file was missing and initialized at {CONFIG_FILE}.",
            )
        )
    else:
        try:
            with open(CONFIG_FILE, "rb") as handle:
                raw = tomllib.load(handle)
        except Exception as exc:
            diagnostics.append(
                ConfigDiagnostic(
                    severity="error",
                    code="config_parse_failed",
                    message=f"Could not parse config file: {exc}",
                )
            )
            raw = {}
        if raw:
            unknown = [key for key in raw if key not in Settings.model_fields]
            for key in unknown:
                diagnostics.append(
                    ConfigDiagnostic(
                        severity="warning",
                        code="unknown_config_key",
                        message=f"Unknown config key ignored: {key}",
                    )
                )

    if not _keyring_available():
        diagnostics.append(
            ConfigDiagnostic(
                severity="warning",
                code="keyring_unavailable",
                message="System keyring is unavailable; secrets may only load from environment variables.",
            )
        )

    active = resolve_active_config(settings)
    provider_checks = [
        ("stt", active.stt, STT_PROVIDER_IDS),
        ("tts", active.tts, TTS_PROVIDER_IDS),
        ("llm", active.llm, LLM_PROVIDER_IDS),
    ]
    secret_requirements = {
        "nvidia_nim": ["nvidia_api_key"],
        "elevenlabs": ["elevenlabs_api_key"],
        "sarvam": ["sarvam_api_key"],
        "groq": ["groq_api_key"],
        "openai": ["openai_api_key"],
        "anthropic": ["anthropic_api_key"],
    }
    for category, provider_id, allowed in provider_checks:
        if provider_id not in allowed:
            diagnostics.append(
                ConfigDiagnostic(
                    severity="error",
                    code="invalid_provider",
                    message=f"Unsupported {category} provider: {provider_id}",
                    provider=provider_id,
                )
            )
            continue
        for key_name in secret_requirements.get(provider_id, []):
            if not get_secret(key_name):
                diagnostics.append(
                    ConfigDiagnostic(
                        severity="warning",
                        code="required_secret_missing",
                        message=f"Required secret missing for active {category} provider.",
                        provider=provider_id,
                        key=key_name,
                    )
                )

    return diagnostics
