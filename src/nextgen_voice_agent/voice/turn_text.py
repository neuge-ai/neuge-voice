from __future__ import annotations

import re

HARD_INTERRUPT_COMMANDS = {"stop", "cancel"}
SOFT_AMENDMENT_MARKERS = {"actually", "no", "wrong", "also", "instead", "but"}


def normalize_turn_text(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", text.lower()).split())


def is_hard_interrupt_command(normalized: str) -> bool:
    return normalized in HARD_INTERRUPT_COMMANDS


def is_soft_amendment_marker_only(normalized: str) -> bool:
    return normalized in SOFT_AMENDMENT_MARKERS
