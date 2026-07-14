"""Conservative migration of the legacy project-state file."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .constants import Policy
from .store import new_state, record_stage_transition


def parse_legacy_scalar(raw: str) -> Any:
    value = raw.strip()
    if not value:
        return ""
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    if re.fullmatch(r"-?[0-9]+", value):
        return int(value)
    return value.split(" #", 1)[0].strip()

def read_legacy_fields(path: Path) -> tuple[dict[str, Any], list[str]]:
    """Read only safe top-level legacy scalars; never import Gate approvals."""

    notes: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return {}, [f"could not parse legacy state: {exc}; legacy file was preserved"]

    fields: dict[str, Any] = {}
    for line in text.splitlines():
        match = re.fullmatch(
            r"(schema_version|project_id|title|project_name|current_stage|last_updated):\s*(.*)",
            line,
        )
        if match:
            fields[match.group(1)] = parse_legacy_scalar(match.group(2))
    if not fields:
        notes.append(
            "legacy state format was not safely parseable; defaults were used and the "
            "legacy file was preserved"
        )
    if re.search(r"^\s+status:\s*approved\s*(?:#.*)?$", text, re.MULTILINE):
        notes.append(
            "legacy Gate approvals were intentionally not migrated; approve them "
            "explicitly with researchctl"
        )
    return fields, notes

def migrate_legacy_state(
    root: Path, policy: Policy, legacy_path: Path
) -> tuple[dict[str, Any], list[str]]:
    state = new_state(root, policy)
    fields, notes = read_legacy_fields(legacy_path)
    project_id = fields.get("project_id")
    if isinstance(project_id, str) and project_id.strip():
        state["project_id"] = project_id.strip()
    project_name = fields.get("project_name") or fields.get("title")
    if isinstance(project_name, str) and project_name.strip():
        state["project_name"] = project_name.strip()

    legacy_stage = fields.get("current_stage")
    stage_aliases = {
        "intake": policy.stage_order[0],
        "experiment": "experiment_results",
        "result": "experiment_results",
    }
    candidate = stage_aliases.get(legacy_stage, legacy_stage)
    ungated_legacy_stages = {"idea", "literature"}
    if candidate in policy.stage_order and candidate in ungated_legacy_stages:
        if candidate != policy.stage_order[0]:
            record_stage_transition(
                state,
                to_stage=candidate,
                trigger="legacy-migration",
                timestamp=state["created_at"],
            )
        else:
            state["current_stage"] = candidate
    elif legacy_stage:
        notes.append(
            f"legacy stage {legacy_stage!r} was not migrated because its Gate "
            "approvals cannot be imported safely; "
            f"defaulted to {policy.stage_order[0]!r}"
        )
    notes.append(f"legacy state was retained at {legacy_path}")
    return state, notes
