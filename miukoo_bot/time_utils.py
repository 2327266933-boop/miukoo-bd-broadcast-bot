from datetime import datetime, time, timedelta, timezone
from typing import Optional


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(hour=int(hour), minute=int(minute))


def is_quiet_time(moment: datetime, start: str, end: str) -> bool:
    start_time = parse_hhmm(start)
    end_time = parse_hhmm(end)
    local_time = moment.astimezone().time().replace(second=0, microsecond=0)

    if start_time == end_time:
        return False
    if start_time < end_time:
        return start_time <= local_time < end_time
    return local_time >= start_time or local_time < end_time


def next_allowed_time(moment: datetime, start: str, end: str) -> datetime:
    if not is_quiet_time(moment, start, end):
        return moment

    end_time = parse_hhmm(end)
    local_moment = moment.astimezone()
    candidate = local_moment.replace(
        hour=end_time.hour,
        minute=end_time.minute,
        second=0,
        microsecond=0,
    )
    if candidate <= local_moment:
        candidate = candidate + timedelta(days=1)
    return candidate.astimezone(timezone.utc)
