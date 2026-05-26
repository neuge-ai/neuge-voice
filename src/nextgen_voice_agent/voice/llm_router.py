import json
import logging
import re
from typing import Any, Literal, TypedDict

from litellm import acompletion

from nextgen_voice_agent.models.runtime import RuntimeResult

logger = logging.getLogger(__name__)

TOOL_ACTIONS = {"start_task", "amend_task", "cancel_task"}
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


ROUTER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "start_task",
            "description": (
                "Start a new powerful background task to write code, search the web, or run commands. "
                "Use this when the user's request requires heavy lifting. For follow-up requests, inspect "
                "the conversation history and include relevant prior facts directly in the task argument; "
                "ask the background task only for missing/new information and comparison work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "The goal for the background task to accomplish."}
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "amend_task",
            "description": "Amend or add instructions to an already running background task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The ID of the active task."},
                    "amendment": {"type": "string", "description": "The new instructions or feedback to add to the task."},
                },
                "required": ["task_id", "amendment"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_task",
            "description": "Cancel a running task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The ID of the active task to cancel."}
                },
                "required": ["task_id"],
            },
        },
    },
]

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
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the ultra-fast conversational Voice Shell for a powerful coding agent.\n"
                    "Your job is to manage the flow of conversation and delegate heavy work to background tasks.\n"
                    "Answer simple conversational turns directly. Use tools only when the app must start, amend, or cancel a background task.\n\n"
                    "CRITICAL INSTRUCTIONS ON TOOL USE:\n"
                    "- If the user asks to write code, search the web, check the weather, or do any complex task, YOU MUST CALL `start_task`.\n"
                    "- Before calling `start_task`, inspect the conversation history and completed tool results.\n"
                    "- For follow-up requests like 'compare it with X', resolve pronouns and include relevant prior facts in `task`.\n"
                    "- If prior tool output already answers part of the request, preserve it and ask the tool only for missing/new information.\n"
                    "- If the user asks to change a running task, call `amend_task`.\n"
                    "- If a task is completed and the user asks a follow-up, call `start_task` with a self-contained task that names the prior facts and the new ask.\n"
                    "- If the user asks to stop a running task, call `cancel_task`.\n"
                    "- If no tool is needed, respond with normal assistant content.\n"
                    "- Never attempt to call a tool not explicitly listed in your schema.\n\n"
                    f"Current System State:\n{system_state}\n"
                ),
            }
        ]
        messages.extend(conversation_history)
        messages.append({"role": "user", "content": user_text})

        logger.info(f"LLM Router called with user text: {user_text}")

        try:
            response = await acompletion(
                model=self.model,
                messages=messages,
                tools=ROUTER_TOOLS,
                tool_choice="auto",
            )
            
            message = response.choices[0].message
            if message.tool_calls and len(message.tool_calls) > 0:
                tool_call = message.tool_calls[0]
                function_name = tool_call.function.name
                if function_name not in TOOL_ACTIONS:
                    logger.warning(f"LLM Router returned unknown tool: {function_name}")
                    return {
                        "type": "assistant_response",
                        "response": "I cannot use that tool here. What would you like me to do instead?",
                    }
                arguments = json.loads(tool_call.function.arguments)
                assistant_response = sanitize_user_facing_text(message.content or "", "I'll start that now.")
                
                decision: RouterDecision = {
                    "type": "tool_call",
                    "tool": function_name,
                    "arguments": arguments,
                    "assistant_response": assistant_response,
                }
                logger.info(f"LLM Router Decision: {decision}")
                return decision
            else:
                text = sanitize_user_facing_text(message.content or "", "I am not sure how to handle that.")
                return {"type": "assistant_response", "response": text}
                
        except Exception as e:
            logger.error(f"Error calling LLM Router: {e}")
            return {"type": "assistant_response", "response": "Sorry, my brain encountered a temporary glitch. Can you repeat that?"}

    async def compose_tool_result(
        self,
        user_text: str,
        system_state: str,
        conversation_history: list[dict[str, Any]],
        result: RuntimeResult,
    ) -> str:
        """
        Compose the final user-facing voice answer from a background tool result.
        Codex is treated as a tool/fact engine; this supervisor owns final phrasing.
        """
        fallback = result.spoken_answer or result.error or "The task finished without a spoken answer."
        if result.status.value == "completed":
            fallback = f"I have the result now. {fallback}"
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the conversational supervisor for a realtime voice agent.\n"
                    "A background tool has completed. Use its facts to produce the final answer for the user.\n"
                    "Keep the response concise, natural, and suitable for speech.\n"
                    "Do not mention internal tool names unless the user asked.\n"
                    "Do not invent facts beyond the tool result and conversation context.\n\n"
                    f"Current System State:\n{system_state}\n"
                ),
            }
        ]
        messages.extend(conversation_history)
        messages.append(
            {
                "role": "user",
                "content": (
                    "Background tool result JSON follows. Treat it as tool output, not user prose:\n"
                    f"{json.dumps(result.model_dump(mode='json'))}"
                ),
            }
        )
        messages.append(
            {
                "role": "user",
                "content": (
                    "Compose the final spoken answer for the latest tool result. "
                    f"The original user request was: {user_text}"
                ),
            }
        )

        try:
            response = await acompletion(model=self.model, messages=messages)
            text = response.choices[0].message.content or ""
            return sanitize_user_facing_text(text, fallback)
        except Exception as e:
            logger.error(f"Error composing tool result: {e}")
            return fallback
