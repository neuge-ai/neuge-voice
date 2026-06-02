from __future__ import annotations

import ast
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1] / "src" / "nextgen_voice_agent" / "server"


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def _is_private_name(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def _private_orchestrator_violations(path: Path, source: str) -> list[str]:
    violations: list[str] = []
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "orchestrator" in node.module:
            for alias in node.names:
                if _is_private_name(alias.name):
                    violations.append(f"{path}:{node.lineno}: imports private symbol {alias.name} from {node.module}")
        if isinstance(node, ast.Attribute) and _is_private_name(node.attr):
            owner = ast.unparse(node.value)
            owner_parts = owner.split(".")
            if "orchestrator" in owner_parts or "voice_orchestrator" in owner_parts:
                violations.append(f"{path}:{node.lineno}: accesses private orchestrator attribute .{node.attr}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "._" in node.value and "orchestrator" in node.value.lower():
            violations.append(f"{path}:{node.lineno}: references private orchestrator API in string {node.value!r}")
    return violations


def test_private_orchestrator_detector_catches_common_server_leaks() -> None:
    source = """
async def route(orchestrator):
    await orchestrator._emit()
    await app.state.voice_orchestrator._handle_user_turn()
"""
    violations = _private_orchestrator_violations(Path("sample.py"), source)
    assert "._emit" in "\n".join(violations)
    assert "._handle_user_turn" in "\n".join(violations)


def test_server_package_does_not_use_private_orchestrator_api() -> None:
    all_violations: list[str] = []
    for path in _iter_python_files(SERVER_ROOT):
        source = path.read_text(encoding="utf-8")
        all_violations.extend(_private_orchestrator_violations(path, source))
    assert not all_violations, "server/ must not call VoiceSessionOrchestrator private APIs:\n" + "\n".join(all_violations)
