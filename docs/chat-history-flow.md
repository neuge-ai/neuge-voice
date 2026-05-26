# Voice Chat History Flow

This note explains the chat-history changes in the voice path.

## Short Version

The voice assistant now treats Codex like a background tool, not like the final speaker.

Flow:

1. User says something.
2. The router LLM either answers directly or calls a tool like `start_task`.
3. If it starts Codex, the assistant immediately says a short acknowledgement.
4. Codex runs in the background and returns structured facts.
5. The supervisor LLM turns those facts into the final spoken answer.
6. That final answer is sent to the frontend as `DELIVERY_READY` and then TTS reads it.

So TTS should hear the supervisor's clean final answer, not raw Codex output.

## Files Changed

- `src/nextgen_voice_agent/voice/orchestrator.py`
  - Stores voice conversation history.
  - Emits the immediate assistant acknowledgement.
  - Adds completed Codex results back into history.
  - Calls the supervisor composer before emitting `DELIVERY_READY`.

- `src/nextgen_voice_agent/voice/llm_router.py`
  - Parses router tool calls.
  - Preserves the router's short acknowledgement text.
  - Composes the final user-facing answer from the Codex result.
  - Removes hidden `<think>...</think>` blocks and simple markdown fences before text reaches TTS.

- `src/nextgen_voice_agent/agent/prompts.py`
  - Tells Codex to behave like a tool/fact engine.
  - Codex should return concise facts in `spoken_answer`; the supervisor handles final phrasing.

## Start Task History Shape

When the router starts a task, the history is stored in Codex-style order:

```json
[
  {
    "role": "user",
    "content": "Check the weather in Delhi today."
  },
  {
    "role": "assistant",
    "tool_calls": [
      {
        "id": "call_task_123",
        "type": "function",
        "function": {
          "name": "start_task",
          "arguments": "{\"task\":\"Check the weather in Delhi today.\"}"
        }
      }
    ]
  },
  {
    "role": "tool",
    "tool_call_id": "call_task_123",
    "name": "start_task",
    "content": "Started Task ID: task_123"
  },
  {
    "role": "assistant",
    "content": "I'll check that now."
  }
]
```

The important detail is that the spoken acknowledgement comes from the router decision:

```json
{
  "type": "tool_call",
  "tool": "start_task",
  "arguments": {"task": "Check the weather in Delhi today."},
  "assistant_response": "I'll check that now."
}
```

This avoids speaking the controller's generic REST acknowledgement like:

```text
Sure, I will work on: Check the weather in Delhi today.
```

That generic acknowledgement still belongs to manual/REST task starts, but not the voice turn.

## Completed Result History

When Codex finishes, the orchestrator appends the completed result to history once:

```json
{
  "role": "tool",
  "tool_call_id": "result_task_123",
  "name": "start_task",
  "content": "{\"task_id\":\"task_123\",\"status\":\"completed\",\"spoken_answer\":\"Delhi is very hot today...\"}"
}
```

Then the supervisor LLM composes the final answer, and the orchestrator stores that too:

```json
{
  "role": "assistant",
  "content": "Delhi is very hot today, with temperatures around 43 to 44 degrees Celsius..."
}
```

That final assistant message is what gets emitted as `DELIVERY_READY`.

## Why The Supervisor Does Not Send `role: "tool"` In Its Own LLM Call

There are two separate things:

1. The session history can store tool messages.
2. A fresh LLM API call has strict ordering rules for `role: "tool"` messages.

In OpenAI-style chat formats, a `role: "tool"` message is only valid right after an assistant message that made a matching tool call. A fresh supervisor call like this can be rejected:

```json
[
  {"role": "system", "content": "Compose a spoken answer."},
  {"role": "tool", "tool_call_id": "result_task_123", "content": "...Codex result..."},
  {"role": "user", "content": "Now answer the user."}
]
```

The provider can reject it because there was no immediately previous assistant tool call with `tool_call_id: "result_task_123"` inside that same request.

So the supervisor composer passes the completed Codex result as normal context:

```json
{
  "role": "user",
  "content": "Background tool result JSON follows. Treat it as tool output, not user prose:\n{...}"
}
```

Conceptually it is still tool output. The only difference is the message role used for this separate composition call.

## Follow-Up Turns

If a result is queued but the user speaks again first, the orchestrator first saves that queued result into history, clears the active task, and then routes the new user turn.

That lets the router see prior facts. For example:

```text
User: What is the weather in Delhi today?
Assistant: I'll check that now.
Codex result saved in history: Delhi is around 43 to 44 C.
User: Compare it with Kolkata.
```

The follow-up can now be routed as a new task with the earlier Delhi result available in history, instead of trying to amend a task that may already be completed.

## User-Facing Text Cleanup

Some reasoning models may return text like:

```text
<think>I should summarize the weather clearly.</think>
Delhi is very hot today.
```

The voice layer should not show or speak the thinking block. `sanitize_user_facing_text()` removes that before the text reaches the frontend/TTS.

It also unwraps simple fenced output:

````text
```text
Delhi is very hot today.
```
````

Final spoken text:

```text
Delhi is very hot today.
```

