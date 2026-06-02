from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable

from nextgen_voice_agent.agent.controller import AgentController
from nextgen_voice_agent.models.task import CancelTaskRequest, TaskStatus
from nextgen_voice_agent.voice.session_entities import (
    SessionActivity,
    SessionTimer,
    datetime_delta_ms,
    format_ms_to_human,
)

if TYPE_CHECKING:
    from nextgen_voice_agent.voice.orchestrator import VoiceSessionState


class NativeToolExecutor:
    """Executes sync native tools by mutating VoiceSessionState (does not own session state)."""

    def __init__(self, *, controller: AgentController, clock: Callable[[], datetime]) -> None:
        self._controller = controller
        self._clock = clock

    async def execute(
        self,
        state: VoiceSessionState,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name == "start_timer":
            duration_ms = int(args["duration_ms"])
            timer = SessionTimer(
                timer_id=f"timer_{len(state.active_timers) + 1:03d}",
                label=str(args.get("label") or "timer"),
                duration_ms=duration_ms,
                reason=str(args["reason"]) if args.get("reason") is not None else None,
                started_at=self._clock(),
                ui_title=args.get("ui_title"),
            )
            state.active_timers[timer.timer_id] = timer
            return {"timer": self._timer_payload(timer, self._clock()), "message": f"I started the {timer.label} timer."}
        if tool_name == "list_active_timers":
            return {
                "timers": [
                    self._timer_payload(timer, self._clock())
                    for timer in state.active_timers.values()
                    if timer.status == "running"
                ]
            }
        if tool_name == "get_timer_status":
            timer = self._select_timer(state, args.get("timer_id"))
            return {"timer": self._timer_payload(timer, self._clock()), "message": self._timer_status_message(timer)}
        if tool_name == "cancel_timer":
            timer = self._select_timer(state, args.get("timer_id"))
            timer.status = "cancelled"
            return {"timer": self._timer_payload(timer, self._clock()), "message": f"I cancelled the {timer.label} timer."}
        if tool_name == "start_activity":
            activity = SessionActivity(
                activity_id=f"activity_{len(state.active_activities) + 1:03d}",
                activity_type=str(args.get("activity_type") or "activity"),
                label=str(args.get("label") or args.get("activity_type") or "activity"),
                target_duration_ms=int(args["target_duration_ms"]) if args.get("target_duration_ms") is not None else None,
                target_distance_meters=int(args["target_distance_meters"])
                if args.get("target_distance_meters") is not None
                else None,
                started_at=self._clock(),
                ui_title=args.get("ui_title"),
            )
            state.active_activities[activity.activity_id] = activity
            return {
                "activity": self._activity_payload(activity, self._clock()),
                "message": f"I started tracking {activity.label}.",
            }
        if tool_name == "list_active_activities":
            return {
                "activities": [
                    self._activity_payload(activity, self._clock())
                    for activity in state.active_activities.values()
                    if activity.status == "active"
                ]
            }
        if tool_name == "get_activity_status":
            activity = self._select_activity(state, args.get("activity_id"))
            return {
                "activity": self._activity_payload(activity, self._clock()),
                "message": f"{activity.label} is still active.",
            }
        if tool_name == "end_activity":
            activity = self._select_activity(state, args.get("activity_id"))
            rejection = self._reject_obviously_invalid_activity_completion(activity, self._clock())
            if rejection is not None:
                return rejection
            activity.status = "completed"
            payload = self._activity_payload(activity, self._clock())
            state.recent_completed_events.append({"type": "activity", "activity": payload, "completed_at": self._iso_now()})
            state.recent_completed_events = state.recent_completed_events[-10:]
            return {"activity": payload, "message": f"I marked {activity.label} complete."}
        if tool_name == "cancel_activity":
            activity = self._select_activity(state, args.get("activity_id"))
            activity.status = "cancelled"
            return {
                "activity": self._activity_payload(activity, self._clock()),
                "message": f"I cancelled tracking {activity.label}.",
            }
        if tool_name == "list_active_background_tasks":
            return {
                "tasks": [
                    self._task_payload(state, task, self._clock())
                    for task in self._controller.tasks.values()
                    if task.status in {TaskStatus.RUNNING, TaskStatus.AMENDING, TaskStatus.WAITING_FOR_APPROVAL}
                ]
            }
        if tool_name == "get_background_task_status":
            task_id = str(args.get("task_id") or state.active_task_id or "")
            status = self._controller.get_status(task_id)
            return {
                "task": status.task.model_dump(mode="json"),
                "progress": status.progress,
                "message": status.progress or status.task.user_visible_status,
            }
        if tool_name == "cancel_background_task":
            task_id = str(args.get("task_id") or state.active_task_id or "")
            response = await self._controller.cancel_task(
                task_id,
                CancelTaskRequest(reason=str(args.get("reason") or "User voice cancellation")),
            )
            if state.active_task_id == task_id:
                state.active_task_id = None
            return response.model_dump(mode="json") | {"message": "I stopped that task."}
        raise ValueError(f"Unknown native tool: {tool_name}")

    def _iso_now(self) -> str:
        return self._clock().astimezone().isoformat()

    def _select_timer(self, state: VoiceSessionState, timer_id: object) -> SessionTimer:
        if timer_id:
            timer = state.active_timers.get(str(timer_id))
            if timer:
                return timer
            raise ValueError(f"Timer {timer_id} was not found.")
        active = [timer for timer in state.active_timers.values() if timer.status == "running"]
        if len(active) == 1:
            return active[0]
        raise ValueError("I need to know which timer.")

    def _select_activity(self, state: VoiceSessionState, activity_id: object) -> SessionActivity:
        if activity_id:
            activity = state.active_activities.get(str(activity_id))
            if activity:
                return activity
            raise ValueError(f"Activity {activity_id} was not found.")
        active = [activity for activity in state.active_activities.values() if activity.status == "active"]
        if len(active) == 1:
            return active[0]
        raise ValueError("I need to know which activity.")

    def _reject_obviously_invalid_activity_completion(
        self,
        activity: SessionActivity,
        now: datetime,
    ) -> dict[str, Any] | None:
        payload = self._activity_payload(activity, now)
        elapsed_ms = int(payload["elapsed_ms"])
        if activity.target_duration_ms is not None and elapsed_ms < activity.target_duration_ms:
            return {
                "ok": False,
                "error_code": "activity_completion_too_early",
                "reason": "The tracked activity duration has not elapsed yet.",
                "activity": payload,
                "elapsed_ms": elapsed_ms,
                "target_duration_ms": activity.target_duration_ms,
            }
        if activity.target_distance_meters is None or elapsed_ms <= 0:
            return None
        elapsed_seconds = elapsed_ms / 1000
        speed_mps = activity.target_distance_meters / elapsed_seconds
        if speed_mps <= 12:
            return None
        return {
            "ok": False,
            "error_code": "activity_completion_physically_implausible",
            "reason": "The tracked distance and elapsed time imply an impossible pace.",
            "activity": payload,
            "elapsed_ms": elapsed_ms,
            "target_distance_meters": activity.target_distance_meters,
            "implied_speed_mps": round(speed_mps, 1),
        }

    def _timer_status_message(self, timer: SessionTimer) -> str:
        payload = self._timer_payload(timer, self._clock())
        remaining = int(payload["remaining_ms"] / 1000)
        if remaining <= 0:
            return f"The {timer.label} timer is done."
        return f"The {timer.label} timer still has about {remaining} seconds left."

    def _timer_payload(self, timer: SessionTimer, now: datetime) -> dict[str, Any]:
        elapsed_ms = max(0, int((now - timer.started_at) / timedelta(milliseconds=1)))
        remaining_ms = max(0, timer.duration_ms - elapsed_ms)
        return {
            "timer_id": timer.timer_id,
            "label": timer.label,
            "started_at": timer.started_at.astimezone().isoformat(),
            "ends_at": timer.ends_at.astimezone().isoformat(),
            "elapsed_ms": elapsed_ms,
            "elapsed_time_human": format_ms_to_human(elapsed_ms),
            "remaining_ms": remaining_ms,
            "remaining_time_human": format_ms_to_human(remaining_ms),
            "status": timer.status,
            "reason": timer.reason,
            "ui_title": timer.ui_title,
        }

    def _activity_payload(self, activity: SessionActivity, now: datetime) -> dict[str, Any]:
        elapsed_ms = max(0, int((now - activity.started_at) / timedelta(milliseconds=1)))
        return {
            "activity_id": activity.activity_id,
            "type": activity.activity_type,
            "label": activity.label,
            "started_at": activity.started_at.astimezone().isoformat(),
            "elapsed_ms": elapsed_ms,
            "elapsed_time_human": format_ms_to_human(elapsed_ms),
            "target_duration_ms": activity.target_duration_ms,
            "target_distance_meters": activity.target_distance_meters,
            "status": activity.status,
            "ui_title": activity.ui_title,
        }

    def _task_payload(self, state: VoiceSessionState, task: Any, now: datetime) -> dict[str, Any]:
        elapsed_ms = datetime_delta_ms(now, state.task_started_at or task.created_at)
        last_progress_ms_ago = datetime_delta_ms(now, task.last_update_at)
        last_spoken_ms = None
        if state.last_progress_spoken_at is not None:
            last_spoken_ms = int((now - state.last_progress_spoken_at) / timedelta(milliseconds=1))
        return {
            "task_id": task.task_id,
            "type": "codex",
            "status": task.status.value,
            "original_request": task.original_request,
            "user_visible_status": task.user_visible_status,
            "amendable": bool(getattr(task, "amendable", False)),
            "elapsed_ms": max(0, elapsed_ms),
            "last_meaningful_progress_ms_ago": max(0, last_progress_ms_ago),
            "last_spoken_update_ms_ago": last_spoken_ms,
            "progress_update_count": state.progress_update_count,
            "ui_title": getattr(task, "ui_title", None),
        }