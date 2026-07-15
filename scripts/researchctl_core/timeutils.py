"""UTC timestamp parsing and monotonic state chronology."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .constants import TimestampExhaustionError
from .gate_records import iter_present_gate_records


def utc_now() -> str:
    """Return a stable, timezone-explicit UTC timestamp."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )

def format_utc_timestamp(value: datetime) -> str:
    """Serialize an aware UTC datetime without losing sub-second ordering."""

    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")

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
    for record in iter_present_gate_records(state):
        history = record.get("history")
        if not isinstance(history, list):
            continue
        recorded.extend(
            parse_utc_timestamp(decision.get("decided_at"))
            for decision in history
            if isinstance(decision, dict)
        )
    lifecycle = state.get("lifecycle")
    lifecycle_history = (
        lifecycle.get("history") if isinstance(lifecycle, dict) else None
    )
    if isinstance(lifecycle_history, list):
        recorded.extend(
            parse_utc_timestamp(decision.get("decided_at"))
            for decision in lifecycle_history
            if isinstance(decision, dict)
        )
    activation_history = state.get("activation_history")
    if isinstance(activation_history, list):
        recorded.extend(
            parse_utc_timestamp(event.get("decided_at"))
            for event in activation_history
            if isinstance(event, dict)
        )
    artifacts = state.get("artifacts")
    if isinstance(artifacts, dict):
        for stage_bucket in artifacts.values():
            if not isinstance(stage_bucket, dict):
                continue
            for role_bucket in stage_bucket.values():
                if not isinstance(role_bucket, dict):
                    continue
                for entry in role_bucket.values():
                    revisions = entry.get("revisions") if isinstance(entry, dict) else None
                    if not isinstance(revisions, list):
                        continue
                    recorded.extend(
                        parse_utc_timestamp(revision.get("registered_at"))
                        for revision in revisions
                        if isinstance(revision, dict)
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
