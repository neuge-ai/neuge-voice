from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from nextgen_voice_agent.models.runtime import RuntimeAmendment, RuntimeResult, RuntimeResultStatus, RuntimeTaskRequest
from nextgen_voice_agent.runtimes.codex_runtime import (
    CodexAppServerClient,
    CodexAppServerRuntime,
    CodexAppServerRuntimeConfig,
)


def runtime_request() -> RuntimeTaskRequest:
    return RuntimeTaskRequest(
        task_id="task_codex",
        generation=1,
        prompt="Return structured JSON.",
        original_request="Do the thing.",
    )


def completed_turn_notification(result_text: str, *, extra_items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "method": "turn/completed",
        "params": {
            "threadId": "thread_1",
            "turnId": "turn_1",
            "turn": {
                "id": "turn_1",
                "items": [
                    *(extra_items or []),
                    {
                        "id": "item_final",
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": result_text,
                    },
                ],
            },
        },
    }


class FakeClient:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any] | None]] = []
        self.notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.interrupted: dict[str, str] | None = None
        self.steered: dict[str, Any] | None = None

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.requests.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "thread_1"}}
        if method == "turn/start":
            return {"turn": {"id": "turn_1"}}
        if method == "turn/interrupt":
            assert params is not None
            self.interrupted = {"threadId": params["threadId"], "turnId": params["turnId"]}
            await self.notifications.put(
                {
                    "method": "turn/completed",
                    "params": {"threadId": params["threadId"], "turnId": params["turnId"], "turn": {"id": params["turnId"]}},
                }
            )
            return {}
        if method == "turn/steer":
            assert params is not None
            self.steered = params
            return {}
        raise AssertionError(f"Unexpected method {method}")

    async def subscribe(self):
        while True:
            yield await self.notifications.get()

    async def stop(self) -> None:
        return None


async def collect_results(runtime: CodexAppServerRuntime) -> list[Any]:
    return [message async for message in runtime.start_task(runtime_request())]


@pytest.mark.asyncio
async def test_codex_app_server_client_spawns_and_routes_response() -> None:
    class FakeStdout:
        def __init__(self) -> None:
            self.lines: asyncio.Queue[bytes] = asyncio.Queue()

        async def readline(self) -> bytes:
            return await self.lines.get()

    class FakeStderr(FakeStdout):
        pass

    class FakeStdin:
        def __init__(self, stdout: FakeStdout) -> None:
            self.stdout = stdout
            self.writes: list[dict[str, Any]] = []

        def write(self, data: bytes) -> None:
            payload = json.loads(data.decode("utf-8"))
            self.writes.append(payload)
            self.stdout.lines.put_nowait(
                json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": {"ok": True}}).encode("utf-8") + b"\n"
            )

        async def drain(self) -> None:
            return None

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode = None
            self.stdout = FakeStdout()
            self.stderr = FakeStderr()
            self.stdin = FakeStdin(self.stdout)
            self.args: tuple[Any, ...] = ()
            self.pid = 123

        def terminate(self) -> None:
            self.returncode = -15
            self.stdout.lines.put_nowait(b"")
            self.stderr.lines.put_nowait(b"")

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            return self.returncode or 0

    process = FakeProcess()

    async def process_factory(*args: Any, **kwargs: Any) -> FakeProcess:
        process.args = args
        return process

    client = CodexAppServerClient(process_factory=process_factory)

    response = await client.request("example/method", {"x": 1})
    await client.stop()

    assert process.args == ("codex", "app-server", "--listen", "stdio://")
    assert process.stdin.writes[0]["method"] == "initialize"
    assert process.stdin.writes[0]["params"] == {
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
    }
    assert process.stdin.writes[1]["method"] == "example/method"
    assert response == {"ok": True}


@pytest.mark.asyncio
async def test_codex_runtime_starts_thread_and_turn_with_schema() -> None:
    client = FakeClient()
    runtime = CodexAppServerRuntime(model="gpt-5.4-mini", client=client)

    result = RuntimeResult(
        task_id="task_codex",
        generation=1,
        status=RuntimeResultStatus.COMPLETED,
        spoken_answer="Done.",
    )
    worker = asyncio.create_task(collect_results(runtime))
    await asyncio.sleep(0)
    await client.notifications.put(completed_turn_notification(result.model_dump_json()))

    messages = await worker

    assert client.requests[0][0] == "thread/start"
    assert client.requests[0][1]["approvalPolicy"] == "never"
    assert client.requests[0][1]["model"] == "gpt-5.4-mini"
    assert client.requests[1][0] == "turn/start"
    assert client.requests[1][1]["threadId"] == "thread_1"
    assert client.requests[1][1]["input"] == [
        {"type": "text", "text": "Return structured JSON.", "text_elements": []}
    ]
    assert client.requests[1][1]["outputSchema"]["properties"]["status"]["enum"]
    assert messages[-1] == result


@pytest.mark.asyncio
async def test_codex_runtime_invalid_final_json_fails() -> None:
    client = FakeClient()
    runtime = CodexAppServerRuntime(client=client)

    worker = asyncio.create_task(collect_results(runtime))
    await asyncio.sleep(0)
    await client.notifications.put(completed_turn_notification("{not-json"))

    messages = await worker

    assert messages[-1].status == RuntimeResultStatus.FAILED
    assert "not valid RuntimeResult JSON" in messages[-1].error


@pytest.mark.asyncio
async def test_codex_runtime_uses_final_assistant_item_from_completed_turn() -> None:
    client = FakeClient()
    runtime = CodexAppServerRuntime(client=client)
    result = RuntimeResult(
        task_id="task_codex",
        generation=1,
        status=RuntimeResultStatus.COMPLETED,
        spoken_answer="Done.",
        technical_summary="ok",
    )

    worker = asyncio.create_task(collect_results(runtime))
    await asyncio.sleep(0)
    await client.notifications.put(
        completed_turn_notification(
            result.model_dump_json(),
            extra_items=[
                {"id": "reasoning_1", "type": "reasoning", "summary": ['{"ignored": true}']},
                {"id": "plan_1", "type": "plan", "text": '{"ignored_plan": true}'},
                {
                    "id": "assistant_early",
                    "type": "agentMessage",
                    "text": '{"status":"failed","spoken_answer":"Wrong item"}',
                },
            ],
        )
    )

    messages = await worker

    assert messages[-1].status == RuntimeResultStatus.COMPLETED
    assert messages[-1].spoken_answer == "Done."


@pytest.mark.asyncio
async def test_codex_runtime_accepts_single_json_code_fence() -> None:
    client = FakeClient()
    runtime = CodexAppServerRuntime(client=client)
    result = RuntimeResult(
        task_id="task_codex",
        generation=1,
        status=RuntimeResultStatus.COMPLETED,
        spoken_answer="Done.",
    )

    worker = asyncio.create_task(collect_results(runtime))
    await asyncio.sleep(0)
    await client.notifications.put(completed_turn_notification(f"```json\n{result.model_dump_json()}\n```"))

    messages = await worker

    assert messages[-1].status == RuntimeResultStatus.COMPLETED
    assert messages[-1].spoken_answer == "Done."


@pytest.mark.asyncio
async def test_codex_runtime_does_not_scan_noisy_final_text_for_json() -> None:
    client = FakeClient()
    runtime = CodexAppServerRuntime(client=client)

    worker = asyncio.create_task(collect_results(runtime))
    await asyncio.sleep(0)
    await client.notifications.put(
        completed_turn_notification(
            (
                'Here is the result: {"task_id":"task_codex","generation":1,'
                '"status":"completed","spoken_answer":"Done.","technical_summary":"ok",'
                '"sources_or_tools_used":[]} Extra diagnostic: {"ignored": true}'
            )
        )
    )

    messages = await worker

    assert messages[-1].status == RuntimeResultStatus.FAILED
    assert "not valid RuntimeResult JSON" in messages[-1].error


@pytest.mark.asyncio
async def test_codex_runtime_timeout_interrupts_turn() -> None:
    client = FakeClient()
    runtime = CodexAppServerRuntime(
        client=client,
        config=CodexAppServerRuntimeConfig(total_timeout_seconds=0.01, request_timeout_seconds=0.01),
    )

    messages = await collect_results(runtime)

    assert client.interrupted == {"threadId": "thread_1", "turnId": "turn_1"}
    assert messages[-1].status == RuntimeResultStatus.FAILED
    assert "timed out" in messages[-1].error


@pytest.mark.asyncio
async def test_codex_runtime_cancel_interrupts_and_ignores_result() -> None:
    client = FakeClient()
    runtime = CodexAppServerRuntime(client=client)

    worker = asyncio.create_task(collect_results(runtime))
    await asyncio.sleep(0)
    await runtime.cancel_task("task_codex")
    messages = await worker

    assert client.interrupted == {"threadId": "thread_1", "turnId": "turn_1"}
    assert messages[-1].status == RuntimeResultStatus.CANCELLED


@pytest.mark.asyncio
async def test_codex_runtime_steers_active_turn_for_amendment() -> None:
    client = FakeClient()
    runtime = CodexAppServerRuntime(client=client)

    worker = asyncio.create_task(collect_results(runtime))
    await asyncio.sleep(0)

    await runtime.amend_task(
        RuntimeAmendment(task_id="task_codex", generation=2, amendment="Actually use Kolkata.")
    )
    await client.notifications.put(
        completed_turn_notification(
            RuntimeResult(
                task_id="task_codex",
                generation=1,
                status=RuntimeResultStatus.COMPLETED,
                spoken_answer="Done.",
            ).model_dump_json()
        )
    )
    await worker

    assert client.steered == {
        "threadId": "thread_1",
        "expectedTurnId": "turn_1",
        "input": [{"type": "text", "text": "Actually use Kolkata.", "text_elements": []}],
    }
