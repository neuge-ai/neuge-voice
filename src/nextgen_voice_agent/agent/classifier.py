from __future__ import annotations

from abc import ABC, abstractmethod

from nextgen_voice_agent.models.task import (
    ClassifiedInput,
    RelationToActiveTask,
    Task,
)


class InputClassifier(ABC):
    @abstractmethod
    def classify(self, text: str, active_task: Task | None) -> ClassifiedInput:
        """Classify a user utterance relative to the active task."""


class RuleBasedInputClassifier(InputClassifier):
    def classify(self, text: str, active_task: Task | None) -> ClassifiedInput:
        normalized = text.strip().lower()

        if active_task is None:
            return ClassifiedInput(
                relation_to_active_task=RelationToActiveTask.NEW_TASK,
                target_task_id=None,
                action="start_new_task",
                confidence=0.8,
            )

        if any(phrase in normalized for phrase in ("stop", "cancel", "forget it", "never mind", "don't need")):
            return ClassifiedInput(
                relation_to_active_task=RelationToActiveTask.CANCELLATION,
                target_task_id=active_task.task_id,
                action="cancel_task",
                confidence=0.9,
            )

        if any(phrase in normalized for phrase in ("status", "what are you doing", "progress", "how is it going")):
            return ClassifiedInput(
                relation_to_active_task=RelationToActiveTask.STATUS_REQUEST,
                target_task_id=active_task.task_id,
                action="report_status",
                confidence=0.85,
            )

        if any(phrase in normalized for phrase in ("actually", "instead", "also", "use ", "make it", "include", "don't ")):
            return ClassifiedInput(
                relation_to_active_task=RelationToActiveTask.AMENDMENT,
                target_task_id=active_task.task_id,
                action="amend_task",
                confidence=0.75,
            )

        if any(phrase in normalized for phrase in ("by the way", "what is", "explain")):
            return ClassifiedInput(
                relation_to_active_task=RelationToActiveTask.UNRELATED,
                target_task_id=active_task.task_id,
                action="answer_directly",
                confidence=0.7,
            )

        return ClassifiedInput(
            relation_to_active_task=RelationToActiveTask.NEW_TASK,
            target_task_id=None,
            action="start_new_task",
            confidence=0.55,
        )

