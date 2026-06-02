from __future__ import annotations

from typing import Any

from nextgen_voice_agent import main as cli


def test_cli_prints_hashrouter_url_without_opening_for_dynamic_port(monkeypatch, capsys) -> None:
    run_calls: list[dict[str, Any]] = []
    opened: list[str] = []

    monkeypatch.setattr(cli, "get_free_port", lambda: 54321)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(cli, "wait_for_backend_health", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "create_app", lambda settings=None: object())
    monkeypatch.setattr(
        cli.uvicorn,
        "run",
        lambda app, host, port, log_level: run_calls.append({"host": host, "port": port, "log_level": log_level}),
    )
    monkeypatch.setattr("sys.argv", ["neuge", "--dynamic-port"])

    cli.main()

    output = capsys.readouterr().out
    assert "PORT:54321" in output
    assert "URL:http://127.0.0.1:54321/#/" in output
    assert opened == []
    assert run_calls == [{"host": "127.0.0.1", "port": 54321, "log_level": "info"}]


def test_cli_opens_browser_by_default_for_plain_cli(monkeypatch, capsys) -> None:
    opened: list[str] = []

    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(cli, "create_app", lambda settings=None: object())
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr("sys.argv", ["neuge", "--port", "8123"])

    cli.main()

    output = capsys.readouterr().out
    assert "URL:http://127.0.0.1:8123/#/" in output
    assert opened == ["http://127.0.0.1:8123/#/"]


def test_cli_no_open_suppresses_browser(monkeypatch) -> None:
    opened: list[str] = []

    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(cli, "create_app", lambda settings=None: object())
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr("sys.argv", ["neuge", "--port", "8123", "--no-open"])

    cli.main()

    assert opened == []
