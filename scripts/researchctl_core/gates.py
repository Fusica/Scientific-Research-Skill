"""Gate prerequisites, canonical artifact bindings, and stage transitions."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import (
    artifact_reference,
    current_artifact_revision,
    verify_revision_files,
)
from .constants import Policy, ResearchCtlError
from .gate_records import approval_targets_for_gate, gate_record, iter_gate_records
from .policy import split_artifact_role


GateRef = tuple[str, str | None]


def command_actor() -> str:
    for variable in ("RESEARCHCTL_ACTOR", "USER", "USERNAME"):
        value = os.environ.get(variable)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def decision_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"DEC-{timestamp}-{uuid.uuid4().hex[:8].upper()}"


def gate_ref_owner_stage(policy: Policy, gate: str, target: str | None) -> str:
    """Return the sole stage whose exit is controlled by an exact Gate reference."""

    matches = [
        stage
        for stage in policy.stage_order
        if (requirement := policy.stage_exit_requirements[stage]) is not None
        and requirement["gate"] == gate
        and requirement.get("target") == target
    ]
    if len(matches) != 1:
        suffix = f"/{target}" if target is not None else ""
        raise ResearchCtlError(
            f"policy Gate reference {gate}{suffix} must own exactly one stage exit"
        )
    return matches[0]


def gate_ref_for_stage_exit(policy: Policy, stage: str) -> GateRef | None:
    requirement = policy.stage_exit_requirements.get(stage)
    if requirement is None:
        return None
    return requirement["gate"], requirement.get("target")


def gate_ref_prerequisites(
    policy: Policy, gate: str, target: str | None
) -> tuple[GateRef, ...]:
    """Return every earlier stage-exit approval required by this Gate reference."""

    reference = (gate, target)
    try:
        index = policy.gate_sequence.index(reference)
    except ValueError as exc:
        suffix = f"/{target}" if target is not None else ""
        raise ResearchCtlError(f"unknown Gate reference {gate}{suffix}") from exc
    return policy.gate_sequence[:index]


def transition_rule(
    policy: Policy, from_stage: str, to_stage: str
) -> dict[str, Any]:
    candidates = policy.stage_transitions.get(from_stage)
    if not isinstance(candidates, list):
        raise ResearchCtlError(
            f"policy has no transition rules from stage {from_stage!r}"
        )
    matches = [candidate for candidate in candidates if candidate.get("to") == to_stage]
    if len(matches) != 1:
        raise ResearchCtlError(
            f"policy does not allow stage transition {from_stage}->{to_stage}"
        )
    return matches[0]


def transition_gate_ref(
    policy: Policy, from_stage: str, to_stage: str
) -> GateRef | None:
    trigger = transition_rule(policy, from_stage, to_stage).get("trigger")
    if not isinstance(trigger, dict):
        raise ResearchCtlError(
            f"policy transition {from_stage}->{to_stage} has no trigger"
        )
    if trigger.get("type") == "checkpoint":
        return None
    if trigger.get("type") != "stage_exit" or trigger.get("stage") not in policy.stage_order:
        raise ResearchCtlError(
            f"policy transition {from_stage}->{to_stage} has an invalid trigger"
        )
    reference = gate_ref_for_stage_exit(policy, trigger["stage"])
    if reference is None:
        raise ResearchCtlError(
            f"policy transition {from_stage}->{to_stage} references no Gate"
        )
    return reference


def gate_approval_destination(
    policy: Policy, current_stage: str, gate: str, target: str | None
) -> str | None:
    """Return the graph destination unlocked here, or None for a terminal exit."""

    reference = (gate, target)
    owner = gate_ref_owner_stage(policy, gate, target)
    for candidate in policy.stage_transitions.get(current_stage, []):
        destination = candidate.get("to")
        if not isinstance(destination, str):
            continue
        if transition_gate_ref(policy, current_stage, destination) == reference:
            return destination
    if current_stage == owner:
        return None
    suffix = f"/{target}" if target is not None else ""
    raise ResearchCtlError(
        f"Gate {gate}{suffix} cannot be approved from current_stage {current_stage!r}"
    )


def release_target_sequence(policy: Policy) -> tuple[str, ...]:
    """Return every policy-ordered target of the external-release Gate."""

    targets = approval_targets_for_gate(policy, policy.release_gate)
    if not targets or targets != policy.release_targets:
        raise ResearchCtlError(
            f"policy external-release Gate {policy.release_gate} has an invalid "
            "target sequence"
        )
    return targets


def release_target_for_approval(
    policy: Policy, record: Any
) -> str:
    """Return the first release target that is not currently approved."""

    targets = release_target_sequence(policy)
    records = record.get("targets") if isinstance(record, dict) else None
    for target in targets:
        target_record = records.get(target) if isinstance(records, dict) else None
        if not isinstance(target_record, dict) or target_record.get("status") != "approved":
            return target
    return targets[-1]


def release_stage_for_target(policy: Policy, release_target: str) -> str:
    """Return the stage that owns one release target's exit."""

    return gate_ref_owner_stage(policy, policy.release_gate, release_target)


def release_allowed_stages_for_target(
    policy: Policy, release_target: str
) -> tuple[str, ...]:
    """Return policy-derived stages from which a release round may be approved."""

    owner = release_stage_for_target(policy, release_target)
    allowed = [owner]
    reference = (policy.release_gate, release_target)
    for source in policy.stage_order:
        for candidate in policy.stage_transitions[source]:
            destination = candidate.get("to")
            if (
                isinstance(destination, str)
                and transition_gate_ref(policy, source, destination) == reference
            ):
                allowed.append(source)
    return tuple(dict.fromkeys(allowed))


def artifact_role_contract_for_gate(
    policy: Policy,
    gate: str,
    release_target: str | None,
    approval_mode: str | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return required and optional roles for one exact approval mode."""

    spec = policy.gate_specs[gate]
    optional_roles: Any = []
    target_specs = spec.get("approval_targets")
    mode_specs = spec.get("approval_modes")
    if isinstance(target_specs, dict):
        if approval_mode is not None:
            raise ResearchCtlError(
                f"policy targeted Gate {gate} does not allow approval modes"
            )
        target_spec = target_specs.get(release_target)
        if not isinstance(target_spec, dict):
            raise ResearchCtlError(
                f"policy has no artifact roles for Gate target {gate}/{release_target}"
            )
        roles = target_spec.get("required_artifact_roles")
    elif isinstance(mode_specs, dict):
        mode = approval_mode or spec.get("default_approval_mode")
        mode_spec = mode_specs.get(mode)
        if not isinstance(mode_spec, dict):
            raise ResearchCtlError(
                f"policy Gate {gate} does not allow approval mode {mode!r}"
            )
        roles = mode_spec.get("required_artifact_roles")
        optional_roles = mode_spec.get("waivable_historical_roles", [])
    else:
        if release_target is not None:
            raise ResearchCtlError(
                f"policy untargeted Gate {gate} cannot use target {release_target!r}"
            )
        roles = spec.get("required_artifact_roles")
    if not isinstance(roles, list) or not roles:
        raise ResearchCtlError(f"policy Gate {gate} has no required artifact roles")
    if not isinstance(optional_roles, list) or not all(
        isinstance(role, str) for role in optional_roles
    ):
        raise ResearchCtlError(f"policy Gate {gate} has invalid optional artifact roles")
    return tuple(roles), tuple(optional_roles)


def required_artifact_roles_for_gate(
    policy: Policy,
    gate: str,
    release_target: str | None,
    approval_mode: str | None = None,
) -> tuple[str, ...]:
    required, _optional = artifact_role_contract_for_gate(
        policy, gate, release_target, approval_mode
    )
    return required


def waived_artifact_roles_for_refs(
    policy: Policy,
    gate: str,
    release_target: str | None,
    approval_mode: str,
    references: list[dict[str, Any]],
) -> list[str]:
    _required, optional = artifact_role_contract_for_gate(
        policy, gate, release_target, approval_mode
    )
    present_roles: set[str] = set()
    for reference in references:
        label = reference.get("label")
        if not isinstance(label, str):
            continue
        parts = label.split(".", 3)
        if len(parts) == 4 and parts[0] == "artifacts":
            present_roles.add(f"{parts[1]}.{parts[2]}")
    return [role for role in optional if role not in present_roles]


def gate_artifact_refs(
    root: Path,
    state: dict[str, Any],
    policy: Policy,
    gate: str,
    release_target: str | None,
    *,
    verify_integrity: bool = True,
    approval_mode: str | None = None,
) -> list[dict[str, Any]]:
    """Bind exactly one canonical current artifact for every applicable role."""

    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ResearchCtlError("state artifacts must be a v2 object")
    references: list[dict[str, Any]] = []
    failures: list[str] = []
    required_roles, optional_roles = artifact_role_contract_for_gate(
        policy, gate, release_target, approval_mode
    )
    optional_role_set = set(optional_roles)
    for role_reference in (*required_roles, *optional_roles):
        stage, role = split_artifact_role(role_reference, policy.stage_order)
        stage_bucket = artifacts.get(stage)
        role_bucket = stage_bucket.get(role) if isinstance(stage_bucket, dict) else None
        if not isinstance(role_bucket, dict) or not role_bucket:
            if role_reference not in optional_role_set:
                failures.append(f"missing required artifact role {role_reference}")
            continue
        if len(role_bucket) != 1:
            failures.append(
                f"artifact role {role_reference} must contain exactly one canonical "
                f"artifact, found {len(role_bucket)}"
            )
            continue
        artifact_id, entry = next(iter(role_bucket.items()))
        revision = current_artifact_revision(entry)
        label = f"artifacts.{stage}.{role}.{artifact_id}"
        if not isinstance(artifact_id, str) or revision is None:
            failures.append(f"{label} has no valid current revision")
            continue
        revision_errors = verify_revision_files(
            root,
            policy,
            revision,
            label,
            verify_source=verify_integrity,
            verify_snapshot=verify_integrity,
        )
        if revision_errors:
            failures.extend(revision_errors)
            continue
        references.append(artifact_reference(policy, label, artifact_id, revision))
    if failures:
        raise ResearchCtlError(
            f"Gate {gate} artifact requirements failed: " + "; ".join(failures)
        )
    return references


def selection_artifact_ref(
    policy: Policy, gate: str, references: list[dict[str, Any]]
) -> dict[str, Any] | None:
    selection_role = policy.gate_specs[gate].get("selection_artifact_role")
    if selection_role is None:
        return None
    if not isinstance(selection_role, str):
        raise ResearchCtlError(
            f"policy Gate {gate} has invalid selection_artifact_role"
        )
    stage, role = split_artifact_role(selection_role, policy.stage_order)
    prefix = f"artifacts.{stage}.{role}."
    matching = [
        reference
        for reference in references
        if isinstance(reference.get("label"), str)
        and reference["label"].startswith(prefix)
    ]
    if len(matching) != 1:
        raise ResearchCtlError(
            f"Gate {gate} selection role {selection_role} must bind exactly one portfolio"
        )
    return dict(matching[0])


def transition_requirements(
    policy: Policy, from_stage: str, to_stage: str
) -> tuple[GateRef, ...]:
    """Compatibility helper returning the exact GateRef required by a transition."""

    reference = transition_gate_ref(policy, from_stage, to_stage)
    return () if reference is None else (reference,)


def latest_approved_artifact_refs(history: list[Any]) -> list[dict[str, Any]]:
    for decision in reversed(history):
        if not isinstance(decision, dict) or decision.get("action") != "approve":
            continue
        refs = decision.get("artifact_refs")
        if not isinstance(refs, list):
            return []
        return [dict(reference) for reference in refs if isinstance(reference, dict)]
    return []
