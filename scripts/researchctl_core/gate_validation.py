"""Gate history, prerequisite, selection, cascade, and binding validation."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .artifacts import (
    artifact_ref_errors,
    iter_gate_artifact_refs,
    retained_artifact_reference,
)
from .constants import Policy, ResearchCtlError
from .gate_records import approval_targets_for_gate, gate_record, iter_gate_records
from .gates import (
    GateRef,
    artifact_role_contract_for_gate,
    gate_artifact_refs,
    gate_ref_prerequisites,
    selection_artifact_ref,
    transition_gate_ref,
    waived_artifact_roles_for_refs,
)
from .policy import mutable_after_approval_roles, retrospective_gate_contract
from .timeutils import parse_utc_timestamp


DecisionIndex = dict[str, tuple[str, str | None, dict[str, Any]]]


def _gate_label(gate: str, target: str | None) -> str:
    return gate + (f"/{target}" if target is not None else "")


def _gate_ref_object(gate: str, target: str | None) -> dict[str, str]:
    value = {"gate": gate}
    if target is not None:
        value["target"] = target
    return value


def _parsed_gate_ref(value: Any, policy: Policy) -> GateRef | None:
    required = set(policy.runtime.gate_ref_required_fields)
    optional = set(policy.runtime.gate_ref_optional_fields)
    if (
        not isinstance(value, dict)
        or not required <= set(value)
        or set(value) - required - optional
    ):
        return None
    gate = value.get("gate")
    target = value.get("target")
    reference = (gate, target)
    return reference if reference in policy.gate_sequence else None


def _reference_role(reference: Any) -> str | None:
    label = reference.get("label") if isinstance(reference, dict) else None
    if not isinstance(label, str):
        return None
    match = re.fullmatch(r"artifacts\.([^.]+)\.([^.]+)\.(.+)", label)
    if match is None or reference.get("artifact_id") != match.group(3):
        return None
    return f"{match.group(1)}.{match.group(2)}"


def _approval_before(history: Any, decided_at: datetime) -> bool:
    if not isinstance(history, list):
        return False
    candidates: list[tuple[datetime, int, dict[str, Any]]] = []
    for index, decision in enumerate(history):
        if not isinstance(decision, dict):
            continue
        timestamp = parse_utc_timestamp(decision.get("decided_at"))
        if timestamp is not None and timestamp < decided_at:
            candidates.append((timestamp, index, decision))
    return bool(candidates) and candidates[-1][2].get("new_status") == "approved"


def _validate_cascade_contract(
    policy: Policy, decisions: DecisionIndex, errors: list[str]
) -> None:
    events: list[
        tuple[datetime, GateRef, str, dict[str, Any]]
    ] = []
    linked_by_upstream: dict[
        str, list[tuple[datetime, GateRef, str, dict[str, Any]]]
    ] = {}
    for identifier, (gate, target, decision) in decisions.items():
        decided_at = parse_utc_timestamp(decision.get("decided_at"))
        if decided_at is None:
            continue
        event = (decided_at, (gate, target), identifier, decision)
        events.append(event)
        cascade = decision.get("cascade")
        upstream_id = (
            cascade.get("upstream_decision_id") if isinstance(cascade, dict) else None
        )
        if isinstance(upstream_id, str):
            linked_by_upstream.setdefault(upstream_id, []).append(event)
    events.sort(key=lambda event: (event[0], event[2]))

    def status_before(reference: GateRef, timestamp: datetime) -> str | None:
        prior = [
            event
            for event in events
            if event[1] == reference and event[0] < timestamp
        ]
        return prior[-1][3].get("new_status") if prior else None

    for upstream_id, linked in linked_by_upstream.items():
        upstream = decisions.get(upstream_id)
        if upstream is None:
            errors.append(f"cascade decisions reference unknown upstream decision {upstream_id}")
            continue
        upstream_gate, upstream_target, upstream_decision = upstream
        upstream_ref = (upstream_gate, upstream_target)
        upstream_at = parse_utc_timestamp(upstream_decision.get("decided_at"))
        if upstream_at is None:
            continue
        if upstream_decision.get("action") != "reopen" or "cascade" in upstream_decision:
            errors.append(f"cascade upstream decision {upstream_id} must be a root reopen")
        linked.sort(key=lambda event: (event[0], event[2]))
        if any(later[0] <= earlier[0] for earlier, later in zip(linked, linked[1:])):
            errors.append(f"cascade for {upstream_id} timestamps must be strictly increasing")
        start_at = linked[0][0]
        upstream_index = policy.gate_sequence.index(upstream_ref)
        expected = [
            reference
            for reference in reversed(policy.gate_sequence[upstream_index + 1 :])
            if status_before(reference, start_at) == "approved"
        ]
        actual = [event[1] for event in linked]
        if actual != expected:
            errors.append(
                f"cascade for {upstream_id} must reopen exactly the approved "
                "downstream GateRefs in reverse approval sequence"
            )
        linked_ids = {event[2] for event in linked}
        for decided_at, downstream_ref, identifier, decision in linked:
            cascade = decision.get("cascade")
            prefix = f"cascade decision {identifier}"
            if decision.get("action") != "reopen":
                errors.append(f"{prefix} must use action reopen")
            if not isinstance(cascade, dict):
                continue
            if cascade.get("upstream_gate_ref") != _gate_ref_object(*upstream_ref):
                errors.append(f"{prefix} upstream_gate_ref does not match its decision")
            if cascade.get("upstream_reason") != upstream_decision.get("reason"):
                errors.append(f"{prefix} upstream_reason does not match its decision")
            for field in (
                "supporting_evidence_ids",
                "opposing_evidence_ids",
                "unresolved_risks",
                "decision_conditions",
            ):
                if decision.get(field) != upstream_decision.get(field):
                    errors.append(f"{prefix} {field} must match its upstream decision")
            if decided_at >= upstream_at:
                errors.append(
                    f"{prefix} must point to a strictly later upstream reopen decision"
                )
            if policy.gate_sequence.index(downstream_ref) <= upstream_index:
                errors.append(f"{prefix} must belong to a downstream GateRef")
        interleaved = [
            identifier
            for decided_at, _reference, identifier, _decision in events
            if start_at <= decided_at < upstream_at and identifier not in linked_ids
        ]
        if interleaved:
            errors.append(
                f"cascade for {upstream_id} has interleaved Gate decisions: "
                + ", ".join(interleaved)
            )

    for identifier, (gate, target, decision) in decisions.items():
        if decision.get("action") != "reopen" or "cascade" in decision:
            continue
        if identifier in linked_by_upstream:
            continue
        decided_at = parse_utc_timestamp(decision.get("decided_at"))
        if decided_at is None:
            continue
        reference = (gate, target)
        index = policy.gate_sequence.index(reference)
        still_approved = [
            candidate
            for candidate in policy.gate_sequence[index + 1 :]
            if status_before(candidate, decided_at) == "approved"
        ]
        if still_approved:
            errors.append(
                f"root reopen decision {identifier} is missing its approved-downstream "
                "cascade: "
                + ", ".join(_gate_label(*candidate) for candidate in still_approved)
            )


def _validate_gate_record(
    root: Path,
    state: dict[str, Any],
    policy: Policy,
    gate: str,
    target: str | None,
    record: Any,
    decisions: DecisionIndex,
    all_decision_ids: set[str],
    errors: list[str],
) -> None:
    label = _gate_label(gate, target)
    if not isinstance(record, dict):
        errors.append(f"Gate {label} must be an object")
        return
    if set(record) != set(policy.runtime.gate_record_fields):
        errors.append(
            f"Gate {label} fields must be "
            + ", ".join(policy.runtime.gate_record_fields)
        )
    status = record.get("status")
    history = record.get("history")
    latest = record.get("latest_decision_id")
    if status not in policy.runtime.gate_statuses:
        errors.append(f"Gate {label} has invalid status {status!r}")
    if not isinstance(history, list):
        errors.append(f"Gate {label} history must be a list")
        return
    if not history:
        if latest is not None:
            errors.append(f"Gate {label} latest_decision_id must be null without history")
        if status != "pending":
            errors.append(f"Gate {label} without history must be pending")
        return

    expected_previous_status = "pending"
    previous_decided_at: datetime | None = None
    latest_approved_refs: list[Any] | None = None
    mode_specs = policy.gate_specs[gate].get("approval_modes")
    retrospective = retrospective_gate_contract(policy)
    retrospective_gate = retrospective[0] if retrospective is not None else None
    retrospective_mode = retrospective[1] if retrospective is not None else None
    for index, decision in enumerate(history):
        prefix = f"Gate {label} history[{index}]"
        if not isinstance(decision, dict):
            errors.append(f"{prefix} must be an object")
            continue
        required_fields = set(policy.runtime.decision_required_fields)
        optional_fields = set(policy.runtime.gate_decision_optional_fields)
        missing = required_fields - set(decision)
        unknown = set(decision) - required_fields - optional_fields
        if missing:
            errors.append(f"{prefix} missing fields: {', '.join(sorted(missing))}")
        if unknown:
            errors.append(f"{prefix} has unknown fields: {', '.join(sorted(unknown))}")

        identifier = decision.get("decision_id")
        if not isinstance(identifier, str) or not identifier:
            errors.append(f"{prefix} decision_id must be non-empty")
        elif identifier in all_decision_ids:
            errors.append(f"{prefix} duplicates decision_id {identifier}")
        else:
            all_decision_ids.add(identifier)
            decisions[identifier] = (gate, target, decision)

        action = decision.get("action")
        previous_status = decision.get("previous_status")
        new_status = decision.get("new_status")
        if action not in policy.runtime.gate_actions:
            errors.append(f"{prefix} has invalid action {action!r}")
        if previous_status not in policy.runtime.gate_statuses:
            errors.append(f"{prefix} has invalid previous_status")
        elif previous_status != expected_previous_status:
            errors.append(
                f"{prefix} previous_status {previous_status!r} does not continue "
                f"from {expected_previous_status!r}"
            )
        if new_status not in policy.runtime.gate_statuses:
            errors.append(f"{prefix} has invalid new_status")
        if action == "approve" and (
            previous_status == "approved" or new_status != "approved"
        ):
            errors.append(f"{prefix} approve must transition pending/reopened -> approved")
        if action == "reopen" and (
            previous_status != "approved" or new_status != "reopened"
        ):
            errors.append(f"{prefix} reopen must transition approved -> reopened")
        if new_status in policy.runtime.gate_statuses:
            expected_previous_status = new_status

        cascade = decision.get("cascade")
        if "cascade" in decision:
            if action != "reopen":
                errors.append(f"{prefix} cascade is valid only for reopen")
            if not isinstance(cascade, dict) or set(cascade) != set(
                policy.runtime.cascade_fields
            ):
                errors.append(
                    f"{prefix} cascade must contain exactly: "
                    + ", ".join(policy.runtime.cascade_fields)
                )
            else:
                if _parsed_gate_ref(cascade.get("upstream_gate_ref"), policy) is None:
                    errors.append(f"{prefix} cascade upstream_gate_ref is invalid")
                for field in ("upstream_decision_id", "upstream_reason"):
                    if not isinstance(cascade.get(field), str) or not cascade[field].strip():
                        errors.append(f"{prefix} cascade {field} must be non-empty")

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

        artifact_refs = decision.get("artifact_refs")
        seen_roles: list[str] = []
        seen_labels: set[str] = set()
        if not isinstance(artifact_refs, list):
            errors.append(f"{prefix} artifact_refs must be a list")
            artifact_refs = []
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
            if isinstance(reference, dict):
                retained = retained_artifact_reference(state, policy, reference)
                if retained is None:
                    errors.append(
                        f"{ref_prefix} does not resolve to one retained artifact registry revision"
                    )
                elif reference != retained:
                    errors.append(
                        f"{ref_prefix} does not exactly match its retained artifact registry revision"
                    )
                registered_at = parse_utc_timestamp(reference.get("registered_at"))
                if (
                    action == "approve"
                    and decided_at is not None
                    and registered_at is not None
                    and registered_at >= decided_at
                ):
                    errors.append(
                        f"{ref_prefix}.registered_at must be earlier than the approval decision"
                    )
            ref_label = reference.get("label") if isinstance(reference, dict) else None
            if isinstance(ref_label, str):
                if ref_label in seen_labels:
                    errors.append(f"{ref_prefix} duplicates label {ref_label}")
                seen_labels.add(ref_label)
            role = _reference_role(reference)
            if role is None:
                errors.append(
                    f"{ref_prefix}.label must match its artifact_id and use "
                    "artifacts.<stage>.<role>.<artifact_id>"
                )
            else:
                seen_roles.append(role)

        approval_mode = decision.get("approval_mode")
        if action == "approve":
            latest_approved_refs = artifact_refs
            if isinstance(mode_specs, dict):
                if not isinstance(approval_mode, str) or approval_mode not in mode_specs:
                    errors.append(f"{prefix} must name a configured approval_mode")
                    contract_mode = None
                else:
                    contract_mode = approval_mode
            else:
                contract_mode = None
                if "approval_mode" in decision:
                    errors.append(f"{prefix} must not define approval_mode")
            try:
                required_roles, optional_roles = artifact_role_contract_for_gate(
                    policy, gate, target, contract_mode
                )
            except ResearchCtlError as exc:
                errors.append(f"{prefix}: {exc}")
                required_roles, optional_roles = (), ()
            allowed_roles = set(required_roles) | set(optional_roles)
            if set(seen_roles) - allowed_roles:
                errors.append(f"{prefix} has artifact_refs from unexpected roles")
            if set(required_roles) - set(seen_roles):
                errors.append(f"{prefix} artifact_refs are missing required roles")
            if len(seen_roles) != len(set(seen_roles)):
                errors.append(f"{prefix} must bind exactly one canonical artifact per role")

            selection_role = policy.gate_specs[gate].get("selection_artifact_role")
            selection = decision.get("selection")
            if selection_role is None:
                if "selection" in decision:
                    errors.append(f"{prefix} must not define selection")
            elif not isinstance(selection, dict) or set(selection) != set(
                policy.runtime.selection_fields
            ):
                errors.append(
                    f"{prefix} selection must contain selected_id and artifact_ref"
                )
            else:
                if not isinstance(selection.get("selected_id"), str) or not selection[
                    "selected_id"
                ].strip():
                    errors.append(f"{prefix} selection.selected_id must be non-empty")
                try:
                    expected_ref = selection_artifact_ref(
                        policy,
                        gate,
                        [ref for ref in artifact_refs if isinstance(ref, dict)],
                    )
                except ResearchCtlError as exc:
                    errors.append(f"{prefix}: {exc}")
                else:
                    if selection.get("artifact_ref") != expected_ref:
                        errors.append(
                            f"{prefix} selection.artifact_ref must equal the bound "
                            "portfolio artifact revision"
                        )

            if gate == retrospective_gate and approval_mode == retrospective_mode:
                expected_waived = waived_artifact_roles_for_refs(
                    policy,
                    gate,
                    target,
                    approval_mode,
                    [ref for ref in artifact_refs if isinstance(ref, dict)],
                )
                if not expected_waived:
                    errors.append(
                        f"{prefix} retrospective mode must waive unavailable historical roles"
                    )
                if decision.get("waived_artifact_roles") != expected_waived:
                    errors.append(
                        f"{prefix} waived_artifact_roles do not match absent roles"
                    )
            elif "waived_artifact_roles" in decision:
                errors.append(
                    f"{prefix} waived_artifact_roles are valid only for retrospective mode"
                )
        else:
            if latest_approved_refs is not None and artifact_refs != latest_approved_refs:
                errors.append(f"{prefix} reopen artifact_refs must match the latest approval")
            for field in ("selection", "approval_mode", "waived_artifact_roles"):
                if field in decision:
                    errors.append(f"{prefix} {field} is valid only for approval")

    last = history[-1] if isinstance(history[-1], dict) else {}
    if latest != last.get("decision_id"):
        errors.append(
            f"Gate {label} latest_decision_id does not match its last history entry"
        )
    if status != last.get("new_status"):
        errors.append(f"Gate {label} status does not match its last decision")


def validate_gate_records(
    root: Path,
    state: dict[str, Any],
    policy: Policy,
    errors: list[str],
) -> tuple[Any, DecisionIndex]:
    decisions: DecisionIndex = {}
    gates = state.get("gates")
    if not isinstance(gates, dict):
        errors.append("gates must be an object")
        return gates, decisions
    missing_gates = set(policy.gate_order) - set(gates)
    extra_gates = set(gates) - set(policy.gate_order)
    if missing_gates:
        errors.append("missing Gates: " + ", ".join(sorted(missing_gates)))
    if extra_gates:
        errors.append("unknown Gates: " + ", ".join(sorted(extra_gates)))

    all_decision_ids: set[str] = set()
    for gate in policy.gate_order:
        container = gates.get(gate)
        targets = approval_targets_for_gate(policy, gate)
        if targets:
            if not isinstance(container, dict) or set(container) != set(
                policy.runtime.gate_target_container_fields
            ):
                errors.append(f"targeted Gate {gate} must contain only targets")
                continue
            records = container.get("targets")
            if not isinstance(records, dict) or set(records) != set(targets):
                errors.append(
                    f"Gate {gate}.targets must define exactly: " + ", ".join(targets)
                )
                continue
            for target in targets:
                _validate_gate_record(
                    root,
                    state,
                    policy,
                    gate,
                    target,
                    records.get(target),
                    decisions,
                    all_decision_ids,
                    errors,
                )
        else:
            _validate_gate_record(
                root,
                state,
                policy,
                gate,
                None,
                container,
                decisions,
                all_decision_ids,
                errors,
            )

    _validate_cascade_contract(policy, decisions, errors)

    for gate, target, record in iter_gate_records(state, policy):
        prerequisites = gate_ref_prerequisites(policy, gate, target)
        label = _gate_label(gate, target)
        if isinstance(record, dict) and record.get("status") == "approved":
            for prerequisite_gate, prerequisite_target in prerequisites:
                prerequisite = gate_record(
                    state, policy, prerequisite_gate, prerequisite_target
                )
                if not isinstance(prerequisite, dict) or prerequisite.get("status") != "approved":
                    errors.append(
                        f"approved Gate {label} requires approved Gate "
                        f"{_gate_label(prerequisite_gate, prerequisite_target)}"
                    )
        history = record.get("history") if isinstance(record, dict) else None
        if not isinstance(history, list):
            continue
        for index, decision in enumerate(history):
            if not isinstance(decision, dict) or decision.get("action") != "approve":
                continue
            decided_at = parse_utc_timestamp(decision.get("decided_at"))
            if decided_at is None:
                continue
            for prerequisite_gate, prerequisite_target in prerequisites:
                prerequisite = gate_record(
                    state, policy, prerequisite_gate, prerequisite_target
                )
                prerequisite_history = (
                    prerequisite.get("history") if isinstance(prerequisite, dict) else None
                )
                if not _approval_before(prerequisite_history, decided_at):
                    errors.append(
                        f"Gate {label} history[{index}] approval lacks approval of "
                        f"prerequisite Gate "
                        f"{_gate_label(prerequisite_gate, prerequisite_target)}"
                    )

    current_stage = state.get("current_stage")
    if current_stage in policy.stage_order:
        current_index = policy.stage_order.index(current_stage)
        for gate, target in policy.gate_sequence:
            destinations: list[str] = []
            for source in policy.stage_order:
                for candidate in policy.stage_transitions[source]:
                    destination = candidate.get("to")
                    if not isinstance(destination, str):
                        continue
                    try:
                        reference = transition_gate_ref(policy, source, destination)
                    except ResearchCtlError:
                        continue
                    if reference == (gate, target):
                        destinations.append(destination)
            if not destinations:
                continue
            if min(policy.stage_order.index(stage) for stage in destinations) <= current_index:
                record = gate_record(state, policy, gate, target)
                if not isinstance(record, dict) or record.get("status") != "approved":
                    errors.append(
                        f"current_stage {current_stage!r} requires approved Gate "
                        f"{_gate_label(gate, target)}"
                    )
    return gates, decisions


def _comparable_refs(values: list[Any]) -> list[str]:
    return sorted(
        json.dumps(value, ensure_ascii=False, sort_keys=True)
        for value in values
        if isinstance(value, dict)
    )


def _refs_for_roles(
    values: list[Any], roles: set[str], *, include: bool
) -> list[dict[str, Any]]:
    return [
        value
        for value in values
        if isinstance(value, dict)
        and ((_reference_role(value) in roles) is include)
    ]


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
        for gate, target, record in iter_gate_records(state, policy):
            history = record.get("history") if isinstance(record, dict) else None
            if (
                not isinstance(record, dict)
                or record.get("status") != "approved"
                or not isinstance(history, list)
            ):
                continue
            label = _gate_label(gate, target)
            approval = next(
                (
                    decision
                    for decision in reversed(history)
                    if isinstance(decision, dict) and decision.get("action") == "approve"
                ),
                None,
            )
            approved_refs = approval.get("artifact_refs") if isinstance(approval, dict) else None
            if not isinstance(approved_refs, list) or not approved_refs:
                continue
            mutable_roles = set(
                mutable_after_approval_roles(
                    policy,
                    gate,
                    target,
                    approval.get("approval_mode") if isinstance(approval, dict) else None,
                )
            )
            try:
                current_refs = gate_artifact_refs(
                    root,
                    state,
                    policy,
                    gate,
                    target,
                    verify_integrity=verify_artifact_integrity and not mutable_roles,
                    approval_mode=approval.get("approval_mode"),
                )
            except ResearchCtlError as exc:
                message = (
                    f"approved Gate {label} no longer has a valid current artifact "
                    f"binding: {exc}"
                )
                (warnings if gate in allow_binding_drift_for else errors).append(message)
                continue
            if verify_artifact_integrity and mutable_roles:
                for reference in _refs_for_roles(current_refs, mutable_roles, include=False):
                    integrity_errors = artifact_ref_errors(
                        root,
                        policy,
                        reference,
                        f"approved Gate {label} current artifact",
                        verify_source=True,
                        verify_snapshot=True,
                    )
                    destination = warnings if gate in allow_binding_drift_for else errors
                    destination.extend(
                        f"approved Gate {label} current artifact is not verifiable: {error}"
                        for error in integrity_errors
                    )
            strict_current_refs = _refs_for_roles(current_refs, mutable_roles, include=False)
            strict_approved_refs = _refs_for_roles(approved_refs, mutable_roles, include=False)
            if _comparable_refs(strict_current_refs) != _comparable_refs(strict_approved_refs):
                message = (
                    f"approved Gate {label} current artifacts differ from its latest "
                    "approved artifact_refs; reopen the Gate before changing them"
                )
                (warnings if gate in allow_binding_drift_for else errors).append(message)
            for role in mutable_roles:
                approved_mutable = _refs_for_roles(approved_refs, {role}, include=True)
                current_mutable = _refs_for_roles(current_refs, {role}, include=True)
                if (
                    len(approved_mutable) != 1
                    or len(current_mutable) != 1
                    or approved_mutable[0].get("artifact_id")
                    != current_mutable[0].get("artifact_id")
                ):
                    message = (
                        f"approved Gate {label} mutable role {role} must retain its "
                        "canonical artifact identity"
                    )
                    (warnings if gate in allow_binding_drift_for else errors).append(message)
            if policy.gate_specs[gate].get("selection_artifact_role") is not None:
                selection = approval.get("selection")
                try:
                    expected_ref = selection_artifact_ref(policy, gate, current_refs)
                except ResearchCtlError as exc:
                    errors.append(f"approved Gate {label} selection is invalid: {exc}")
                else:
                    if not isinstance(selection, dict) or selection.get("artifact_ref") != expected_ref:
                        errors.append(
                            f"approved Gate {label} selection no longer binds its portfolio"
                        )

    if verify_artifact_integrity:
        for history_label, reference in iter_gate_artifact_refs(state, policy):
            errors.extend(
                f"historical Gate artifact is not verifiable: {error}"
                for error in artifact_ref_errors(
                    root,
                    policy,
                    reference,
                    history_label,
                    verify_source=False,
                    verify_snapshot=True,
                )
            )

    if isinstance(gates, dict):
        retrospective = retrospective_gate_contract(policy)
        retrospective_gate = retrospective[0] if retrospective is not None else None
        retrospective_mode = retrospective[1] if retrospective is not None else None
        for gate, target, record in iter_gate_records(state, policy):
            history = record.get("history") if isinstance(record, dict) else None
            if not isinstance(history, list):
                continue
            for index, decision in enumerate(history):
                if (
                    gate == retrospective_gate
                    and isinstance(decision, dict)
                    and decision.get("action") == "approve"
                    and decision.get("approval_mode") == retrospective_mode
                ):
                    waived = decision.get("waived_artifact_roles")
                    warnings.append(
                        f"Gate {_gate_label(gate, target)} history[{index}] used "
                        f"{retrospective_mode}; historical artifact roles "
                        "remain unverified: "
                        + (
                            ", ".join(str(role) for role in waived)
                            if isinstance(waived, list)
                            else "<invalid>"
                        )
                    )
