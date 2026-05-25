from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nextgen_voice_agent.config import get_settings
from nextgen_voice_agent.server.dependencies import create_controller, create_stt, create_tts
from nextgen_voice_agent.server import routes, websocket
from nextgen_voice_agent.voice.orchestrator import VoiceSessionOrchestrator
from nextgen_voice_agent.voice.stt import parse_asr_mode


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)
    app.state.controller = create_controller(settings)
    app.state.stt_provider = create_stt(settings)
    app.state.tts_provider = create_tts(settings)
    app.state.voice_orchestrator = VoiceSessionOrchestrator(
        app.state.controller,
        stt_provider=app.state.stt_provider,
        requested_asr_mode=parse_asr_mode(settings.asr_mode),
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

    app.include_router(routes.router)
    app.include_router(websocket.router)
    return app


app = create_app()
