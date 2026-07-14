"""UTC timestamp parsing and monotonic state chronology."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .constants import TimestampExhaustionError


def utc_now() -> str:
    """Return a stable, timezone-explicit UTC timestamp."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )

def format_utc_timestamp(value: datetime) -> str:
    """Serialize an aware UTC datetime without losing sub-second ordering."""

    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def parse_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except (ValueError, OverflowError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    return parsed

def valid_timestamp(value: Any) -> bool:
    return parse_utc_timestamp(value) is not None

def next_state_timestamp(state: dict[str, Any]) -> str:
    """Return a UTC timestamp that cannot move behind recorded state history."""

    now = parse_utc_timestamp(utc_now())
    recorded = [
        parse_utc_timestamp(state.get(field)) for field in ("created_at", "updated_at")
    ]
    checkpoint = state.get("last_checkpoint")
    if isinstance(checkpoint, dict):
        recorded.append(parse_utc_timestamp(checkpoint.get("timestamp")))
    stage_history = state.get("stage_history")
    if isinstance(stage_history, list):
        recorded.extend(
            parse_utc_timestamp(transition.get("timestamp"))
            for transition in stage_history
            if isinstance(transition, dict)
        )
    gates = state.get("gates")
    if isinstance(gates, dict):
        for record in gates.values():
            history = record.get("history") if isinstance(record, dict) else None
            if not isinstance(history, list):
                continue
            recorded.extend(
                parse_utc_timestamp(decision.get("decided_at"))
                for decision in history
                if isinstance(decision, dict)
            )
    valid_recorded = [candidate for candidate in recorded if candidate is not None]
    if valid_recorded:
        try:
            next_after_history = max(valid_recorded) + timedelta(microseconds=1)
        except OverflowError as exc:
            raise TimestampExhaustionError(
                "state timestamps cannot advance beyond the supported datetime range"
            ) from exc
        chosen = max(candidate for candidate in (now, next_after_history) if candidate)
        return format_utc_timestamp(chosen)
    # utc_now() is valid by construction, so this fallback is defensive only.
    return format_utc_timestamp(now) if now is not None else utc_now()
