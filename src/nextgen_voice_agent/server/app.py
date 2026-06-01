from __future__ import annotations

import os
import sys
import mimetypes
from pathlib import Path

from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response

from nextgen_voice_agent.config import get_settings
from nextgen_voice_agent.server.dependencies import create_controller, create_stt, create_tts
from nextgen_voice_agent.server import routes, websocket
from nextgen_voice_agent.voice.orchestrator import VoiceSessionOrchestrator, VoiceTimingConfig
from nextgen_voice_agent.voice.stt import parse_asr_mode


def create_app() -> FastAPI:
    settings = get_settings()
    from nextgen_voice_agent.config import inject_llm_secrets
    inject_llm_secrets()
    app = FastAPI(title=settings.app_name)
    app.state.controller = create_controller(settings)
    app.state.stt_provider = create_stt(settings)
    app.state.stt_provider.warm_up()
    app.state.tts_provider = create_tts(settings)
    app.state.tts_provider.warm_up()
    app.state.voice_orchestrator = VoiceSessionOrchestrator(
        app.state.controller,
        stt_provider=app.state.stt_provider,
        requested_asr_mode=parse_asr_mode(settings.asr_mode),
        timing=VoiceTimingConfig(assistant_ack_timeout_ms=2000),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "runtime": settings.runtime,
            "stt_provider": app.state.stt_provider.name,
            "tts_provider": app.state.tts_provider.name,
            "requested_asr_mode": settings.asr_mode,
            "effective_asr_mode": app.state.voice_orchestrator.effective_asr_mode.value,
        }

    @app.on_event("shutdown")
    async def shutdown_runtime() -> None:
        shutdown = getattr(app.state.controller.runtime, "shutdown", None)
        if shutdown is not None:
            await shutdown()

    @app.get("/api/config/status")
    async def config_status() -> dict:
        from nextgen_voice_agent.config import CONFIG_FILE, get_settings, get_secret
        settings = get_settings()

        llm_provider = settings.router_model.split("/")[0].lower()
        stt_provider = settings.stt_provider.lower()
        tts_provider = settings.tts_provider.lower()

        providers_schema = {
            "stt": [
                {"id": "fake", "name": "Fake (Testing)", "required_keys": []},
                {"id": "nvidia_nim", "name": "NVIDIA NIM", "required_keys": [{"id": "nvidia_api_key"}]}
            ],
            "tts": [
                {"id": "browser_dev", "name": "Browser Native", "required_keys": []},
                {"id": "nvidia_nim", "name": "NVIDIA NIM", "required_keys": [{"id": "nvidia_api_key"}]},
                {"id": "elevenlabs", "name": "ElevenLabs", "required_keys": [{"id": "elevenlabs_api_key"}]},
                {"id": "sarvam", "name": "Sarvam AI", "required_keys": [{"id": "sarvam_api_key"}]}
            ],
            "llm": [
                {"id": "groq", "name": "Groq", "required_keys": [{"id": "groq_api_key"}]},
                {"id": "openai", "name": "OpenAI", "required_keys": [{"id": "openai_api_key"}]},
                {"id": "anthropic", "name": "Anthropic", "required_keys": [{"id": "anthropic_api_key"}]}
            ]
        }

        for category in providers_schema.values():
            for provider in category:
                for req_key in provider["required_keys"]:
                    req_key["is_configured"] = bool(get_secret(req_key["id"]))

        active_config = {
            "stt": stt_provider,
            "tts": tts_provider,
            "llm": llm_provider
        }

        missing_capabilities = []
        for category_id, active_provider_id in active_config.items():
            category_schema = providers_schema.get(category_id, [])
            provider_schema = next((p for p in category_schema if p["id"] == active_provider_id), None)
            
            if provider_schema:
                for req_key in provider_schema["required_keys"]:
                    if not req_key["is_configured"]:
                        missing_capabilities.append(category_id)
                        break

        from nextgen_voice_agent.config import Settings
        default_config = {
            "stt": Settings.model_fields["stt_provider"].default.lower(),
            "tts": Settings.model_fields["tts_provider"].default.lower(),
            "llm": Settings.model_fields["router_model"].default.split("/")[0].lower()
        }

        return {
            "system_ready": len(missing_capabilities) == 0,
            "missing_capabilities": missing_capabilities,
            "active_config": active_config,
            "default_config": default_config,
            "providers_schema": providers_schema,
            "config_path": str(CONFIG_FILE),
        }

    @app.post("/api/config/open")
    async def open_config() -> dict[str, str | bool]:
        from nextgen_voice_agent.platform.open_file import open_config_file

        return open_config_file()

    app.include_router(routes.router)
    app.include_router(websocket.router)
    mount_frontend(app)
    return app


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


app = create_app()
