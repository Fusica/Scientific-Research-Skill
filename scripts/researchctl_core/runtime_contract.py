"""Load the shared machine schema consumed by every local runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .constants import (
    DEFAULT_RUNTIME_CONTRACT_PATH,
    ResearchCtlError,
    RuntimeContract,
    SUPPORTED_RUNTIME_CONTRACT_VERSION,
)
from .jsonutil import (
    DuplicateJsonKeyError,
    NonStandardJsonConstantError,
    strict_json_loads,
)


def runtime_contract_path() -> Path:
    """Return the runtime contract path, with an override for isolated tests."""

    override = os.environ.get("RESEARCHCTL_RUNTIME_CONTRACT")
    return (
        Path(override).expanduser().resolve()
        if override
        else DEFAULT_RUNTIME_CONTRACT_PATH
    )


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResearchCtlError(f"runtime contract {label} must be an object")
    return value


def _fields(value: Any, label: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        raise ResearchCtlError(
            f"runtime contract {label} must be a non-empty string list"
        )
    if len(value) != len(set(value)):
        raise ResearchCtlError(f"runtime contract {label} contains duplicates")
    return tuple(value)


def _exact_fields(
    section: dict[str, Any], expected: set[str], label: str
) -> None:
    if set(section) != expected:
        raise ResearchCtlError(
            f"runtime contract {label} fields do not match the v2 contract"
        )


def _require_disjoint(
    left: tuple[str, ...],
    right: tuple[str, ...],
    left_label: str,
    right_label: str,
) -> None:
    overlap = set(left) & set(right)
    if overlap:
        raise ResearchCtlError(
            f"runtime contract {left_label} and {right_label} overlap: "
            + ", ".join(sorted(overlap))
        )


def _require_runtime_enum(
    value: tuple[str, ...], expected: set[str], label: str
) -> None:
    """Fail closed where Python branches on fixed v2 machine semantics."""

    if set(value) != expected:
        raise ResearchCtlError(
            f"runtime contract {label} is unsupported by the v2 runtime"
        )


def _require_runtime_capabilities(
    value: tuple[str, ...], required: set[str], label: str
) -> None:
    """Allow optional extensions while preserving fields interpreted by v2 code."""

    missing = required - set(value)
    if missing:
        raise ResearchCtlError(
            f"runtime contract {label} is missing v2 capabilities: "
            + ", ".join(sorted(missing))
        )


def load_runtime_contract() -> RuntimeContract:
    """Load and validate the single fixed state and decision schema."""

    path = runtime_contract_path()
    try:
        raw = strict_json_loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ResearchCtlError(f"runtime contract file not found: {path}") from exc
    except (OSError, UnicodeError) as exc:
        raise ResearchCtlError(f"cannot read runtime contract {path}: {exc}") from exc
    except (DuplicateJsonKeyError, NonStandardJsonConstantError) as exc:
        raise ResearchCtlError(f"runtime contract contains {exc}: {path}") from exc
    except (json.JSONDecodeError, RecursionError) as exc:
        if isinstance(exc, RecursionError):
            raise ResearchCtlError(
                f"runtime contract JSON is nested too deeply: {path}"
            ) from exc
        raise ResearchCtlError(
            f"runtime contract must be JSON: {path}:{exc.lineno}:{exc.colno}: "
            f"{exc.msg}"
        ) from exc

    if not isinstance(raw, dict):
        raise ResearchCtlError("runtime contract root must be an object")
    _exact_fields(
        raw,
        {
            "contract_version",
            "state_schema_version",
            "state",
            "decision",
            "lifecycle",
            "activation",
            "gate",
            "artifact",
            "checkpoint",
            "stage_transition",
        },
        "root",
    )
    contract_version = raw.get("contract_version")
    if contract_version != SUPPORTED_RUNTIME_CONTRACT_VERSION:
        raise ResearchCtlError(
            "unsupported runtime contract_version "
            f"{contract_version!r}; this runtime requires "
            f"{SUPPORTED_RUNTIME_CONTRACT_VERSION!r}"
        )
    state_schema_version = raw.get("state_schema_version")
    if not isinstance(state_schema_version, str) or not state_schema_version.strip():
        raise ResearchCtlError(
            "runtime contract state_schema_version must be a non-empty string"
        )

    state = _object(raw.get("state"), "state")
    decision = _object(raw.get("decision"), "decision")
    lifecycle = _object(raw.get("lifecycle"), "lifecycle")
    activation = _object(raw.get("activation"), "activation")
    gate = _object(raw.get("gate"), "gate")
    artifact = _object(raw.get("artifact"), "artifact")
    checkpoint = _object(raw.get("checkpoint"), "checkpoint")
    transition = _object(raw.get("stage_transition"), "stage_transition")
    _exact_fields(state, {"required_fields"}, "state")
    _exact_fields(decision, {"required_fields"}, "decision")
    _exact_fields(
        lifecycle,
        {
            "statuses",
            "actions",
            "record_fields",
            "decision_fields",
            "decision_optional_fields",
        },
        "lifecycle",
    )
    _exact_fields(activation, {"actions", "event_fields"}, "activation")
    _exact_fields(
        gate,
        {
            "statuses",
            "actions",
            "record_fields",
            "target_container_fields",
            "decision_optional_fields",
            "cascade_fields",
            "gate_ref_required_fields",
            "gate_ref_optional_fields",
            "selection_fields",
        },
        "gate",
    )
    _exact_fields(
        artifact,
        {"entry_fields", "revision_fields", "reference_prefix_fields"},
        "artifact",
    )
    _exact_fields(checkpoint, {"fields"}, "checkpoint")
    _exact_fields(transition, {"fields", "trigger_prefixes"}, "stage_transition")

    values = {
        "state.required_fields": _fields(
            state.get("required_fields"), "state.required_fields"
        ),
        "decision.required_fields": _fields(
            decision.get("required_fields"), "decision.required_fields"
        ),
        "lifecycle.statuses": _fields(
            lifecycle.get("statuses"), "lifecycle.statuses"
        ),
        "lifecycle.actions": _fields(
            lifecycle.get("actions"), "lifecycle.actions"
        ),
        "lifecycle.record_fields": _fields(
            lifecycle.get("record_fields"), "lifecycle.record_fields"
        ),
        "lifecycle.decision_fields": _fields(
            lifecycle.get("decision_fields"), "lifecycle.decision_fields"
        ),
        "lifecycle.decision_optional_fields": _fields(
            lifecycle.get("decision_optional_fields"),
            "lifecycle.decision_optional_fields",
        ),
        "activation.actions": _fields(
            activation.get("actions"), "activation.actions"
        ),
        "activation.event_fields": _fields(
            activation.get("event_fields"), "activation.event_fields"
        ),
        "gate.statuses": _fields(gate.get("statuses"), "gate.statuses"),
        "gate.actions": _fields(gate.get("actions"), "gate.actions"),
        "gate.record_fields": _fields(
            gate.get("record_fields"), "gate.record_fields"
        ),
        "gate.target_container_fields": _fields(
            gate.get("target_container_fields"), "gate.target_container_fields"
        ),
        "gate.decision_optional_fields": _fields(
            gate.get("decision_optional_fields"), "gate.decision_optional_fields"
        ),
        "gate.cascade_fields": _fields(
            gate.get("cascade_fields"), "gate.cascade_fields"
        ),
        "gate.gate_ref_required_fields": _fields(
            gate.get("gate_ref_required_fields"), "gate.gate_ref_required_fields"
        ),
        "gate.gate_ref_optional_fields": _fields(
            gate.get("gate_ref_optional_fields"), "gate.gate_ref_optional_fields"
        ),
        "gate.selection_fields": _fields(
            gate.get("selection_fields"), "gate.selection_fields"
        ),
        "artifact.entry_fields": _fields(
            artifact.get("entry_fields"), "artifact.entry_fields"
        ),
        "artifact.revision_fields": _fields(
            artifact.get("revision_fields"), "artifact.revision_fields"
        ),
        "artifact.reference_prefix_fields": _fields(
            artifact.get("reference_prefix_fields"),
            "artifact.reference_prefix_fields",
        ),
        "checkpoint.fields": _fields(
            checkpoint.get("fields"), "checkpoint.fields"
        ),
        "stage_transition.fields": _fields(
            transition.get("fields"), "stage_transition.fields"
        ),
        "stage_transition.trigger_prefixes": _fields(
            transition.get("trigger_prefixes"),
            "stage_transition.trigger_prefixes",
        ),
    }
    for left_label, right_label in (
        ("decision.required_fields", "lifecycle.decision_fields"),
        ("decision.required_fields", "lifecycle.decision_optional_fields"),
        ("lifecycle.decision_fields", "lifecycle.decision_optional_fields"),
        ("decision.required_fields", "gate.decision_optional_fields"),
        ("gate.record_fields", "gate.target_container_fields"),
        ("gate.gate_ref_required_fields", "gate.gate_ref_optional_fields"),
        ("artifact.reference_prefix_fields", "artifact.revision_fields"),
    ):
        _require_disjoint(
            values[left_label], values[right_label], left_label, right_label
        )
    for label, expected in (
        (
            "state.required_fields",
            {
                "schema_version", "workflow_version", "enabled", "project_id",
                "project_name", "current_stage", "lifecycle", "activation_history",
                "gates", "artifacts", "last_checkpoint", "stage_history",
                "created_at", "updated_at",
            },
        ),
        (
            "decision.required_fields",
            {
                "decision_id", "action", "previous_status", "new_status", "reason",
                "actor", "decided_at", "artifact_refs", "supporting_evidence_ids",
                "opposing_evidence_ids", "unresolved_risks", "decision_conditions",
            },
        ),
        ("lifecycle.record_fields", {"status", "latest_decision_id", "history"}),
        ("lifecycle.decision_fields", {"stage"}),
        (
            "activation.event_fields",
            {
                "action", "previous_enabled", "new_enabled", "reason", "actor",
                "decided_at",
            },
        ),
        ("gate.record_fields", {"status", "latest_decision_id", "history"}),
        ("gate.target_container_fields", {"targets"}),
        (
            "gate.cascade_fields",
            {"upstream_gate_ref", "upstream_decision_id", "upstream_reason"},
        ),
        ("gate.gate_ref_required_fields", {"gate"}),
        ("gate.gate_ref_optional_fields", {"target"}),
        ("gate.selection_fields", {"selected_id", "artifact_ref"}),
        ("artifact.entry_fields", {"current_revision", "revisions"}),
        (
            "artifact.revision_fields",
            {
                "revision", "source_path", "snapshot_path", "content_hash",
                "size_bytes", "registered_at",
            },
        ),
        ("artifact.reference_prefix_fields", {"label", "artifact_id"}),
        ("checkpoint.fields", {"summary", "timestamp"}),
        (
            "stage_transition.fields",
            {"from_stage", "to_stage", "trigger", "timestamp"},
        ),
        ("lifecycle.statuses", {"active", "terminated", "completed"}),
        ("lifecycle.actions", {"terminate", "complete", "reopen"}),
        ("activation.actions", {"enable", "disable"}),
        ("gate.statuses", {"pending", "approved", "reopened"}),
        ("gate.actions", {"approve", "reopen"}),
        (
            "stage_transition.trigger_prefixes",
            {"checkpoint", "gate-approve", "gate-reopen"},
        ),
    ):
        _require_runtime_enum(values[label], expected, label)
    for label, required in (
        (
            "lifecycle.decision_optional_fields",
            {"gate_ref", "gate_decision_id"},
        ),
        (
            "gate.decision_optional_fields",
            {"approval_mode", "waived_artifact_roles", "selection", "cascade"},
        ),
    ):
        _require_runtime_capabilities(values[label], required, label)

    return RuntimeContract(
        contract_version=contract_version,
        state_schema_version=state_schema_version,
        state_required_fields=values["state.required_fields"],
        decision_required_fields=values["decision.required_fields"],
        lifecycle_statuses=values["lifecycle.statuses"],
        lifecycle_actions=values["lifecycle.actions"],
        lifecycle_record_fields=values["lifecycle.record_fields"],
        lifecycle_decision_fields=values["lifecycle.decision_fields"],
        lifecycle_decision_optional_fields=values[
            "lifecycle.decision_optional_fields"
        ],
        activation_actions=values["activation.actions"],
        activation_event_fields=values["activation.event_fields"],
        gate_statuses=values["gate.statuses"],
        gate_actions=values["gate.actions"],
        gate_record_fields=values["gate.record_fields"],
        gate_target_container_fields=values["gate.target_container_fields"],
        gate_decision_optional_fields=values[
            "gate.decision_optional_fields"
        ],
        cascade_fields=values["gate.cascade_fields"],
        gate_ref_required_fields=values["gate.gate_ref_required_fields"],
        gate_ref_optional_fields=values["gate.gate_ref_optional_fields"],
        selection_fields=values["gate.selection_fields"],
        artifact_entry_fields=values["artifact.entry_fields"],
        artifact_revision_fields=values["artifact.revision_fields"],
        artifact_reference_prefix_fields=values[
            "artifact.reference_prefix_fields"
        ],
        checkpoint_fields=values["checkpoint.fields"],
        stage_transition_fields=values["stage_transition.fields"],
        stage_transition_trigger_prefixes=values[
            "stage_transition.trigger_prefixes"
        ],
        raw=raw,
    )
