import pytest

from nextgen_voice_agent.config import Settings
from nextgen_voice_agent.server.runtime_mode import (
    BackendRunMode,
    UiServingMode,
    apply_runtime_modes,
    parse_backend_run_mode,
    parse_ui_serving_mode,
)


def test_parse_backend_run_mode_defaults() -> None:
    assert parse_backend_run_mode("managed_local") == BackendRunMode.MANAGED_LOCAL
    assert parse_backend_run_mode("server") == BackendRunMode.SERVER
    assert parse_backend_run_mode("test") == BackendRunMode.TEST


def test_parse_backend_run_mode_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unsupported backend_run_mode"):
        parse_backend_run_mode("electron_sidecar")


def test_apply_runtime_modes_for_test_app() -> None:
    policy = apply_runtime_modes(Settings(backend_run_mode="test", ui_serving_mode="none"))
    assert policy.backend_run_mode == BackendRunMode.TEST
    assert policy.ui_serving_mode == UiServingMode.NONE
    assert policy.should_mount_frontend is False
    assert policy.allow_dev_cors is True


def test_apply_runtime_modes_for_server() -> None:
    policy = apply_runtime_modes(Settings(backend_run_mode="server", ui_serving_mode="none"))
    assert policy.bind_host_default == "0.0.0.0"
    assert policy.require_explicit_cors_origins is True
    assert policy.should_auto_open_browser is False


def test_apply_runtime_modes_for_managed_local_browser() -> None:
    policy = apply_runtime_modes(
        Settings(backend_run_mode="managed_local", ui_serving_mode="backend_static")
    )
    assert policy.should_mount_frontend is True
    assert policy.should_auto_open_browser is True
