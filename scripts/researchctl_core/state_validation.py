"""State envelope, checkpoint, stage-history, and chronology validation."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .artifacts import artifact_ref_errors, retained_artifact_reference
from .constants import Policy, ResearchCtlError
from .gate_records import gate_record, iter_gate_records
from .gates import gate_ref_owner_stage, transition_requirements, transition_gate_ref
from .timeutils import parse_utc_timestamp, valid_timestamp


def _artifact_revision_existed_before(
    state: dict[str, Any], decided_at: datetime
) -> bool:
    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        return False
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
                for revision in revisions:
                    registered_at = parse_utc_timestamp(
                        revision.get("registered_at")
                        if isinstance(revision, dict)
                        else None
                    )
                    if registered_at is not None and registered_at < decided_at:
                        return True
    return False


def validate_activation_history(
    state: dict[str, Any], policy: Policy, errors: list[str]
) -> None:
    history = state.get("activation_history")
    if not isinstance(history, list):
        errors.append("activation_history must be a list")
        return
    expected_enabled = True
    previous_decided_at: datetime | None = None
    for index, event in enumerate(history):
        prefix = f"activation_history[{index}]"
        if not isinstance(event, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if set(event) != set(policy.runtime.activation_event_fields):
            errors.append(
                f"{prefix} fields must be: "
                + ", ".join(policy.runtime.activation_event_fields)
            )
        action = event.get("action")
        previous_enabled = event.get("previous_enabled")
        new_enabled = event.get("new_enabled")
        if action not in policy.runtime.activation_actions:
            errors.append(f"{prefix} has invalid action {action!r}")
        if type(previous_enabled) is not bool or previous_enabled is not expected_enabled:
            errors.append(f"{prefix} previous_enabled does not continue history")
        expected_new = {"enable": True, "disable": False}.get(action)
        if type(new_enabled) is not bool or new_enabled is not expected_new:
            errors.append(f"{prefix} new_enabled does not match action")
        if isinstance(new_enabled, bool):
            expected_enabled = new_enabled
        for field in ("reason", "actor"):
            if not isinstance(event.get(field), str) or not event[field].strip():
                errors.append(f"{prefix} {field} must be non-empty")
        decided_at = parse_utc_timestamp(event.get("decided_at"))
        if decided_at is None:
            errors.append(
                f"{prefix} decided_at must be a timezone-explicit UTC timestamp"
            )
        elif previous_decided_at is not None and decided_at <= previous_decided_at:
            errors.append(f"{prefix} decided_at must be later than the prior event")
        else:
            previous_decided_at = decided_at
    if state.get("enabled") is not expected_enabled:
        errors.append("enabled does not match activation_history")


def validate_lifecycle_record(
    root: Path,
    state: dict[str, Any],
    policy: Policy,
    gate_decisions: dict[str, tuple[str, str | None, dict[str, Any]]],
    errors: list[str],
) -> None:
    lifecycle = state.get("lifecycle")
    if not isinstance(lifecycle, dict):
        errors.append("lifecycle must be an object")
        return
    if set(lifecycle) != set(policy.runtime.lifecycle_record_fields):
        errors.append(
            "lifecycle fields must be: "
            + ", ".join(policy.runtime.lifecycle_record_fields)
        )
    status = lifecycle.get("status")
    latest = lifecycle.get("latest_decision_id")
    history = lifecycle.get("history")
    if status not in policy.runtime.lifecycle_statuses:
        errors.append(f"lifecycle has invalid status {status!r}")
    if not isinstance(history, list):
        errors.append("lifecycle history must be a list")
        return
    if not history:
        if status != "active":
            errors.append("lifecycle without history must be active")
        if latest is not None:
            errors.append("lifecycle latest_decision_id must be null without history")
        return

    expected_status = "active"
    previous_decided_at: datetime | None = None
    decision_ids = set(gate_decisions)
    expected_fields = set(policy.runtime.decision_required_fields) | set(
        policy.runtime.lifecycle_decision_fields
    )
    optional_fields = set(policy.runtime.lifecycle_decision_optional_fields)
    for index, decision in enumerate(history):
        prefix = f"lifecycle history[{index}]"
        if not isinstance(decision, dict):
            errors.append(f"{prefix} must be an object")
            continue
        missing = expected_fields - set(decision)
        unknown = set(decision) - expected_fields - optional_fields
        if missing:
            errors.append(f"{prefix} missing fields: {', '.join(sorted(missing))}")
        if unknown:
            errors.append(f"{prefix} has unknown fields: {', '.join(sorted(unknown))}")

        identifier = decision.get("decision_id")
        if not isinstance(identifier, str) or not identifier:
            errors.append(f"{prefix} decision_id must be non-empty")
        elif identifier in decision_ids:
            errors.append(f"{prefix} duplicates decision_id {identifier}")
        else:
            decision_ids.add(identifier)

        action = decision.get("action")
        previous_status = decision.get("previous_status")
        new_status = decision.get("new_status")
        if action not in policy.runtime.lifecycle_actions:
            errors.append(f"{prefix} has invalid action {action!r}")
        if previous_status != expected_status:
            errors.append(
                f"{prefix} previous_status {previous_status!r} does not continue "
                f"from {expected_status!r}"
            )
        expected_new = {
            ("active", "terminate"): "terminated",
            ("active", "complete"): "completed",
            ("terminated", "reopen"): "active",
            ("completed", "reopen"): "active",
        }.get((previous_status, action))
        if new_status != expected_new:
            errors.append(f"{prefix} has an invalid lifecycle transition")
        if new_status in policy.runtime.lifecycle_statuses:
            expected_status = new_status

        for field in ("reason", "actor"):
            if not isinstance(decision.get(field), str) or not decision[field].strip():
                errors.append(f"{prefix} {field} must be non-empty")
        for field, require_value in (
            ("supporting_evidence_ids", True),
            ("opposing_evidence_ids", False),
            ("unresolved_risks", False),
            ("decision_conditions", True),
        ):
            values = decision.get(field)
            if not isinstance(values, list) or not all(
                isinstance(value, str) and value.strip() for value in values
            ):
                errors.append(f"{prefix} {field} must be a string list")
            elif require_value and not values:
                errors.append(f"{prefix} {field} must not be empty")
            elif len(values) != len(set(values)):
                errors.append(f"{prefix} {field} must not contain duplicates")

        decided_at = parse_utc_timestamp(decision.get("decided_at"))
        if decided_at is None:
            errors.append(
                f"{prefix} decided_at must be a timezone-explicit UTC timestamp"
            )
        elif previous_decided_at is not None and decided_at <= previous_decided_at:
            errors.append(f"{prefix} decided_at must be later than the prior decision")
        else:
            previous_decided_at = decided_at
        if action == "complete" and decided_at is not None:
            release = gate_record(
                state,
                policy,
                policy.release_gate,
                policy.initial_release_target,
            )
            release_history = (
                release.get("history") if isinstance(release, dict) else None
            )
            prior_release = [
                item
                for item in (
                    release_history if isinstance(release_history, list) else []
                )
                if isinstance(item, dict)
                and (at := parse_utc_timestamp(item.get("decided_at"))) is not None
                and at < decided_at
            ]
            if not prior_release or prior_release[-1].get("new_status") != "approved":
                errors.append(
                    f"{prefix} complete requires prior approved Gate "
                    f"{policy.release_gate}/{policy.initial_release_target}"
                )
        if decision.get("stage") not in policy.stage_order:
            errors.append(f"{prefix} has unknown stage {decision.get('stage')!r}")

        gate_ref = decision.get("gate_ref")
        gate_decision_id = decision.get("gate_decision_id")
        if (gate_ref is None) != (gate_decision_id is None):
            errors.append(
                f"{prefix} gate_ref and gate_decision_id must appear together"
            )
        elif gate_ref is not None:
            if action != "reopen":
                errors.append(f"{prefix} Gate linkage is valid only for reopen")
            linked = gate_decisions.get(gate_decision_id)
            if linked is None or linked[2].get("action") != "reopen":
                errors.append(f"{prefix} gate_decision_id must name a Gate reopen")
            else:
                linked_gate, linked_target, _linked_decision = linked
                expected_ref = {
                    "gate": linked_gate,
                    **({"target": linked_target} if linked_target is not None else {}),
                }
                if gate_ref != expected_ref:
                    errors.append(f"{prefix} gate_ref does not match gate_decision_id")
        elif previous_status == "completed" and action == "reopen":
            errors.append(f"{prefix} completed reopen must link an affected Gate")

        artifact_refs = decision.get("artifact_refs")
        if not isinstance(artifact_refs, list):
            errors.append(f"{prefix} artifact_refs must be a list")
            continue
        if not artifact_refs:
            if decided_at is None or _artifact_revision_existed_before(
                state, decided_at
            ):
                errors.append(
                    f"{prefix} artifact_refs must be non-empty when registered "
                    "artifacts existed at decision time"
                )
            continue
        seen_labels: set[str] = set()
        for ref_index, reference in enumerate(artifact_refs):
            ref_prefix = f"{prefix}.artifact_refs[{ref_index}]"
            errors.extend(
                artifact_ref_errors(
                    root,
                    policy,
                    reference,
                    ref_prefix,
                    verify_source=False,
                    verify_snapshot=False,
                )
            )
            if not isinstance(reference, dict):
                continue
            retained = retained_artifact_reference(state, policy, reference)
            if retained is None or retained != reference:
                errors.append(
                    f"{ref_prefix} does not exactly match a retained artifact "
                    "registry revision"
                )
            label = reference.get("label")
            if isinstance(label, str):
                if label in seen_labels:
                    errors.append(f"{ref_prefix} duplicates label {label}")
                seen_labels.add(label)

    last = history[-1] if isinstance(history[-1], dict) else {}
    if latest != last.get("decision_id"):
        errors.append(
            "lifecycle latest_decision_id does not match its last history entry"
        )
    if status != last.get("new_status"):
        errors.append("lifecycle status does not match its last decision")


def validate_state_envelope(
    state: dict[str, Any],
    policy: Policy,
    errors: list[str],
) -> tuple[datetime | None, datetime | None]:
    configured_fields = set(policy.runtime.state_required_fields)
    missing = configured_fields - set(state)
    extra = set(state) - configured_fields
    if missing:
        errors.append("missing state fields: " + ", ".join(sorted(missing)))
    if extra:
        errors.append("unknown state fields: " + ", ".join(sorted(extra)))
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
    gate_decisions_by_id: dict[
        str, tuple[str, str | None, dict[str, Any]]
    ],
    created_at: datetime | None,
    updated_at: datetime | None,
    errors: list[str],
) -> None:
    checkpoint = state.get("last_checkpoint")
    if checkpoint is not None:
        if not isinstance(checkpoint, dict):
            errors.append("last_checkpoint must be null or an object")
        else:
            checkpoint_fields = set(policy.runtime.checkpoint_fields)
            if set(checkpoint) != checkpoint_fields:
                errors.append(
                    "last_checkpoint fields must be: "
                    + ", ".join(policy.runtime.checkpoint_fields)
                )
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
            required = set(policy.runtime.stage_transition_fields)
            absent = required - set(transition)
            unknown = set(transition) - required
            if absent:
                errors.append(
                    f"{prefix} missing fields: {', '.join(sorted(absent))}"
                )
                continue
            if unknown:
                errors.append(
                    f"{prefix} has unknown fields: {', '.join(sorted(unknown))}"
                )
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
            trigger = transition.get("trigger")
            gate_prefixes = tuple(
                prefix
                for prefix in policy.runtime.stage_transition_trigger_prefixes
                if prefix.startswith("gate-")
            )
            gate_trigger = (
                re.fullmatch(
                    rf"({'|'.join(re.escape(prefix) for prefix in gate_prefixes)}):(.+)",
                    trigger,
                )
                if isinstance(trigger, str)
                else None
            )
            if (
                from_stage in policy.stage_order
                and to_stage in policy.stage_order
                and gate_trigger is None
            ):
                try:
                    requirements = transition_requirements(policy, from_stage, to_stage)
                except ResearchCtlError as exc:
                    errors.append(f"{prefix}: {exc}")
                else:
                    if requirements:
                        errors.append(
                            f"{prefix} checkpoint cannot drive a stage-exit transition"
                        )
            if not isinstance(trigger, str) or not trigger.strip():
                errors.append(f"{prefix} trigger must be non-empty")
            else:
                if gate_trigger:
                    expected_action = gate_trigger.group(1).removeprefix("gate-")
                    trigger_decision_id = gate_trigger.group(2)
                    linked = gate_decisions_by_id.get(trigger_decision_id)
                    if linked is None:
                        errors.append(f"{prefix} references an unknown Gate decision")
                    else:
                        linked_gate, linked_target, linked_decision = linked
                        if linked_decision.get("action") != expected_action:
                            errors.append(f"{prefix} trigger action does not match its decision")
                        if linked_decision.get("cascade") is not None:
                            errors.append(
                                f"{prefix} cascade decisions must not drive stage "
                                "transitions"
                            )
                        if expected_action == "reopen":
                            if linked_decision.get("decided_at") != transition.get("timestamp"):
                                errors.append(
                                    f"{prefix} timestamp does not match its Gate decision"
                                )
                            expected_target = gate_ref_owner_stage(
                                policy, linked_gate, linked_target
                            )
                            if to_stage != expected_target:
                                errors.append(
                                    f"{prefix} target does not match its GateRef owner stage"
                                )
                        elif (
                            from_stage in policy.stage_order
                            and to_stage in policy.stage_order
                        ):
                            transition_at = parse_utc_timestamp(transition.get("timestamp"))
                            decision_at = parse_utc_timestamp(linked_decision.get("decided_at"))
                            linked_record = gate_record(
                                state, policy, linked_gate, linked_target
                            )
                            linked_history = (
                                linked_record.get("history")
                                if isinstance(linked_record, dict)
                                else None
                            )
                            decisions_at_transition = [
                                item
                                for item in (
                                    linked_history
                                    if isinstance(linked_history, list)
                                    else []
                                )
                                if isinstance(item, dict)
                                and (
                                    item_at := parse_utc_timestamp(item.get("decided_at"))
                                )
                                is not None
                                and transition_at is not None
                                and item_at <= transition_at
                            ]
                            if (
                                decision_at is None
                                or transition_at is None
                                or decision_at > transition_at
                                or not decisions_at_transition
                                or decisions_at_transition[-1].get("decision_id")
                                != trigger_decision_id
                                or decisions_at_transition[-1].get("new_status")
                                != "approved"
                            ):
                                errors.append(
                                    f"{prefix} must use the active Gate approval at "
                                    "the transition timestamp"
                                )
                            try:
                                expected_ref = transition_gate_ref(
                                    policy, from_stage, to_stage
                                )
                            except ResearchCtlError as exc:
                                errors.append(f"{prefix}: {exc}")
                            else:
                                if expected_ref != (linked_gate, linked_target):
                                    errors.append(
                                        f"{prefix} target does not match its GateRef transition"
                                    )
                elif trigger not in policy.runtime.stage_transition_trigger_prefixes:
                    errors.append(f"{prefix} has unsupported trigger {trigger!r}")
            transition_at = parse_utc_timestamp(transition.get("timestamp"))
            if transition_at is None:
                errors.append(
                    f"{prefix} timestamp must be a timezone-explicit UTC timestamp"
                )
            elif (
                previous_transition_at is not None
                and transition_at <= previous_transition_at
            ):
                errors.append(
                    f"{prefix} timestamp must be later than the prior transition"
                )
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
        for gate, target, record in iter_gate_records(state, policy):
            history = record.get("history") if isinstance(record, dict) else None
            if not isinstance(history, list):
                continue
            recorded_events.extend(
                (
                    f"Gate {gate}{f'/{target}' if target is not None else ''} "
                    f"history[{index}].decided_at",
                    decision.get("decided_at"),
                )
                for index, decision in enumerate(history)
                if isinstance(decision, dict)
            )
    lifecycle = state.get("lifecycle")
    lifecycle_history = (
        lifecycle.get("history") if isinstance(lifecycle, dict) else None
    )
    if isinstance(lifecycle_history, list):
        recorded_events.extend(
            (
                f"lifecycle history[{index}].decided_at",
                decision.get("decided_at"),
            )
            for index, decision in enumerate(lifecycle_history)
            if isinstance(decision, dict)
        )
    activation_history = state.get("activation_history")
    if isinstance(activation_history, list):
        recorded_events.extend(
            (
                f"activation_history[{index}].decided_at",
                event.get("decided_at"),
            )
            for index, event in enumerate(activation_history)
            if isinstance(event, dict)
        )
    artifacts = state.get("artifacts")
    if isinstance(artifacts, dict):
        for stage, stage_bucket in artifacts.items():
            if not isinstance(stage_bucket, dict):
                continue
            for role, role_bucket in stage_bucket.items():
                if not isinstance(role_bucket, dict):
                    continue
                for artifact_id, entry in role_bucket.items():
                    revisions = entry.get("revisions") if isinstance(entry, dict) else None
                    if not isinstance(revisions, list):
                        continue
                    previous_registered_at: datetime | None = None
                    for index, revision in enumerate(revisions):
                        if not isinstance(revision, dict):
                            continue
                        label = (
                            f"artifacts.{stage}.{role}.{artifact_id}."
                            f"revisions[{index}].registered_at"
                        )
                        raw_registered_at = revision.get("registered_at")
                        registered_at = parse_utc_timestamp(raw_registered_at)
                        recorded_events.append((label, raw_registered_at))
                        if (
                            registered_at is not None
                            and previous_registered_at is not None
                            and registered_at <= previous_registered_at
                        ):
                            errors.append(
                                f"{label} must be later than the prior revision"
                            )
                        if registered_at is not None:
                            previous_registered_at = registered_at
    for event_label, raw_timestamp in recorded_events:
        event_at = parse_utc_timestamp(raw_timestamp)
        if event_at is None:
            continue
        if created_at is not None and event_at < created_at:
            errors.append(f"{event_label} must not be earlier than created_at")
        if updated_at is not None and event_at > updated_at:
            errors.append(f"{event_label} must not be later than updated_at")
