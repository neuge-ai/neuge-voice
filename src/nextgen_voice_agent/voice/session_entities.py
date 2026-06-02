from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


def format_ms_to_human(ms: int | None) -> str | None:
    if ms is None:
        return None
    total_seconds = max(0, ms) // 1000
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    return f"{days} Days {hours} hours {minutes} minutes and {seconds} seconds"


def datetime_delta_ms(later: datetime, earlier: datetime) -> int:
    if later.tzinfo is None and earlier.tzinfo is not None:
        earlier = earlier.replace(tzinfo=None)
    elif later.tzinfo is not None and earlier.tzinfo is None:
        later = later.replace(tzinfo=None)
    return int((later - earlier) / timedelta(milliseconds=1))


@dataclass
class SessionTimer:
    timer_id: str
    label: str
    started_at: datetime
    duration_ms: int
    reason: str | None = None
    status: str = "running"
    ui_title: str | None = None

    @property
    def ends_at(self) -> datetime:
        return self.started_at + timedelta(milliseconds=self.duration_ms)


@dataclass
class SessionActivity:
    activity_id: str
    activity_type: str
    label: str
    started_at: datetime
    target_duration_ms: int | None = None
    target_distance_meters: int | None = None
    status: str = "active"
    ui_title: str | None = None


@dataclass
class DeliveryRecord:
    delivery_id: str
    kind: str
    task_id: str
    generation: int
    text: str
    original_request: str
    emitted_at: datetime
    status: str = "emitted"
    reason: str | None = None
    spoken_at: datetime | None = None
