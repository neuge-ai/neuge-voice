from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import Any

from nextgen_voice_agent.models.runtime import (
    RuntimeAmendment,
    RuntimeProgress,
    RuntimeResult,
    RuntimeResultStatus,
    RuntimeTaskRequest,
)
from nextgen_voice_agent.runtimes.base import RuntimeMessage, TaskRuntime

logger = logging.getLogger(__name__)


class CodexRuntimeState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class CodexAppServerRuntimeConfig:
    startup_timeout_seconds: float = 10.0
    total_timeout_seconds: float = 600.0
    termination_grace_seconds: float = 2.0
    request_timeout_seconds: float = 30.0


ProcessFactory = Callable[..., Awaitable[asyncio.subprocess.Process]]


class CodexAppServerError(RuntimeError):
    pass


class CodexAppServerClient:
    """Small JSON-RPC client for `codex app-server` over newline-delimited stdio."""

    def __init__(
        self,
        command: str = "codex",
        config: CodexAppServerRuntimeConfig | None = None,
        process_factory: ProcessFactory | None = None,
    ) -> None:
        self.command = command
        self.config = config or CodexAppServerRuntimeConfig()
        self.process_factory = process_factory or asyncio.create_subprocess_exec
        self.process: asyncio.subprocess.Process | None = None
        self._next_request_id = 1
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._notifications: set[asyncio.Queue[dict[str, Any]]] = set()
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._initialized = False

    async def start(self) -> None:
        async with self._start_lock:
            if self.process is not None and self.process.returncode is None and self._initialized:
                return
            if self.process is None or self.process.returncode is not None:
                try:
                    self.process = await asyncio.wait_for(
                        self.process_factory(
                            self.command,
                            "app-server",
                            "--listen",
                            "stdio://",
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        ),
                        timeout=self.config.startup_timeout_seconds,
                    )
                except TimeoutError as exc:
                    raise TimeoutError(
                        f"Codex app-server did not start within {self.config.startup_timeout_seconds:g} seconds."
                    ) from exc

                logger.info("Codex app-server started with pid %s", getattr(self.process, "pid", None))
                self._reader_task = asyncio.create_task(self._read_stdout_loop())
                self._stderr_task = asyncio.create_task(self._read_stderr_loop())
            await self._initialize()

    async def stop(self) -> None:
        process = self.process
        if process is None:
            return
        self._fail_pending(CodexAppServerError("Codex app-server is shutting down."))
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=self.config.termination_grace_seconds)
            except TimeoutError:
                process.kill()
                await process.wait()
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
        self.process = None
        self._initialized = False

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        await self.start()
        return await self._request_started(method, params)

    async def _request_started(self, method: str, params: dict[str, Any] | None = None) -> Any:
        process = self.process
        if process is None or process.stdin is None or process.returncode is not None:
            raise CodexAppServerError("Codex app-server is not running.")

        request_id = self._next_request_id
        self._next_request_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = future
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params

        async with self._write_lock:
            try:
                process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
                await process.stdin.drain()
            except Exception:
                self._pending.pop(request_id, None)
                raise

        try:
            return await asyncio.wait_for(future, timeout=self.config.request_timeout_seconds)
        except TimeoutError:
            self._pending.pop(request_id, None)
            raise TimeoutError(f"Codex app-server request timed out: {method}")

    async def _initialize(self) -> None:
        if self._initialized:
            return
        await self._request_started(
            "initialize",
            {
                "clientInfo": {
                    "name": "neuge-voice-agent",
                    "title": "Neuge Voice Agent",
                    "version": "0.1.0",
                },
                "capabilities": {
                    "experimentalApi": True,
                    "requestAttestation": False,
                    "optOutNotificationMethods": [],
                },
            },
        )
        self._initialized = True

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._notifications.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._notifications.discard(queue)

    async def _read_stdout_loop(self) -> None:
        assert self.process is not None
        stdout = self.process.stdout
        if stdout is None:
            self._fail_pending(CodexAppServerError("Codex app-server stdout pipe is unavailable."))
            return
        try:
            while True:
                line = await stdout.readline()
                if not line:
                    break
                try:
                    message = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    logger.warning("Ignoring non-JSON app-server stdout line: %r", line[:500])
                    continue
                self._dispatch_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Codex app-server stdout reader failed")
            self._fail_pending(exc)
        finally:
            returncode = self.process.returncode if self.process else None
            message = (
                f"Codex app-server exited with {returncode}."
                if returncode is not None
                else "Codex app-server stdout closed."
            )
            self._fail_pending(CodexAppServerError(message))

    async def _read_stderr_loop(self) -> None:
        assert self.process is not None
        stderr = self.process.stderr
        if stderr is None:
            return
        try:
            while True:
                line = await stderr.readline()
                if not line:
                    break
                logger.info("codex app-server stderr: %s", line.decode(errors="replace").rstrip())
        except asyncio.CancelledError:
            raise

    def _dispatch_message(self, message: dict[str, Any]) -> None:
        if "id" in message:
            request_id = message["id"]
            if not isinstance(request_id, int):
                return
            future = self._pending.pop(request_id, None)
            if future is None or future.done():
                return
            if "error" in message:
                future.set_exception(CodexAppServerError(str(message["error"])))
            else:
                future.set_result(message.get("result"))
            return

        if "method" in message:
            for queue in list(self._notifications):
                queue.put_nowait(message)

    def _fail_pending(self, exc: BaseException) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()


class CodexAppServerRuntime(TaskRuntime):
    """Codex runtime adapter backed by a persistent `codex app-server` stdio process."""

    supports_live_amendments = True

    def __init__(
        self,
        command: str = "codex",
        model: str | None = None,
        config: CodexAppServerRuntimeConfig | None = None,
        process_factory: ProcessFactory | None = None,
        schema_path: Path | None = None,
        client: CodexAppServerClient | None = None,
    ) -> None:
        self.command = command
        self.model = model
        self.config = config or CodexAppServerRuntimeConfig()
        self.process_factory = process_factory or asyncio.create_subprocess_exec
        self.schema_path = schema_path or self._default_schema_path()
        self.client = client or CodexAppServerClient(
            command=command,
            config=self.config,
            process_factory=self.process_factory,
        )
        self._tasks: dict[str, _CodexTaskState] = {}
        self._cancelled_task_ids: set[str] = set()

    async def start_task(self, request: RuntimeTaskRequest) -> AsyncIterator[RuntimeMessage]:
        self._cancelled_task_ids.discard(request.task_id)
        yield self._progress(request, CodexRuntimeState.STARTING, "Starting Codex app-server task.")

        event_queue: asyncio.Queue[RuntimeMessage] = asyncio.Queue()
        notification_worker: asyncio.Task[None] | None = None
        task_state: _CodexTaskState | None = None

        try:
            thread_response = await self.client.request("thread/start", self._thread_start_params())
            thread_id = self._extract_thread_id(thread_response)
            if not thread_id:
                raise CodexAppServerError("Codex app-server did not return a thread id.")

            notification_worker = asyncio.create_task(self._collect_notifications(request, thread_id, event_queue))
            turn_response = await self.client.request("turn/start", self._turn_start_params(request, thread_id))
            turn_id = self._extract_turn_id(turn_response)
            if not turn_id:
                raise CodexAppServerError("Codex app-server did not return a turn id.")

            task_state = _CodexTaskState(thread_id=thread_id, turn_id=turn_id, generation=request.generation)
            self._tasks[request.task_id] = task_state
            yield self._progress(request, CodexRuntimeState.RUNNING, "Codex app-server turn is running.")

            while True:
                message = await asyncio.wait_for(event_queue.get(), timeout=self.config.total_timeout_seconds)
                yield message
                if isinstance(message, RuntimeResult):
                    return
        except TimeoutError:
            if task_state is not None:
                await self._interrupt_task(task_state)
            yield RuntimeResult(
                task_id=request.task_id,
                generation=task_state.generation if task_state is not None else request.generation,
                status=RuntimeResultStatus.FAILED,
                error=f"Codex task timed out after {self.config.total_timeout_seconds:g} seconds.",
                technical_summary=CodexRuntimeState.TIMED_OUT.value,
            )
        except asyncio.CancelledError:
            if task_state is not None:
                await self._interrupt_task(task_state)
            raise
        except Exception as exc:
            logger.exception("Codex app-server task failed for %s", request.task_id)
            yield RuntimeResult(
                task_id=request.task_id,
                generation=task_state.generation if task_state is not None else request.generation,
                status=RuntimeResultStatus.FAILED,
                error=str(exc),
                technical_summary=CodexRuntimeState.FAILED.value,
            )
        finally:
            self._tasks.pop(request.task_id, None)
            if notification_worker and not notification_worker.done():
                notification_worker.cancel()
                try:
                    await notification_worker
                except asyncio.CancelledError:
                    pass

    async def amend_task(self, amendment: RuntimeAmendment) -> None:
        state = self._tasks.get(amendment.task_id)
        if state is None:
            raise CodexAppServerError(f"No active Codex app-server turn for task {amendment.task_id}.")
        state.generation = amendment.generation
        await self.client.request(
            "turn/steer",
            {
                "threadId": state.thread_id,
                "expectedTurnId": state.turn_id,
                "input": [{"type": "text", "text": amendment.amendment, "text_elements": []}],
            },
        )

    async def cancel_task(self, task_id: str) -> None:
        self._cancelled_task_ids.add(task_id)
        state = self._tasks.get(task_id)
        if state is not None:
            await self._interrupt_task(state)

    async def shutdown(self) -> None:
        await self.client.stop()

    async def _collect_notifications(
        self,
        request: RuntimeTaskRequest,
        thread_id: str,
        event_queue: asyncio.Queue[RuntimeMessage],
    ) -> None:
        event_summaries: list[str] = []
        async for notification in self.client.subscribe():
            method = str(notification.get("method", ""))
            params = notification.get("params") or {}
            if not isinstance(params, dict) or params.get("threadId") != thread_id:
                continue

            turn_id = params.get("turnId")
            state = self._tasks.get(request.task_id)
            if state is not None and turn_id is not None and turn_id != state.turn_id:
                continue

            generation = state.generation if state is not None else request.generation
            progress = self._notification_progress(request, generation, method, params)
            if progress:
                event_summaries.append(progress.message)
                await event_queue.put(progress)

            if method == "turn/completed":
                final_text = self._extract_final_assistant_text(params.get("turn"))
                await event_queue.put(
                    self._build_result(
                        request,
                        generation,
                        final_text,
                        "\n".join(event_summaries)[-2000:],
                    )
                )
                return

    def _thread_start_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "approvalPolicy": "never",
            "cwd": os.getcwd(),
            "ephemeral": True,
        }
        if self.model:
            params["model"] = self.model
        return params

    def _turn_start_params(self, request: RuntimeTaskRequest, thread_id: str) -> dict[str, Any]:
        return {
            "threadId": thread_id,
            "input": [{"type": "text", "text": request.prompt, "text_elements": []}],
            "outputSchema": self._load_output_schema(),
        }

    async def _interrupt_task(self, state: _CodexTaskState) -> None:
        try:
            await self.client.request("turn/interrupt", {"threadId": state.thread_id, "turnId": state.turn_id})
        except Exception:
            logger.exception("Failed to interrupt Codex app-server turn %s", state.turn_id)

    def _build_result(
        self,
        request: RuntimeTaskRequest,
        generation: int,
        final_text: str,
        technical_summary: str,
    ) -> RuntimeResult:
        if request.task_id in self._cancelled_task_ids:
            return RuntimeResult(
                task_id=request.task_id,
                generation=generation,
                status=RuntimeResultStatus.CANCELLED,
                spoken_answer="The task was cancelled.",
                technical_summary="Codex turn was cancelled before its result was accepted.",
            )
        try:
            result = self._parse_runtime_result_json(final_text)
            return result.model_copy(update={"task_id": request.task_id, "generation": generation})
        except ValueError as exc:
            return RuntimeResult(
                task_id=request.task_id,
                generation=generation,
                status=RuntimeResultStatus.FAILED,
                error=f"Codex final message was not valid RuntimeResult JSON: {exc}",
                technical_summary=(technical_summary or final_text)[-2000:],
            )

    def _notification_progress(
        self,
        request: RuntimeTaskRequest,
        generation: int,
        method: str,
        params: dict[str, Any],
    ) -> RuntimeProgress | None:
        if method == "turn/started":
            return self._progress(request.task_id, generation, CodexRuntimeState.RUNNING, "Codex turn started.")
        if method == "turn/completed":
            return self._progress(request.task_id, generation, CodexRuntimeState.COMPLETED, "Codex turn completed.")
        if method == "item/started":
            return self._progress(request.task_id, generation, CodexRuntimeState.RUNNING, "Codex started a work item.")
        if method == "item/completed":
            return self._progress(request.task_id, generation, CodexRuntimeState.RUNNING, "Codex completed a work item.")
        if method in {
            "plan/delta",
            "agentMessage/delta",
            "item/agentMessage/delta",
            "reasoningText/delta",
            "reasoningSummaryText/delta",
        }:
            delta = params.get("delta")
            if isinstance(delta, str) and delta.strip():
                return self._progress(request.task_id, generation, CodexRuntimeState.RUNNING, delta.strip()[:500])
        if method in {"commandExecOutput/delta", "process/output/delta", "commandExecution/output/delta"}:
            return self._progress(request.task_id, generation, CodexRuntimeState.RUNNING, "Codex produced command output.")
        if method in {"warning", "error"}:
            message = params.get("message") or params.get("error") or method
            return self._progress(request.task_id, generation, CodexRuntimeState.RUNNING, str(message)[:500])
        return None

    def _extract_thread_id(self, response: Any) -> str | None:
        if not isinstance(response, dict):
            return None
        thread = response.get("thread")
        if isinstance(thread, dict):
            value = thread.get("id") or thread.get("threadId")
            if isinstance(value, str):
                return value
        value = response.get("threadId") or response.get("id")
        return value if isinstance(value, str) else None

    def _extract_turn_id(self, response: Any) -> str | None:
        if not isinstance(response, dict):
            return None
        turn = response.get("turn")
        if isinstance(turn, dict):
            value = turn.get("id") or turn.get("turnId")
            if isinstance(value, str):
                return value
        value = response.get("turnId") or response.get("id")
        return value if isinstance(value, str) else None

    def _extract_final_assistant_text(self, turn: Any) -> str:
        if not isinstance(turn, dict):
            return ""
        items = turn.get("items")
        if not isinstance(items, list):
            return ""
        assistant_texts: list[tuple[bool, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in {"agentMessage", "assistant_message", "assistant"}:
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            assistant_texts.append((item.get("phase") == "final_answer", text.strip()))
        for is_final, text in reversed(assistant_texts):
            if is_final:
                return text
        if assistant_texts:
            return assistant_texts[-1][1]
        return ""

    def _parse_runtime_result_json(self, text: str) -> RuntimeResult:
        candidate = self._unwrap_json_fence(text.strip())
        try:
            return RuntimeResult.model_validate_json(candidate)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    def _unwrap_json_fence(self, text: str) -> str:
        match = re.fullmatch(r"```(?:json)?\s*\n(?P<body>[\s\S]*?)\n?```", text, flags=re.IGNORECASE)
        if match is None:
            return text
        return match.group("body").strip()

    def _progress(
        self,
        task_id: str | RuntimeTaskRequest,
        generation: int | CodexRuntimeState,
        state: CodexRuntimeState | str,
        message: str | None = None,
    ) -> RuntimeProgress:
        if isinstance(task_id, RuntimeTaskRequest):
            request = task_id
            assert isinstance(generation, CodexRuntimeState)
            assert isinstance(state, str)
            return RuntimeProgress(
                task_id=request.task_id,
                generation=request.generation,
                message=f"{generation.value}: {state}",
            )
        assert isinstance(generation, int)
        assert isinstance(state, CodexRuntimeState)
        assert message is not None
        return RuntimeProgress(
            task_id=task_id,
            generation=generation,
            message=f"{state.value}: {message}",
        )

    def _load_output_schema(self) -> dict[str, Any]:
        return json.loads(self.schema_path.read_text(encoding="utf-8"))

    def _default_schema_path(self) -> Path:
        return Path(str(resources.files("nextgen_voice_agent.runtimes").joinpath("runtime_result.schema.json")))


@dataclass
class _CodexTaskState:
    thread_id: str
    turn_id: str
    generation: int
