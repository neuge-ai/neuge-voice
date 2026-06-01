from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Iterator

import click

from nextgen_voice_agent.config import CONFIG_FILE, load_toml_config


def _run(args: list[str]) -> int:
    try:
        return subprocess.call(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return 127


def _editor_attempts(path: str) -> Iterator[tuple[str, list[str]]]:
    for env_name in ("VISUAL", "EDITOR"):
        env_value = os.environ.get(env_name)
        if not env_value:
            continue
        yield env_name.lower(), [*shlex.split(env_value), path]

    for binary, args in (
        ("cursor", ["--goto", path]),
        ("code", ["--goto", path]),
        ("code", [path]),
    ):
        if shutil.which(binary):
            yield binary, [binary, *args]


def open_config_file() -> dict[str, str | bool]:
    load_toml_config()
    path = str(CONFIG_FILE)

    for method, args in _editor_attempts(path):
        if _run(args) == 0:
            return {"opened": True, "path": path, "method": method}

    try:
        exit_code = click.launch(path, locate=False)
    except Exception as exc:
        return {"opened": False, "path": path, "error": str(exc)}

    if exit_code == 0:
        return {"opened": True, "path": path, "method": "click.launch"}

    return {
        "opened": False,
        "path": path,
        "error": f"Could not open config file (exit {exit_code})",
    }
