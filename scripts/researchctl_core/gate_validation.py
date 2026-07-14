"""Gate-history, prerequisite, and artifact-binding validation."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .artifacts import artifact_pointer_errors, iter_gate_artifact_refs
from .constants import (
    ARTIFACT_METADATA_FIELDS,
    GATE_ACTIONS,
    GATE_IDS,
    GATE_STATUSES,
    Policy,
    ResearchCtlError,
)
from .gates import (
    gate_artifact_refs,
    required_artifact_roles_for_gate,
    required_gates,
)
from .timeutils import parse_utc_timestamp


def validate_gate_records(
    root: Path,
    state: dict[str, Any],
    policy: Policy,
    errors: list[str],
) -> tuple[Any, dict[str, tuple[str, dict[str, Any]]]]:
    gate_decisions_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    gates = state.get("gates")
    if not isinstance(gates, dict):
        errors.append("gates must be an object")
    else:
        missing_gates = set(GATE_IDS) - set(gates)
        extra_gates = set(gates) - set(GATE_IDS)
        if missing_gates:
            errors.append("missing Gates: " + ", ".join(sorted(missing_gates)))
        if extra_gates:
            errors.append("unknown Gates: " + ", ".join(sorted(extra_gates)))
        all_decision_ids: set[str] = set()
        for gate in GATE_IDS:
            record = gates.get(gate)
            if not isinstance(record, dict):
                errors.append(f"Gate {gate} must be an object")
                continue
            status = record.get("status")
            history = record.get("history")
            latest = record.get("latest_decision_id")
            if not isinstance(status, str) or status not in GATE_STATUSES:
                errors.append(f"Gate {gate} has invalid status {status!r}")
            if not isinstance(history, list):
                errors.append(f"Gate {gate} history must be a list")
                continue
            if not history:
                if latest is not None:
                    errors.append(
                        f"Gate {gate} latest_decision_id must be null without history"
                    )
                if status != "pending":
                    errors.append(f"Gate {gate} without history must be pending")
                continue
            expected_previous_status = "pending"
            previous_decided_at: datetime | None = None
            latest_approved_refs: list[Any] | None = None
            for index, decision in enumerate(history):
                prefix = f"Gate {gate} history[{index}]"
                if not isinstance(decision, dict):
                    errors.append(f"{prefix} must be an object")
                    continue
                required = {
                    "decision_id",
                    "action",
                    "previous_status",
                    "new_status",
                    "reason",
                    "actor",
                    "decided_at",
                    "artifact_refs",
                }
                absent = required - set(decision)
                if absent:
                    errors.append(
                        f"{prefix} missing fields: {', '.join(sorted(absent))}"
                    )
                    continue
                identifier = decision.get("decision_id")
                if not isinstance(identifier, str) or not identifier:
                    errors.append(f"{prefix} decision_id must be non-empty")
                elif identifier in all_decision_ids:
                    errors.append(
                        f"{prefix} duplicates decision_id {identifier} across Gate history"
                    )
                else:
                    all_decision_ids.add(identifier)
                    gate_decisions_by_id[identifier] = (gate, decision)
                action = decision.get("action")
                previous_status = decision.get("previous_status")
                new_status = decision.get("new_status")
                if not isinstance(action, str) or action not in GATE_ACTIONS:
                    errors.append(f"{prefix} has invalid action")
                if not isinstance(previous_status, str) or previous_status not in GATE_STATUSES:
                    errors.append(f"{prefix} has invalid previous_status")
                elif previous_status != expected_previous_status:
                    errors.append(
                        f"{prefix} previous_status {previous_status!r} does not continue "
                        f"the Gate history from {expected_previous_status!r}"
                    )
                if not isinstance(new_status, str) or new_status not in GATE_STATUSES:
                    errors.append(f"{prefix} has invalid new_status")
                if action == "approve" and (
                    previous_status == "approved" or new_status != "approved"
                ):
                    errors.append(
                        f"{prefix} approve must transition pending/reopened -> approved"
                    )
                if action == "reopen" and (
                    previous_status != "approved" or new_status != "reopened"
                ):
                    errors.append(
                        f"{prefix} reopen must transition approved -> reopened"
                    )
                if isinstance(new_status, str) and new_status in GATE_STATUSES:
                    expected_previous_status = new_status
                if not isinstance(decision.get("reason"), str) or not decision[
                    "reason"
                ].strip():
                    errors.append(f"{prefix} reason must be non-empty")
                if not isinstance(decision.get("actor"), str) or not decision[
                    "actor"
                ].strip():
                    errors.append(f"{prefix} actor must be non-empty")
                decided_at = parse_utc_timestamp(decision.get("decided_at"))
                if decided_at is None:
                    errors.append(
                        f"{prefix} decided_at must be a timezone-explicit UTC timestamp"
                    )
                elif previous_decided_at is not None and decided_at < previous_decided_at:
                    errors.append(f"{prefix} decided_at is earlier than the prior decision")
                else:
                    previous_decided_at = decided_at
                artifact_refs = decision.get("artifact_refs")
                if not isinstance(artifact_refs, list):
                    errors.append(f"{prefix} artifact_refs must be a list")
                else:
                    seen_ref_labels: set[str] = set()
                    seen_ref_roles: set[str] = set()
                    for ref_index, reference in enumerate(artifact_refs):
                        ref_prefix = f"{prefix}.artifact_refs[{ref_index}]"
                        if not isinstance(reference, dict):
                            errors.append(f"{ref_prefix} must be an artifact pointer")
                            continue
                        label = reference.get("label")
                        if not isinstance(label, str) or not label.strip():
                            errors.append(f"{ref_prefix}.label must be non-empty")
                        elif label in seen_ref_labels:
                            errors.append(f"{ref_prefix} duplicates label {label}")
                        else:
                            seen_ref_labels.add(label)
                            label_match = re.fullmatch(
                                r"artifacts\.([^.]+)\.([^.]+)\.(.+)", label
                            )
                            if label_match is None:
                                errors.append(
                                    f"{ref_prefix}.label must use "
                                    "artifacts.<stage>.<role>.<artifact_id>"
                                )
                            else:
                                ref_role = (
                                    f"{label_match.group(1)}.{label_match.group(2)}"
                                )
                                seen_ref_roles.add(ref_role)
                                if reference.get("artifact_id") != label_match.group(3):
                                    errors.append(
                                        f"{ref_prefix}.artifact_id must match its label"
                                    )
                        errors.extend(
                            artifact_pointer_errors(
                                root,
                                reference,
                                ref_prefix,
                                verify_integrity=False,
                            )
                        )
                    if action == "approve":
                        latest_approved_refs = artifact_refs
                        if artifact_refs:
                            try:
                                expected_ref_roles = set(
                                    required_artifact_roles_for_gate(
                                        policy,
                                        gate,
                                        decision.get("release_target")
                                        if gate == "release"
                                        else None,
                                    )
                                )
                            except ResearchCtlError as exc:
                                errors.append(f"{prefix}: {exc}")
                            else:
                                unexpected = seen_ref_roles - expected_ref_roles
                                missing_roles = expected_ref_roles - seen_ref_roles
                                if unexpected:
                                    errors.append(
                                        f"{prefix} has artifact_refs from unexpected roles: "
                                        f"{', '.join(sorted(unexpected))}"
                                    )
                                if missing_roles:
                                    errors.append(
                                        f"{prefix} artifact_refs are missing required roles: "
                                        f"{', '.join(sorted(missing_roles))}"
                                    )
                    elif (
                        action == "reopen"
                        and latest_approved_refs is not None
                        and artifact_refs != latest_approved_refs
                    ):
                        errors.append(
                            f"{prefix} reopen artifact_refs must match the latest approval"
                        )
                if gate == "release":
                    allowed_targets = policy.gate_specs["release"].get(
                        "release_targets", []
                    )
                    if decision.get("release_target") not in allowed_targets:
                        errors.append(f"{prefix} has invalid or missing release_target")
                elif "release_target" in decision:
                    errors.append(f"{prefix} must not define release_target")
            last = history[-1] if isinstance(history[-1], dict) else {}
            if latest != last.get("decision_id"):
                errors.append(
                    f"Gate {gate} latest_decision_id does not match its last history entry"
                )
            if status != last.get("new_status"):
                errors.append(f"Gate {gate} status does not match its last decision")

        for gate_index, gate in enumerate(policy.gate_order):
            record = gates.get(gate)
            if not isinstance(record, dict) or record.get("status") != "approved":
                continue
            prerequisites = tuple(
                dict.fromkeys(
                    (
                        *policy.gate_order[:gate_index],
                        *required_gates(policy.gate_specs[gate]),
                    )
                )
            )
            for prerequisite in prerequisites:
                prerequisite_record = gates.get(prerequisite)
                if (
                    not isinstance(prerequisite_record, dict)
                    or prerequisite_record.get("status") != "approved"
                ):
                    errors.append(
                        f"approved Gate {gate} requires approved Gate {prerequisite}"
                    )
        # Current status alone cannot prove that a forged or later-reopened
        # downstream approval was legal when it was recorded. Validate each
        # approval against prerequisite approval history at that point in time.
        for gate_index, gate in enumerate(policy.gate_order):
            record = gates.get(gate)
            history = record.get("history") if isinstance(record, dict) else None
            if not isinstance(history, list):
                continue
            prerequisites = tuple(
                dict.fromkeys(
                    (
                        *policy.gate_order[:gate_index],
                        *required_gates(policy.gate_specs[gate]),
                    )
                )
            )
            for decision_index, decision in enumerate(history):
                if not isinstance(decision, dict) or decision.get("action") != "approve":
                    continue
                approved_at = parse_utc_timestamp(decision.get("decided_at"))
                if approved_at is None:
                    continue
                for prerequisite in prerequisites:
                    prerequisite_record = gates.get(prerequisite)
                    prerequisite_history = (
                        prerequisite_record.get("history")
                        if isinstance(prerequisite_record, dict)
                        else None
                    )
                    strict_prior: list[dict[str, Any]] = []
                    same_time_approval = False
                    if isinstance(prerequisite_history, list):
                        for candidate in prerequisite_history:
                            if not isinstance(candidate, dict):
                                continue
                            candidate_at = parse_utc_timestamp(
                                candidate.get("decided_at")
                            )
                            if candidate_at is None:
                                continue
                            if candidate_at < approved_at:
                                strict_prior.append(candidate)
                            elif (
                                candidate_at == approved_at
                                and candidate.get("action") == "approve"
                            ):
                                # Older second-resolution records cannot establish a
                                # cross-Gate order, so an equal-time approval is accepted.
                                same_time_approval = True
                    prerequisite_was_approved = (
                        bool(strict_prior)
                        and strict_prior[-1].get("new_status") == "approved"
                    ) or same_time_approval
                    if not prerequisite_was_approved:
                        errors.append(
                            f"Gate {gate} history[{decision_index}] approval lacks a prior "
                            f"approval of prerequisite Gate {prerequisite}"
                        )

        current_stage = state.get("current_stage")
        if current_stage in policy.stage_order:
            current_index = policy.stage_order.index(current_stage)
            for gate in policy.gate_order:
                target = policy.gate_specs[gate].get("advance_to")
                record = gates.get(gate)
                release_stage_is_satisfied = False
                if gate == "release" and current_stage == "revision":
                    release_history = (
                        record.get("history") if isinstance(record, dict) else []
                    )
                    release_stage_is_satisfied = isinstance(
                        release_history, list
                    ) and any(
                        isinstance(decision, dict)
                        and decision.get("action") == "approve"
                        and decision.get("release_target") == "initial_submission"
                        for decision in release_history
                    )
                if (
                    target in policy.stage_order
                    and current_index >= policy.stage_order.index(target)
                    and isinstance(record, dict)
                    and record.get("status") != "approved"
                    and not release_stage_is_satisfied
                ):
                    errors.append(
                        f"current_stage {current_stage!r} requires approved Gate {gate}"
                    )
    return gates, gate_decisions_by_id


def validate_gate_bindings(
    root: Path,
    state: dict[str, Any],
    policy: Policy,
    gates: Any,
    errors: list[str],
    warnings: list[str],
    *,
    verify_artifact_integrity: bool,
    allow_binding_drift_for: frozenset[str],
) -> None:
    if isinstance(gates, dict):
        for gate in policy.gate_order:
            record = gates.get(gate)
            history = record.get("history") if isinstance(record, dict) else None
            if (
                not isinstance(record, dict)
                or record.get("status") != "approved"
                or not isinstance(history, list)
            ):
                continue
            approval = next(
                (
                    decision
                    for decision in reversed(history)
                    if isinstance(decision, dict)
                    and decision.get("action") == "approve"
                ),
                None,
            )
            approved_refs = (
                approval.get("artifact_refs") if isinstance(approval, dict) else None
            )
            if not isinstance(approved_refs, list) or not approved_refs:
                continue
            release_target = (
                approval.get("release_target") if gate == "release" else None
            )
            try:
                current_refs = gate_artifact_refs(
                    root,
                    state,
                    policy,
                    gate,
                    release_target,
                    verify_integrity=verify_artifact_integrity,
                )
            except ResearchCtlError as exc:
                message = (
                    f"approved Gate {gate} no longer has a valid current artifact "
                    f"binding: {exc}"
                )
                (warnings if gate in allow_binding_drift_for else errors).append(message)
                continue

            def comparable_refs(values: list[Any]) -> list[str]:
                fields = ("label", "path", *ARTIFACT_METADATA_FIELDS)
                normalized = [
                    {field: value.get(field) for field in fields}
                    for value in values
                    if isinstance(value, dict)
                ]
                return sorted(
                    json.dumps(value, ensure_ascii=False, sort_keys=True)
                    for value in normalized
                )

            if comparable_refs(current_refs) != comparable_refs(approved_refs):
                message = (
                    f"approved Gate {gate} current artifacts differ from its latest "
                    "approved artifact_refs; reopen the Gate before changing them"
                )
                (warnings if gate in allow_binding_drift_for else errors).append(message)

    for history_label, reference in iter_gate_artifact_refs(state):
        structural_pointer_errors = artifact_pointer_errors(
            root,
            reference,
            history_label,
            verify_integrity=False,
        )
        if structural_pointer_errors:
            warnings.extend(
                f"historical Gate artifact reference is invalid: {error}"
                for error in structural_pointer_errors
            )
            continue
        if verify_artifact_integrity:
            integrity_errors = artifact_pointer_errors(
                root,
                reference,
                history_label,
                verify_integrity=True,
            )
            warnings.extend(
                f"historical Gate artifact is no longer verifiable: {error}"
                for error in integrity_errors
            )
    if isinstance(gates, dict):
        for gate, record in gates.items():
            history = record.get("history") if isinstance(record, dict) else None
            if not isinstance(history, list):
                continue
            for index, decision in enumerate(history):
                if (
                    isinstance(decision, dict)
                    and decision.get("action") == "approve"
                    and decision.get("artifact_refs") == []
                ):
                    warnings.append(
                        f"Gate {gate} history[{index}] predates required artifact binding"
                    )
