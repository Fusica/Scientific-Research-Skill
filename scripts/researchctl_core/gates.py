"""Gate prerequisites, artifact bindings, decisions, and stage transitions."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import artifact_pointer_errors, is_direct_artifact_pointer
from .constants import ARTIFACT_METADATA_FIELDS, GATE_IDS, Policy, ResearchCtlError
from .policy import split_artifact_role


def command_actor() -> str:
    """Resolve the identity persisted with a Gate decision."""

    return (
        os.environ.get("RESEARCHCTL_ACTOR")
        or os.environ.get("USER")
        or os.environ.get("USERNAME")
        or "unknown"
    )


def decision_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"DEC-{timestamp}-{uuid.uuid4().hex[:8].upper()}"


def required_gates(spec: dict[str, Any]) -> tuple[str, ...]:
    value = spec.get("requires_gates", spec.get("requires", []))
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ResearchCtlError("policy gate prerequisites must be a string list")
    return tuple(value)


def required_artifact_roles_for_gate(
    policy: Policy, gate: str, release_target: str | None
) -> tuple[str, ...]:
    spec = policy.gate_specs[gate]
    if gate == "release":
        mapping = spec.get("required_artifact_roles_by_target")
        if not isinstance(mapping, dict) or release_target not in mapping:
            raise ResearchCtlError(
                f"policy has no artifact roles for release target {release_target!r}"
            )
        roles = mapping[release_target]
    else:
        roles = spec.get("required_artifact_roles")
    if not isinstance(roles, list) or not roles:
        raise ResearchCtlError(f"policy Gate {gate} has no required artifact roles")
    return tuple(roles)


def gate_artifact_refs(
    root: Path,
    state: dict[str, Any],
    policy: Policy,
    gate: str,
    release_target: str | None,
    *,
    verify_integrity: bool = True,
) -> list[dict[str, Any]]:
    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ResearchCtlError("state artifacts must be an object")
    references: list[dict[str, Any]] = []
    failures: list[str] = []
    for role_reference in required_artifact_roles_for_gate(
        policy, gate, release_target
    ):
        stage, role = split_artifact_role(role_reference, policy.stage_order)
        stage_bucket = artifacts.get(stage)
        role_bucket = stage_bucket.get(role) if isinstance(stage_bucket, dict) else None
        if (
            not isinstance(role_bucket, dict)
            or not role_bucket
            or is_direct_artifact_pointer(role_bucket)
        ):
            failures.append(f"missing required artifact role {role_reference}")
            continue
        role_references: list[dict[str, Any]] = []
        for key, pointer in sorted(role_bucket.items()):
            label = f"artifacts.{stage}.{role}.{key}"
            pointer_errors = artifact_pointer_errors(
                root, pointer, label, verify_integrity=verify_integrity
            )
            if pointer_errors:
                failures.extend(pointer_errors)
                continue
            if pointer.get("artifact_id") != key:
                failures.append(
                    f"{label}.artifact_id must match its artifact-ID mapping key"
                )
                continue
            reference = {"label": label}
            reference.update(
                {field: pointer[field] for field in ("path", *ARTIFACT_METADATA_FIELDS)}
            )
            role_references.append(reference)
        if not role_references:
            failures.append(f"required artifact role {role_reference} has no valid file")
        references.extend(role_references)
    if failures:
        raise ResearchCtlError(
            f"Gate {gate} artifact requirements failed: " + "; ".join(failures)
        )
    return references


def transition_requirements(
    policy: Policy, from_stage: str, to_stage: str
) -> tuple[str, ...]:
    transitions = policy.raw.get("allowed_transitions")
    if not isinstance(transitions, dict):
        raise ResearchCtlError("policy allowed_transitions must be an object")
    candidates = transitions.get(from_stage)
    if not isinstance(candidates, list):
        raise ResearchCtlError(
            f"policy has no transition rules from stage {from_stage!r}"
        )
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("to") != to_stage:
            continue
        requirements = candidate.get("required_gates", [])
        if not isinstance(requirements, list) or not all(
            isinstance(gate, str) and gate in GATE_IDS for gate in requirements
        ):
            raise ResearchCtlError(
                f"policy transition {from_stage}->{to_stage} has invalid required_gates"
            )
        return tuple(requirements)
    raise ResearchCtlError(
        f"policy does not allow stage transition {from_stage}->{to_stage}"
    )


def latest_approved_artifact_refs(history: list[Any]) -> list[dict[str, Any]]:
    """Copy the exact refs of the approval being reopened, excluding unrelated state."""

    for decision in reversed(history):
        if not isinstance(decision, dict) or decision.get("action") != "approve":
            continue
        refs = decision.get("artifact_refs")
        if not isinstance(refs, list):
            return []
        return [dict(reference) for reference in refs if isinstance(reference, dict)]
    return []
