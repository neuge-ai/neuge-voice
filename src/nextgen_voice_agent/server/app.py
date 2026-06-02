from __future__ import annotations

import os
import sys
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response

from nextgen_voice_agent.config import Settings, get_settings
from nextgen_voice_agent.server.codex_app_server import CodexAppServerService
from nextgen_voice_agent.server.dependencies import create_controller, create_stt, create_tts
from nextgen_voice_agent.server import routes, websocket
from nextgen_voice_agent.server.runtime_mode import apply_runtime_modes
from nextgen_voice_agent.transports.webrtc import WebRTCSessionManager
from nextgen_voice_agent.voice.orchestrator import VoiceSessionOrchestrator, VoiceTimingConfig
from nextgen_voice_agent.voice.stt import parse_asr_mode


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        yield
    finally:
        await app.state.webrtc_session_manager.shutdown()
        await app.state.codex_app_server_service.shutdown()
        shutdown = getattr(app.state.controller.runtime, "shutdown", None)
        if shutdown is not None:
            await shutdown()


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    from nextgen_voice_agent.config import inject_llm_secrets

    inject_llm_secrets()
    policy = apply_runtime_modes(resolved)
    app = FastAPI(title=resolved.app_name, lifespan=_lifespan)
    app.state.settings = resolved
    app.state.runtime_policy = policy
    app.state.controller = create_controller(resolved)
    app.state.stt_provider = create_stt(resolved)
    app.state.stt_provider.warm_up()
    app.state.tts_provider = create_tts(resolved)
    app.state.tts_provider.warm_up()
    app.state.voice_orchestrator = VoiceSessionOrchestrator(
        app.state.controller,
        stt_provider=app.state.stt_provider,
        requested_asr_mode=parse_asr_mode(resolved.asr_mode),
        timing=VoiceTimingConfig(assistant_ack_timeout_ms=2000),
    )
    app.state.webrtc_session_manager = WebRTCSessionManager(app.state.voice_orchestrator)
    app.state.codex_app_server_service = CodexAppServerService()
    _configure_cors(app, policy, resolved)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "runtime": resolved.runtime,
            "backend_run_mode": policy.backend_run_mode.value,
            "ui_serving_mode": policy.ui_serving_mode.value,
            "stt_provider": app.state.stt_provider.name,
            "tts_provider": app.state.tts_provider.name,
            "requested_asr_mode": resolved.asr_mode,
            "effective_asr_mode": app.state.voice_orchestrator.effective_asr_mode.value,
        }

    @app.get("/api/config/status")
    async def config_status():
        from nextgen_voice_agent.config import get_secret
        from nextgen_voice_agent.providers.registry import build_config_status

        return build_config_status(resolved, get_secret)

    @app.post("/api/config/open")
    async def open_config() -> dict[str, str | bool]:
        from nextgen_voice_agent.platform.open_file import open_config_file

        return open_config_file()

    app.include_router(routes.router)
    app.include_router(websocket.router)
    if policy.should_mount_frontend:
        mount_frontend(app)
    return app


def _configure_cors(app: FastAPI, policy, settings: Settings) -> None:
    if policy.require_explicit_cors_origins and settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        return
    if policy.allow_dev_cors:
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )


def mount_frontend(app: FastAPI) -> None:
    web_dist = find_web_dist()
    if web_dist is None:
        return

    assets_dir = web_dist / "assets"
    index_path = web_dist / "index.html"

    if assets_dir.exists():

        @app.get("/assets/{asset_path:path}", include_in_schema=False)
        async def frontend_asset(asset_path: str) -> Response:
            path = (assets_dir / asset_path).resolve()
            if not path.is_file() or assets_dir.resolve() not in path.parents:
                raise HTTPException(status_code=404, detail="Asset not found.")
            media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            return Response(path.read_bytes(), media_type=media_type)

    @app.get("/", include_in_schema=False)
    async def frontend_index() -> HTMLResponse:
        return HTMLResponse(index_path.read_text(encoding="utf-8"))


def find_web_dist() -> Path | None:
    candidates: list[Path] = []
    env_path = os.getenv("NVA_WEB_DIST")
    if env_path:
        candidates.append(Path(env_path))

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / "apps" / "web" / "dist")
        candidates.append(Path(bundle_root) / "web")

    repo_root = Path(__file__).resolve().parents[3]
    candidates.append(repo_root / "apps" / "web" / "dist")

    for candidate in candidates:
        if (candidate / "index.html").exists():
            return candidate
    return None
