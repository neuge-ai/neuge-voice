from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, Field

from nextgen_voice_agent.config import CONFIG_FILE, Settings


class RequiredSecretKey(BaseModel):
    id: str
    is_configured: bool = False


class ProviderEntry(BaseModel):
    id: str
    name: str
    required_keys: list[RequiredSecretKey] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)


class ProvidersSchema(BaseModel):
    stt: list[ProviderEntry]
    tts: list[ProviderEntry]
    llm: list[ProviderEntry]


class ActiveConfig(BaseModel):
    stt: str
    tts: str
    llm: str


class ConfigDiagnosticEntry(BaseModel):
    severity: str
    code: str
    message: str
    provider: str | None = None
    key: str | None = None


class ConfigStatusResponse(BaseModel):
    system_ready: bool
    missing_capabilities: list[str]
    active_config: ActiveConfig
    default_config: ActiveConfig
    providers_schema: ProvidersSchema
    config_path: str
    diagnostics: list[ConfigDiagnosticEntry] = Field(default_factory=list)


def _provider_entries(
    entries: list[tuple[str, str, list[str]]],
    get_secret: Callable[[str], str | None],
) -> list[ProviderEntry]:
    result: list[ProviderEntry] = []
    for provider_id, display_name, secret_keys in entries:
        result.append(
            ProviderEntry(
                id=provider_id,
                name=display_name,
                required_keys=[
                    RequiredSecretKey(id=key_id, is_configured=bool(get_secret(key_id)))
                    for key_id in secret_keys
                ],
            )
        )
    return result


STT_PROVIDER_IDS: frozenset[str] = frozenset({"fake", "nvidia_nim"})
TTS_PROVIDER_IDS: frozenset[str] = frozenset({"browser_dev", "nvidia_nim", "elevenlabs", "sarvam"})
LLM_PROVIDER_IDS: frozenset[str] = frozenset({"groq", "openai", "anthropic"})


def build_providers_schema(get_secret: Callable[[str], str | None]) -> ProvidersSchema:
    return ProvidersSchema(
        stt=_provider_entries(
            [
                ("fake", "Fake (Testing)", []),
                ("nvidia_nim", "NVIDIA NIM", ["nvidia_api_key"]),
            ],
            get_secret,
        ),
        tts=_provider_entries(
            [
                ("browser_dev", "Browser Native", []),
                ("nvidia_nim", "NVIDIA NIM", ["nvidia_api_key"]),
                ("elevenlabs", "ElevenLabs", ["elevenlabs_api_key"]),
                ("sarvam", "Sarvam AI", ["sarvam_api_key"]),
            ],
            get_secret,
        ),
        llm=_provider_entries(
            [
                ("groq", "Groq", ["groq_api_key"]),
                ("openai", "OpenAI", ["openai_api_key"]),
                ("anthropic", "Anthropic", ["anthropic_api_key"]),
            ],
            get_secret,
        ),
    )


def resolve_active_config(settings: Settings) -> ActiveConfig:
    return ActiveConfig(
        stt=settings.stt_provider.lower(),
        tts=settings.tts_provider.lower(),
        llm=settings.router_model.split("/")[0].lower(),
    )


def resolve_default_config() -> ActiveConfig:
    return ActiveConfig(
        stt=str(Settings.model_fields["stt_provider"].default).lower(),
        tts=str(Settings.model_fields["tts_provider"].default).lower(),
        llm=str(Settings.model_fields["router_model"].default).split("/")[0].lower(),
    )


def build_config_status(settings: Settings, get_secret: Callable[[str], str | None]) -> ConfigStatusResponse:
    from nextgen_voice_agent.config_diagnostics import collect_config_diagnostics

    providers_schema = build_providers_schema(get_secret)
    active_config = resolve_active_config(settings)
    default_config = resolve_default_config()
    raw_diagnostics = collect_config_diagnostics(settings, get_secret)
    diagnostics = [
        ConfigDiagnosticEntry(
            severity=item.severity,
            code=item.code,
            message=item.message,
            provider=item.provider,
            key=item.key,
        )
        for item in raw_diagnostics
    ]

    missing_capabilities: list[str] = []
    schema_by_category = {
        "stt": providers_schema.stt,
        "tts": providers_schema.tts,
        "llm": providers_schema.llm,
    }
    for category_id, active_provider_id in active_config.model_dump().items():
        provider_schema = next(
            (entry for entry in schema_by_category[category_id] if entry.id == active_provider_id),
            None,
        )
        if provider_schema is None:
            continue
        for req_key in provider_schema.required_keys:
            if not req_key.is_configured:
                missing_capabilities.append(category_id)
                break

    has_error = any(item.severity == "error" for item in diagnostics)
    return ConfigStatusResponse(
        system_ready=len(missing_capabilities) == 0 and not has_error,
        missing_capabilities=missing_capabilities,
        active_config=active_config,
        default_config=default_config,
        providers_schema=providers_schema,
        config_path=str(CONFIG_FILE),
        diagnostics=diagnostics,
    )


def validate_stt_provider_id(provider_id: str) -> None:
    normalized = provider_id.lower()
    if normalized not in STT_PROVIDER_IDS:
        raise RuntimeError(
            f"Unsupported STT provider {provider_id!r}. Supported providers: {', '.join(sorted(STT_PROVIDER_IDS))}."
        )


def validate_tts_provider_id(provider_id: str) -> None:
    normalized = provider_id.lower()
    if normalized not in TTS_PROVIDER_IDS:
        raise RuntimeError(
            f"Unsupported TTS provider {provider_id!r}. Supported providers: {', '.join(sorted(TTS_PROVIDER_IDS))}."
        )
