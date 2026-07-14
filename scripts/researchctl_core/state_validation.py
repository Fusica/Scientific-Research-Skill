"""State envelope, checkpoint, stage-history, and chronology validation."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .constants import Policy, REQUIRED_STATE_FIELDS, ResearchCtlError
from .gates import transition_requirements
from .timeutils import parse_utc_timestamp, valid_timestamp


def validate_state_envelope(
    state: dict[str, Any],
    policy: Policy,
    errors: list[str],
) -> tuple[datetime | None, datetime | None]:
    missing = REQUIRED_STATE_FIELDS - set(state)
    if missing:
        errors.append("missing state fields: " + ", ".join(sorted(missing)))
    if state.get("schema_version") != policy.schema_version:
        errors.append(
            f"schema_version {state.get('schema_version')!r} does not match "
            f"policy {policy.schema_version!r}"
        )
    if state.get("workflow_version") != policy.workflow_version:
        errors.append(
            f"workflow_version {state.get('workflow_version')!r} does not match "
            f"policy {policy.workflow_version!r}"
        )
    if not isinstance(state.get("enabled"), bool):
        errors.append("enabled must be a boolean")
    for field in ("project_id", "project_name"):
        if not isinstance(state.get(field), str) or not state[field].strip():
            errors.append(f"{field} must be a non-empty string")
    if state.get("current_stage") not in policy.stage_order:
        errors.append(f"unknown current_stage: {state.get('current_stage')!r}")
    for field in ("created_at", "updated_at"):
        if not valid_timestamp(state.get(field)):
            errors.append(f"{field} must be a timezone-explicit UTC timestamp")
    created_at = parse_utc_timestamp(state.get("created_at"))
    updated_at = parse_utc_timestamp(state.get("updated_at"))
    if created_at is not None and updated_at is not None and updated_at < created_at:
        errors.append("updated_at must not be earlier than created_at")
    return created_at, updated_at


def validate_state_timeline(
    state: dict[str, Any],
    policy: Policy,
    gates: Any,
    gate_decisions_by_id: dict[str, tuple[str, dict[str, Any]]],
    created_at: datetime | None,
    updated_at: datetime | None,
    errors: list[str],
) -> None:
    checkpoint = state.get("last_checkpoint")
    if checkpoint is not None:
        if not isinstance(checkpoint, dict):
            errors.append("last_checkpoint must be null or an object")
        else:
            if not isinstance(checkpoint.get("summary"), str) or not checkpoint[
                "summary"
            ].strip():
                errors.append("last_checkpoint summary must be non-empty")
            if not valid_timestamp(checkpoint.get("timestamp")):
                errors.append(
                    "last_checkpoint timestamp must be a timezone-explicit UTC timestamp"
                )
    stage_history = state.get("stage_history")
    if not isinstance(stage_history, list):
        errors.append("stage_history must be a list")
    else:
        expected_stage = policy.stage_order[0]
        previous_transition_at: datetime | None = None
        for index, transition in enumerate(stage_history):
            prefix = f"stage_history[{index}]"
            if not isinstance(transition, dict):
                errors.append(f"{prefix} must be an object")
                continue
            required = {"from_stage", "to_stage", "trigger", "timestamp"}
            absent = required - set(transition)
            if absent:
                errors.append(
                    f"{prefix} missing fields: {', '.join(sorted(absent))}"
                )
                continue
            from_stage = transition.get("from_stage")
            to_stage = transition.get("to_stage")
            if from_stage != expected_stage:
                errors.append(
                    f"{prefix} starts at {from_stage!r}, expected {expected_stage!r}"
                )
            if from_stage not in policy.stage_order:
                errors.append(f"{prefix} has unknown from_stage {from_stage!r}")
            if to_stage not in policy.stage_order:
                errors.append(f"{prefix} has unknown to_stage {to_stage!r}")
            if from_stage in policy.stage_order and to_stage in policy.stage_order:
                try:
                    transition_requirements(policy, from_stage, to_stage)
                except ResearchCtlError as exc:
                    errors.append(f"{prefix}: {exc}")
            if not isinstance(transition.get("trigger"), str) or not transition[
                "trigger"
            ].strip():
                errors.append(f"{prefix} trigger must be non-empty")
            else:
                trigger = transition["trigger"]
                gate_trigger = re.fullmatch(
                    r"gate(-reopen)?:([a-z][a-z0-9_]*):(.+)", trigger
                )
                if gate_trigger:
                    expected_action = "reopen" if gate_trigger.group(1) else "approve"
                    trigger_gate = gate_trigger.group(2)
                    trigger_decision_id = gate_trigger.group(3)
                    linked = gate_decisions_by_id.get(trigger_decision_id)
                    if linked is None:
                        errors.append(f"{prefix} references an unknown Gate decision")
                    else:
                        linked_gate, linked_decision = linked
                        if linked_gate != trigger_gate:
                            errors.append(f"{prefix} trigger Gate does not match its decision")
                        if linked_decision.get("action") != expected_action:
                            errors.append(f"{prefix} trigger action does not match its decision")
                        if linked_decision.get("decided_at") != transition.get("timestamp"):
                            errors.append(f"{prefix} timestamp does not match its Gate decision")
                        spec = policy.gate_specs.get(trigger_gate, {})
                        expected_target = spec.get(
                            "reopen_to" if expected_action == "reopen" else "advance_to"
                        )
                        if to_stage != expected_target:
                            errors.append(
                                f"{prefix} target does not match Gate {trigger_gate} policy"
                            )
                elif trigger not in {"checkpoint", "legacy-migration"}:
                    errors.append(f"{prefix} has unsupported trigger {trigger!r}")
            transition_at = parse_utc_timestamp(transition.get("timestamp"))
            if transition_at is None:
                errors.append(
                    f"{prefix} timestamp must be a timezone-explicit UTC timestamp"
                )
            elif (
                previous_transition_at is not None
                and transition_at < previous_transition_at
            ):
                errors.append(f"{prefix} timestamp is earlier than the prior transition")
            else:
                previous_transition_at = transition_at
            if to_stage in policy.stage_order:
                expected_stage = to_stage
        if expected_stage != state.get("current_stage"):
            errors.append(
                "current_stage does not match the final recorded stage transition"
            )

    recorded_events: list[tuple[str, Any]] = []
    if isinstance(checkpoint, dict):
        recorded_events.append(("last_checkpoint.timestamp", checkpoint.get("timestamp")))
    if isinstance(stage_history, list):
        recorded_events.extend(
            (f"stage_history[{index}].timestamp", transition.get("timestamp"))
            for index, transition in enumerate(stage_history)
            if isinstance(transition, dict)
        )
    if isinstance(gates, dict):
        for gate, record in gates.items():
            history = record.get("history") if isinstance(record, dict) else None
            if not isinstance(history, list):
                continue
            recorded_events.extend(
                (f"Gate {gate} history[{index}].decided_at", decision.get("decided_at"))
                for index, decision in enumerate(history)
                if isinstance(decision, dict)
            )
    for event_label, raw_timestamp in recorded_events:
        event_at = parse_utc_timestamp(raw_timestamp)
        if event_at is None:
            continue
        if created_at is not None and event_at < created_at:
            errors.append(f"{event_label} must not be earlier than created_at")
        if updated_at is not None and event_at > updated_at:
            errors.append(f"{event_label} must not be later than updated_at")
