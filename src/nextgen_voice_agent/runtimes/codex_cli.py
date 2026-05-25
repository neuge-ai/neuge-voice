from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from pathlib import Path

from nextgen_voice_agent.models.runtime import (
    RuntimeAmendment,
    RuntimeProgress,
    RuntimeResult,
    RuntimeResultStatus,
    RuntimeTaskRequest,
)
from nextgen_voice_agent.runtimes.base import RuntimeMessage, TaskRuntime


class CodexRuntimeState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class CodexCliRuntimeConfig:
    startup_timeout_seconds: float = 10.0
    total_timeout_seconds: float = 600.0
    termination_grace_seconds: float = 2.0


ProcessFactory = Callable[..., Awaitable[asyncio.subprocess.Process]]


class CodexCliRuntime(TaskRuntime):
    """Codex CLI runtime adapter using schema-constrained result files."""

    def __init__(
        self,
        command: str = "codex",
        config: CodexCliRuntimeConfig | None = None,
        process_factory: ProcessFactory | None = None,
        schema_path: Path | None = None,
    ) -> None:
        self.command = command
        self.config = config or CodexCliRuntimeConfig()
        self.process_factory = process_factory or asyncio.create_subprocess_exec
        self.schema_path = schema_path or self._default_schema_path()
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled_task_ids: set[str] = set()

    async def start_task(self, request: RuntimeTaskRequest) -> AsyncIterator[RuntimeMessage]:
        self._cancelled_task_ids.discard(request.task_id)
        yield self._progress(request, CodexRuntimeState.STARTING, "Starting Codex CLI process.")

        with tempfile.TemporaryDirectory(prefix=f"nextgen-codex-{request.task_id}-") as tmp_dir:
            result_path = Path(tmp_dir) / "runtime-result.json"
            process = await self._start_process(request, result_path)
            self._processes[request.task_id] = process
            yield self._progress(request, CodexRuntimeState.RUNNING, "Codex CLI process is running.")

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.config.total_timeout_seconds,
                )
            except TimeoutError:
                await self._terminate_process(request.task_id, process)
                yield RuntimeResult(
                    task_id=request.task_id,
                    generation=request.generation,
                    status=RuntimeResultStatus.FAILED,
                    error=f"Codex task timed out after {self.config.total_timeout_seconds:g} seconds.",
                    technical_summary=CodexRuntimeState.TIMED_OUT.value,
                )
                return
            except asyncio.CancelledError:
                await self._terminate_process(request.task_id, process)
                raise
            finally:
                self._processes.pop(request.task_id, None)

            if request.task_id in self._cancelled_task_ids:
                yield RuntimeResult(
                    task_id=request.task_id,
                    generation=request.generation,
                    status=RuntimeResultStatus.CANCELLED,
                    spoken_answer="The task was cancelled.",
                    technical_summary="Codex process was cancelled before its result was accepted.",
                )
                return

            if process.returncode != 0:
                yield RuntimeResult(
                    task_id=request.task_id,
                    generation=request.generation,
                    status=RuntimeResultStatus.FAILED,
                    error=self._decode(stderr) or f"Codex exited with {process.returncode}.",
                    technical_summary=self._decode(stdout)[-2000:],
                )
                return

            yield self._parse_result_file(request, result_path, stdout, stderr)

    async def amend_task(self, amendment: RuntimeAmendment) -> None:
        # Codex exec cannot accept live amendments. The controller owns generation
        # changes and stale-result filtering until a session-capable runtime exists.
        return None

    async def cancel_task(self, task_id: str) -> None:
        self._cancelled_task_ids.add(task_id)
        process = self._processes.get(task_id)
        if process is None or process.returncode is not None:
            return
        await self._terminate_process(task_id, process)

    async def _start_process(self, request: RuntimeTaskRequest, result_path: Path) -> asyncio.subprocess.Process:
        try:
            return await asyncio.wait_for(
                self.process_factory(
                    self.command,
                    "exec",
                    "--skip-git-repo-check",
                    "--output-schema",
                    str(self.schema_path),
                    "-o",
                    str(result_path),
                    request.prompt,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=self.config.startup_timeout_seconds,
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"Codex process did not start within {self.config.startup_timeout_seconds:g} seconds."
            ) from exc

    async def _terminate_process(self, task_id: str, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=self.config.termination_grace_seconds)
        except TimeoutError:
            process.kill()
            await process.wait()
        finally:
            self._processes.pop(task_id, None)

    def _parse_result_file(
        self,
        request: RuntimeTaskRequest,
        result_path: Path,
        stdout: bytes,
        stderr: bytes,
    ) -> RuntimeResult:
        if not result_path.exists():
            return RuntimeResult(
                task_id=request.task_id,
                generation=request.generation,
                status=RuntimeResultStatus.FAILED,
                error="Codex completed without writing the structured result file.",
                technical_summary=self._combined_output(stdout, stderr),
            )

        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            return RuntimeResult.model_validate(payload)
        except (json.JSONDecodeError, ValueError) as exc:
            return RuntimeResult(
                task_id=request.task_id,
                generation=request.generation,
                status=RuntimeResultStatus.FAILED,
                error=f"Codex result file was not valid RuntimeResult JSON: {exc}",
                technical_summary=self._combined_output(stdout, stderr),
            )

    def _progress(self, request: RuntimeTaskRequest, state: CodexRuntimeState, message: str) -> RuntimeProgress:
        return RuntimeProgress(
            task_id=request.task_id,
            generation=request.generation,
            message=f"{state.value}: {message}",
        )

    def _combined_output(self, stdout: bytes, stderr: bytes) -> str:
        output = "\n".join(part for part in [self._decode(stdout), self._decode(stderr)] if part)
        return output[-2000:]

    def _decode(self, payload: bytes) -> str:
        return payload.decode(errors="replace").strip()

    def _default_schema_path(self) -> Path:
        return Path(str(resources.files("nextgen_voice_agent.runtimes").joinpath("runtime_result.schema.json")))
