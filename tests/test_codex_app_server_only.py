from pathlib import Path


def test_no_legacy_codex_cli_modules_or_task_execution() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    assert not (repo_root / "src/nextgen_voice_agent/runtimes/codex_cli.py").exists()
    assert not (repo_root / "src/nextgen_voice_agent/server/codex_cli.py").exists()

    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (repo_root / "src/nextgen_voice_agent").rglob("*.py")
        if "__pycache__" not in path.parts
    )
    assert "CodexCliRuntime" not in source
    assert "codex_cli" not in source
    assert '"codex", "exec"' not in source
    assert "'codex', 'exec'" not in source
