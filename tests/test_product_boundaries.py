from __future__ import annotations

import ast
from pathlib import Path

import pytest

from nextgen_voice_agent.config import Settings
from nextgen_voice_agent.server.app import create_app
from nextgen_voice_agent.voice.session_context import VoiceSessionContext
from nextgen_voice_agent.models.voice import VoiceTransportKind


SERVER_ROOT = Path(__file__).resolve().parents[1] / "src" / "nextgen_voice_agent" / "server"
WEBRTC_PATH = Path(__file__).resolve().parents[1] / "src" / "nextgen_voice_agent" / "transports" / "webrtc.py"


def test_server_routes_do_not_access_orchestrator_sessions() -> None:
    source = (SERVER_ROOT / "routes.py").read_text(encoding="utf-8")
    assert "orchestrator.sessions" not in source


def test_webrtc_manager_is_not_a_routes_global() -> None:
    source = (SERVER_ROOT / "routes.py").read_text(encoding="utf-8")
    assert "webrtc_manager = None" not in source
    assert "global webrtc_manager" not in source


def test_webrtc_does_not_access_orchestrator_sessions() -> None:
    source = WEBRTC_PATH.read_text(encoding="utf-8")
    assert "orchestrator.sessions" not in source
    assert "active_utterance" not in source


@pytest.mark.asyncio
async def test_ensure_session_and_ingest_audio_frame_public_api() -> None:
    from nextgen_voice_agent.agent.controller import AgentController
    from nextgen_voice_agent.runtimes.fake import FakeRuntime
    from nextgen_voice_agent.voice.orchestrator import VoiceSessionOrchestrator
    from nextgen_voice_agent.voice.stt import AudioFrame, FakeSttProvider

    controller = AgentController(runtime=FakeRuntime())
    orchestrator = VoiceSessionOrchestrator(
        controller,
        stt_provider=FakeSttProvider("hello"),
    )
    context = VoiceSessionContext(
        session_id="boundary-session",
        transport=VoiceTransportKind.BROWSER,
    )
    session_id = await orchestrator.ensure_session(context)
    assert orchestrator.has_session(session_id)
    frame = AudioFrame(
        payload=b"\x00\x00",
        sample_rate=16000,
        channels=1,
        encoding="pcm_s16le",
        sequence=0,
        speech_segment_id="seg-1",
    )
    await orchestrator.ingest_audio_frame(session_id, frame)
    await orchestrator.end_session(session_id, "test_done")
    assert not orchestrator.has_session(session_id)


def test_create_app_test_mode_policy() -> None:
    app = create_app(Settings(backend_run_mode="test", ui_serving_mode="dev_server"))
    assert app.state.runtime_policy.ui_serving_mode.value == "dev_server"
    assert app.state.runtime_policy.should_mount_frontend is False
