from __future__ import annotations

from nextgen_voice_agent.config import Settings
from nextgen_voice_agent.config_diagnostics import collect_config_diagnostics
from nextgen_voice_agent.providers.registry import build_config_status


def test_collect_config_diagnostics_flags_missing_secret() -> None:
    diagnostics = collect_config_diagnostics(
        Settings(stt_provider="nvidia_nim", tts_provider="browser_dev", router_model="groq/x"),
        lambda _key: None,
    )
    codes = {item.code for item in diagnostics}
    assert "required_secret_missing" in codes


def test_build_config_status_includes_diagnostics() -> None:
    status = build_config_status(Settings(stt_provider="fake"), lambda _key: None)
    assert isinstance(status.diagnostics, list)


def test_build_config_status_marks_invalid_provider_as_not_ready() -> None:
    status = build_config_status(Settings(stt_provider="not_a_provider"), lambda _key: None)
    assert status.system_ready is False
    assert any(item.code == "invalid_provider" for item in status.diagnostics)
