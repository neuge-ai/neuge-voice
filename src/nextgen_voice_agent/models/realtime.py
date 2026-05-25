from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RealtimeTool(BaseModel):
    type: Literal["function"] = "function"
    name: str
    description: str
    parameters: dict[str, Any]


class RealtimeSessionConfig(BaseModel):
    model: str
    voice: str
    modalities: list[Literal["audio", "text"]] = Field(default_factory=lambda: ["audio", "text"])
    instructions: str
    tools: list[RealtimeTool]
