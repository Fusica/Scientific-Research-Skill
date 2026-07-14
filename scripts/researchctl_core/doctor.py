"""Aggregate independent mechanical validators for research state."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifacts import validate_artifact_identities, validate_artifact_registry
from .constants import Policy
from .gate_validation import validate_gate_bindings, validate_gate_records
from .state_validation import validate_state_envelope, validate_state_timeline
from .workspace_validation import validate_workspace


def validate_state(
    root: Path,
    state: dict[str, Any],
    policy: Policy,
    *,
    verify_artifact_integrity: bool = True,
    allow_binding_drift_for: frozenset[str] = frozenset(),
) -> tuple[list[str], list[str]]:
    """Validate state in stable diagnostic order without mutating it."""

    errors: list[str] = []
    warnings: list[str] = []

    created_at, updated_at = validate_state_envelope(state, policy, errors)
    gates, gate_decisions_by_id = validate_gate_records(
        root,
        state,
        policy,
        errors,
    )

    artifacts = state.get("artifacts")
    if not isinstance(artifacts, (dict, list)):
        errors.append("artifacts must be an object or list")
    else:
        validate_artifact_registry(
            root,
            artifacts,
            policy,
            errors,
            warnings,
            verify_integrity=verify_artifact_integrity,
        )
        validate_artifact_identities(
            state,
            policy,
            errors,
            warnings,
            allow_binding_drift_for=allow_binding_drift_for,
        )

    validate_gate_bindings(
        root,
        state,
        policy,
        gates,
        errors,
        warnings,
        verify_artifact_integrity=verify_artifact_integrity,
        allow_binding_drift_for=allow_binding_drift_for,
    )
    validate_state_timeline(
        state,
        policy,
        gates,
        gate_decisions_by_id,
        created_at,
        updated_at,
        errors,
    )
    validate_workspace(root, policy, errors, warnings)
    return errors, warnings
