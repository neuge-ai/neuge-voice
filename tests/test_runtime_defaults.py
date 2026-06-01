from nextgen_voice_agent.config import Settings
from nextgen_voice_agent.runtimes.codex_runtime import CodexAppServerRuntime
from nextgen_voice_agent.runtimes.fake import FakeRuntime
from nextgen_voice_agent.server.dependencies import create_controller


def test_default_runtime_is_codex_app_server() -> None:
    settings = Settings()
    controller = create_controller(settings)

    assert settings.runtime == "codex_app_server"
    assert isinstance(controller.runtime, CodexAppServerRuntime)


def test_fake_runtime_remains_explicitly_available() -> None:
    controller = create_controller(Settings(runtime="fake"))

    assert isinstance(controller.runtime, FakeRuntime)
