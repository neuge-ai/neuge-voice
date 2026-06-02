from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from nextgen_voice_agent.config import Settings


class BackendRunMode(StrEnum):
    MANAGED_LOCAL = "managed_local"
    SERVER = "server"
    TEST = "test"


class UiServingMode(StrEnum):
    BACKEND_STATIC = "backend_static"
    ELECTRON_STATIC = "electron_static"
    DEV_SERVER = "dev_server"
    NONE = "none"


class RuntimePolicy(BaseModel):
    backend_run_mode: BackendRunMode
    ui_serving_mode: UiServingMode
    bind_host_default: str
    should_mount_frontend: bool
    should_auto_open_browser: bool
    allow_dev_cors: bool
    require_explicit_cors_origins: bool


def parse_backend_run_mode(value: str) -> BackendRunMode:
    normalized = value.strip().lower()
    try:
        return BackendRunMode(normalized)
    except ValueError as exc:
        supported = ", ".join(mode.value for mode in BackendRunMode)
        raise ValueError(f"Unsupported backend_run_mode {value!r}. Supported: {supported}.") from exc


def parse_ui_serving_mode(value: str) -> UiServingMode:
    normalized = value.strip().lower()
    try:
        return UiServingMode(normalized)
    except ValueError as exc:
        supported = ", ".join(mode.value for mode in UiServingMode)
        raise ValueError(f"Unsupported ui_serving_mode {value!r}. Supported: {supported}.") from exc


def apply_runtime_modes(settings: Settings) -> RuntimePolicy:
    backend_mode = parse_backend_run_mode(settings.backend_run_mode)
    ui_mode = parse_ui_serving_mode(settings.ui_serving_mode)

    should_mount_frontend = ui_mode == UiServingMode.BACKEND_STATIC
    should_auto_open_browser = backend_mode == BackendRunMode.MANAGED_LOCAL and ui_mode == UiServingMode.BACKEND_STATIC
    allow_dev_cors = backend_mode in {BackendRunMode.MANAGED_LOCAL, BackendRunMode.TEST}
    require_explicit_cors_origins = backend_mode == BackendRunMode.SERVER

    if backend_mode == BackendRunMode.SERVER:
        bind_host_default = "0.0.0.0"
    else:
        bind_host_default = "127.0.0.1"

    return RuntimePolicy(
        backend_run_mode=backend_mode,
        ui_serving_mode=ui_mode,
        bind_host_default=bind_host_default,
        should_mount_frontend=should_mount_frontend,
        should_auto_open_browser=should_auto_open_browser,
        allow_dev_cors=allow_dev_cors,
        require_explicit_cors_origins=require_explicit_cors_origins,
    )
