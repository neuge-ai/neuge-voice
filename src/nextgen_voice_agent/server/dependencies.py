from starlette.requests import HTTPConnection

from nextgen_voice_agent.agent.controller import AgentController
from nextgen_voice_agent.config import Settings, get_settings
from nextgen_voice_agent.runtimes.codex_runtime import CodexAppServerRuntime
from nextgen_voice_agent.runtimes.fake import FakeRuntime
from nextgen_voice_agent.voice.stt import SttProvider, create_stt_provider
from nextgen_voice_agent.voice.tts import TtsProvider, create_tts_provider
from nextgen_voice_agent.voice.orchestrator import VoiceSessionOrchestrator
from nextgen_voice_agent.server.codex_app_server import CodexAppServerService
from nextgen_voice_agent.transports.webrtc import WebRTCSessionManager


def create_controller(settings: Settings | None = None) -> AgentController:
    settings = settings or get_settings()
    if settings.runtime == "codex_app_server":
        runtime = CodexAppServerRuntime(command=settings.codex_command, model=settings.codex_model)
    else:
        runtime = FakeRuntime()
    return AgentController(runtime=runtime, settings=settings)


async def get_controller(connection: HTTPConnection) -> AgentController:
    return connection.app.state.controller


async def get_voice_orchestrator(connection: HTTPConnection) -> VoiceSessionOrchestrator:
    return connection.app.state.voice_orchestrator


async def get_webrtc_session_manager(connection: HTTPConnection) -> WebRTCSessionManager:
    return connection.app.state.webrtc_session_manager


async def get_tts_provider(connection: HTTPConnection) -> TtsProvider:
    return connection.app.state.tts_provider


def create_stt(settings: Settings | None = None) -> SttProvider:
    return create_stt_provider(settings or get_settings())


def create_tts(settings: Settings | None = None) -> TtsProvider:
    return create_tts_provider(settings or get_settings())


async def get_codex_app_server_service(connection: HTTPConnection) -> CodexAppServerService:
    return connection.app.state.codex_app_server_service
