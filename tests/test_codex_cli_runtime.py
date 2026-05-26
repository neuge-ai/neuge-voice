from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from nextgen_voice_agent.models.runtime import RuntimeResult, RuntimeResultStatus, RuntimeTaskRequest
from nextgen_voice_agent.runtimes.codex_cli import CodexCliRuntime, CodexCliRuntimeConfig


class FakeProcess:
    def __init__(
        self,
        *,
        result_payload: dict[str, Any] | str | None = None,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        block_until_cancelled: bool = False,
    ) -> None:
        self.result_payload = result_payload
        self.returncode: int | None = None
        self.final_returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.block_until_cancelled = block_until_cancelled
        self.terminated = False
        self.killed = False
        self.args: tuple[Any, ...] = ()
        self._done = asyncio.Event()

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.block_until_cancelled:
            await self._done.wait()
            return self.stdout, self.stderr

        self._write_result_file()
        self.returncode = self.final_returncode
        self._done.set()
        return self.stdout, self.stderr

    async def wait(self) -> int:
        await self._done.wait()
        return self.returncode or 0

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self._done.set()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._done.set()

    def _write_result_file(self) -> None:
        if self.result_payload is None:
            return
        result_path = Path(self.args[self.args.index("-o") + 1])
        if isinstance(self.result_payload, str):
            result_path.write_text(self.result_payload, encoding="utf-8")
        else:
            result_path.write_text(json.dumps(self.result_payload), encoding="utf-8")


def runtime_request() -> RuntimeTaskRequest:
    return RuntimeTaskRequest(
        task_id="task_codex",
        generation=1,
        prompt="Return structured JSON.",
        original_request="Do the thing.",
    )


async def collect_results(runtime: CodexCliRuntime) -> list[Any]:
    return [message async for message in runtime.start_task(runtime_request())]


@pytest.mark.asyncio
async def test_codex_cli_uses_schema_and_result_file() -> None:
    result = RuntimeResult(
        task_id="task_codex",
        generation=1,
        status=RuntimeResultStatus.COMPLETED,
        spoken_answer="Done.",
    )
    process = FakeProcess(result_payload=result.model_dump(mode="json"))

    async def process_factory(*args: Any, **kwargs: Any) -> FakeProcess:
        process.args = args
        return process

    runtime = CodexCliRuntime(command="codex", process_factory=process_factory)

    messages = await collect_results(runtime)

    assert process.args[:4] == ("codex", "-a", "never", "exec")
    assert "--skip-git-repo-check" in process.args
    assert "--output-schema" in process.args
    assert "-o" in process.args
    assert process.args[-1] == "Return structured JSON."
    assert messages[-1] == result


@pytest.mark.asyncio
async def test_codex_cli_passes_configured_model() -> None:
    result = RuntimeResult(
        task_id="task_codex",
        generation=1,
        status=RuntimeResultStatus.COMPLETED,
        spoken_answer="Done.",
    )
    process = FakeProcess(result_payload=result.model_dump(mode="json"))

    async def process_factory(*args: Any, **kwargs: Any) -> FakeProcess:
        process.args = args
        return process

    runtime = CodexCliRuntime(model="gpt-5.4-mini", process_factory=process_factory)

    await collect_results(runtime)

    assert "-m" in process.args
    assert process.args[process.args.index("-m") + 1] == "gpt-5.4-mini"
    assert process.args[-1] == "Return structured JSON."


@pytest.mark.asyncio
async def test_codex_cli_invalid_result_file_fails() -> None:
    process = FakeProcess(result_payload="{not-json")

    async def process_factory(*args: Any, **kwargs: Any) -> FakeProcess:
        process.args = args
        return process

    runtime = CodexCliRuntime(process_factory=process_factory)

    messages = await collect_results(runtime)

    assert messages[-1].status == RuntimeResultStatus.FAILED
    assert "not valid RuntimeResult JSON" in messages[-1].error


@pytest.mark.asyncio
async def test_codex_cli_nonzero_exit_fails_with_stderr() -> None:
    process = FakeProcess(returncode=2, stderr=b"bad credentials")

    async def process_factory(*args: Any, **kwargs: Any) -> FakeProcess:
        process.args = args
        return process

    runtime = CodexCliRuntime(process_factory=process_factory)

    messages = await collect_results(runtime)

    assert messages[-1].status == RuntimeResultStatus.FAILED
    assert messages[-1].error == "bad credentials"


@pytest.mark.asyncio
async def test_codex_cli_timeout_terminates_process() -> None:
    process = FakeProcess(block_until_cancelled=True)

    async def process_factory(*args: Any, **kwargs: Any) -> FakeProcess:
        process.args = args
        return process

    runtime = CodexCliRuntime(
        process_factory=process_factory,
        config=CodexCliRuntimeConfig(total_timeout_seconds=0.01, termination_grace_seconds=0.01),
    )

    messages = await collect_results(runtime)

    assert process.terminated
    assert messages[-1].status == RuntimeResultStatus.FAILED
    assert "timed out" in messages[-1].error


@pytest.mark.asyncio
async def test_codex_cli_cancel_terminates_process_and_ignores_result() -> None:
    process = FakeProcess(
        result_payload={
            "task_id": "task_codex",
            "generation": 1,
            "status": "completed",
            "spoken_answer": "Late answer.",
        },
        block_until_cancelled=True,
    )

    async def process_factory(*args: Any, **kwargs: Any) -> FakeProcess:
        process.args = args
        return process

    runtime = CodexCliRuntime(process_factory=process_factory)
    worker = asyncio.create_task(collect_results(runtime))
    await asyncio.sleep(0)

    await runtime.cancel_task("task_codex")
    messages = await worker

    assert process.terminated
    assert messages[-1].status == RuntimeResultStatus.CANCELLED
    assert "Late answer" not in messages[-1].spoken_answer
