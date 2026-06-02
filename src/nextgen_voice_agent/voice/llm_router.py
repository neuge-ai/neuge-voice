import json
import logging
import re
from typing import Any, Literal, TypedDict

from litellm import acompletion

from nextgen_voice_agent.voice.tool_catalog import ROUTER_TOOL_NAMES, ROUTER_TOOLS

logger = logging.getLogger(__name__)

BRAIN_GLITCH_MESSAGE = "Sorry, my brain encountered a temporary glitch. Can you repeat that?"

# Backward-compatible alias for tests and imports.
TOOL_ACTIONS = ROUTER_TOOL_NAMES
THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
UNCLOSED_THINK_RE = re.compile(r"<think\b[^>]*>.*", re.IGNORECASE | re.DOTALL)
FENCED_BLOCK_RE = re.compile(r"^\s*```(?:\w+)?\s*(.*?)\s*```\s*$", re.DOTALL)
XML_THINK_TAG_RE = re.compile(r"</?think\b[^>]*>", re.IGNORECASE)
WHITESPACE_RE = re.compile(r"[ \t]+\n|\n{3,}")


class AssistantResponseDecision(TypedDict):
    type: Literal["assistant_response"]
    response: str


class ToolCallDecision(TypedDict):
    type: Literal["tool_call"]
    tool: str
    arguments: dict[str, Any]
    assistant_response: str


RouterDecision = AssistantResponseDecision | ToolCallDecision


def sanitize_user_facing_text(text: str, fallback: str = "") -> str:
    cleaned = THINK_BLOCK_RE.sub("", text).strip()
    cleaned = UNCLOSED_THINK_RE.sub("", cleaned).strip()
    fence_match = FENCED_BLOCK_RE.match(cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    cleaned = XML_THINK_TAG_RE.sub("", cleaned).strip()
    cleaned = WHITESPACE_RE.sub("\n", cleaned).strip()
    return cleaned or fallback


def messages_with_timestamp_context(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        clean = {key: value for key, value in message.items() if key != "timestamp"}
        timestamp = message.get("timestamp")
        if timestamp and isinstance(clean.get("content"), str):
            clean["content"] = f"[timestamp: {timestamp}]\n{clean['content']}"
        elif timestamp:
            clean["content"] = f"[timestamp: {timestamp}]"
        normalized.append(clean)
    return normalized


SANITY_CHECK_INSTRUCTIONS = (
    "STATE AND PLAUSIBILITY CHECKS:\n"
    "- You receive timestamped conversation history plus authoritative session state with current date/time.\n"
    "- Treat active timers, activities, background tasks, elapsed time, remaining time, and stored activity targets as authoritative.\n"
    "- Do not blindly accept user claims that a timer, activity, physical action, or background task is complete.\n"
    "- If a claim conflicts with elapsed time or task status, respond naturally and point out the mismatch.\n"
    "- For activity completion claims, reason over the latest utterance itself together with elapsed time, even if the activity was started without an explicit target.\n"
    "- If the user adds a distance or duration only at completion time, compare that new claim against the activity start time before deciding whether to end the activity.\n"
    "- If the user restarts the same real-world activity while one is already active, ask whether it is the same session or a new one instead of creating a duplicate.\n"
    "- If the user says they are starting a real-world activity such as a run, walk, workout, drive, or break, use `start_activity`, not `start_task`.\n"
    "- If no active activity exists but the timestamped history contains a recent start claim, use that history as recovery evidence before assuming there is nothing to finish.\n"
    "- If the user says they finished, are done, or completed an activity, inspect active activities and either call `end_activity`, call `get_activity_status`, or challenge the claim if it is too early or implausible.\n"
    "- Never call `start_activity` for a completion claim like 'I'm done with my run.'\n"
    "- Example: if the user started a run seconds ago and then says 'I just finished a five mile run', do not congratulate them or call `end_activity`; challenge or clarify instead.\n"
    "- Example: if the user started a five-minute run and says 'I finished' after ten seconds, do not call `end_activity`; point out the elapsed time mismatch.\n"
    "- `pending_unheard_items` contains durable results/questions that were emitted but not confirmed heard; never claim those were already delivered.\n"
    "- Do not preserve or mention missed ephemeral status/progress filler. Only durable pending items matter.\n"
    "- Use native timer/activity/status tools for deterministic state. Use Codex only for open-ended work.\n"
    "- Keep challenges light, direct, and non-hostile.\n"
    "- If pending_unheard_items includes unresolved failures or interrupted deliveries, mention them briefly after answering the current request when relevant.\n"
)

ROUTE_TURN_TOOL_INSTRUCTIONS = (
    "CRITICAL INSTRUCTIONS ON TOOL USE:\n"
    "- If the user asks to write code, search the web, check the weather, or do any complex task, YOU MUST CALL `start_task`.\n"
    "- If the user asks to set/check/cancel timers or track/check/end activities, use the native timer/activity tools.\n"
    "- If the user asks whether background work is done, use the provided state or native background status tools; do not guess.\n"
    "- Only one Codex/background task may run at a time. If `can_start_new_codex_task` is false, do not call `start_task`.\n"
    "- While Codex is running, simple conversation, timers, activities, status checks, amendments, and cancellation are still allowed.\n"
    "- Treat related follow-ups to the active Codex task as same-thread amendments. If the user refines, narrows, corrects, adds constraints, says 'also', 'instead', 'make sure', 'use X', 'don't do Y', or otherwise refers to the current task, call `amend_task`.\n"
    "- For amendments, pass only the new instruction or correction in `amendment`; do not rewrite the full original task.\n"
    "- If the user asks for a clearly unrelated second complex task while Codex is running, explain that a background task is already running and ask whether to cancel/switch or keep it going.\n"
    "- If the user clearly says to cancel the current task and do a new complex task, call `cancel_task` first; the new task can be started on the next turn after cancellation.\n"
    "- Before calling `start_task`, inspect the conversation history and completed tool results.\n"
    "- For follow-up requests like 'compare it with X', resolve pronouns and include relevant prior facts in `task`.\n"
    "- If prior tool output already answers part of the request, preserve it and ask the tool only for missing/new information.\n"
    "- If the user asks to change a running task, call `amend_task` with the active task id from system state.\n"
    "- If a task is completed and the user asks a follow-up, call `start_task` with a self-contained task that names the prior facts and the new ask.\n"
    "- If the user asks to stop a running task, call `cancel_task`.\n"
    "- If no tool is needed, respond with normal assistant content.\n"
    "- Never attempt to call a tool not explicitly listed in your schema.\n"
)

SPEAK_FROM_STATE_INSTRUCTIONS = (
    "SPEAK MODE:\n"
    "Produce the single next spoken reply for the user.\n"
    "Use conversation history and system state; latest tool results in history are authoritative facts.\n"
    "Keep the response concise, natural, and suitable for speech.\n"
    "Do not mention internal tool names unless the user asked.\n"
    "If a tool failed, say that it failed; do not read raw error text aloud unless the user asked for technical details.\n"
    "Do not invent facts beyond history, state, and tool output.\n"
)


def build_route_turn_system_prompt(system_state: str) -> str:
    return (
        "You are the ultra-fast conversational Voice Shell for a powerful coding agent.\n"
        "Your job is to manage the flow of conversation and delegate heavy work to background tasks.\n"
        "Answer simple conversational turns directly. Use tools only when the app must start, amend, or cancel a background task.\n\n"
        f"{ROUTE_TURN_TOOL_INSTRUCTIONS}\n"
        f"{SANITY_CHECK_INSTRUCTIONS}\n"
        f"Current System State:\n{system_state}\n"
    )


def build_speak_from_state_system_prompt(system_state: str) -> str:
    return (
        "You are the ultra-fast conversational Voice Shell for a powerful coding agent.\n"
        "Your job is to manage the flow of conversation and delegate heavy work to background tasks.\n\n"
        f"{SPEAK_FROM_STATE_INSTRUCTIONS}\n"
        f"{SANITY_CHECK_INSTRUCTIONS}\n"
        f"Current System State:\n{system_state}\n"
    )


async def _acompletion_user_facing(
    *,
    model: str,
    system_prompt: str,
    history: list[dict[str, Any]],
    user_content: str,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | None = None,
    empty_fallback: str = "",
) -> tuple[Any, str]:
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(messages_with_timestamp_context(history))
    messages.append({"role": "user", "content": user_content})
    if tools is not None:
        response = await acompletion(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice or "auto",
        )
    else:
        response = await acompletion(model=model, messages=messages)
    message = response.choices[0].message
    text = sanitize_user_facing_text(message.content or "", empty_fallback)
    return message, text


class OrchestratorLLMProvider:
    def __init__(self, model: str = "groq/qwen/qwen3-32b"):
        self.model = model

    async def route_turn(
        self,
        user_text: str,
        system_state: str,
        conversation_history: list[dict[str, Any]],
    ) -> RouterDecision:
        """
        Sends the conversation history + user text to the LLM and returns either
        a direct assistant response or a parsed tool call decision.
        """
        logger.info(f"LLM Router called with user text: {user_text}")

        try:
            message, _ = await _acompletion_user_facing(
                model=self.model,
                system_prompt=build_route_turn_system_prompt(system_state),
                history=conversation_history,
                user_content=user_text,
                tools=ROUTER_TOOLS,
                tool_choice="auto",
            )
            if message.tool_calls and len(message.tool_calls) > 0:
                tool_call = message.tool_calls[0]
                function_name = tool_call.function.name
                if function_name not in ROUTER_TOOL_NAMES:
                    logger.warning(f"LLM Router returned unknown tool: {function_name}")
                    return {
                        "type": "assistant_response",
                        "response": "I cannot use that tool here. What would you like me to do instead?",
                    }
                arguments = json.loads(tool_call.function.arguments)
                assistant_response = sanitize_user_facing_text(message.content or "", "I'll look into that.")

                decision: RouterDecision = {
                    "type": "tool_call",
                    "tool": function_name,
                    "arguments": arguments,
                    "assistant_response": assistant_response,
                }
                logger.info(f"LLM Router Decision: {decision}")
                return decision
            direct_text = sanitize_user_facing_text(message.content or "", "I am not sure how to handle that.")
            return {"type": "assistant_response", "response": direct_text}
                
        except Exception as e:
            logger.error(f"Error calling LLM Router: {e}")
            return {"type": "assistant_response", "response": BRAIN_GLITCH_MESSAGE}

    async def speak_from_state(
        self,
        instruction: str,
        user_text: str | None,
        system_state: str,
        conversation_history: list[dict[str, Any]],
    ) -> str:
        anchor = user_text or "(none — background event)"
        _, text = await _acompletion_user_facing(
            model=self.model,
            system_prompt=build_speak_from_state_system_prompt(system_state),
            history=conversation_history,
            user_content=f"{instruction}\n\nLatest user text: {anchor}",
        )
        if not text:
            raise RuntimeError("empty speak response")
        return text
