"""Load the shared machine schema consumed by every local runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .constants import (
    ARTIFACT_ROLE_RE,
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


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchCtlError(
            f"runtime contract {label} must be a non-empty string"
        )
    return value


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


def _relation_signatures(
    value: Any,
    *,
    relation_kinds: tuple[str, ...],
    record_kinds: tuple[str, ...],
) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    signatures = _object(value, "scientific_record.relation_signatures")
    if set(signatures) != set(relation_kinds):
        raise ResearchCtlError(
            "runtime contract scientific_record.relation_signatures must define "
            "every relation kind exactly once"
        )
    known_record_kinds = set(record_kinds)
    materialized: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for relation_kind in relation_kinds:
        label = f"scientific_record.relation_signatures.{relation_kind}"
        signature = _object(signatures.get(relation_kind), label)
        _exact_fields(signature, {"source_kinds", "target_kinds"}, label)
        source_kinds = _fields(signature.get("source_kinds"), f"{label}.source_kinds")
        target_kinds = _fields(signature.get("target_kinds"), f"{label}.target_kinds")
        unknown = (set(source_kinds) | set(target_kinds)) - known_record_kinds
        if unknown:
            raise ResearchCtlError(
                f"runtime contract {label} uses unknown record kinds: "
                + ", ".join(sorted(unknown))
            )
        materialized[relation_kind] = (source_kinds, target_kinds)
    return materialized


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
            "scientific_record",
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
    scientific_record = _object(
        raw.get("scientific_record"), "scientific_record"
    )
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
    _exact_fields(
        scientific_record,
        {
            "manifest_schema_version",
            "artifact_role",
            "manifest_fields",
            "record_fields",
            "source_fields",
            "relation_fields",
            "record_kinds",
            "relation_kinds",
            "relation_signatures",
        },
        "scientific_record",
    )

    scientific_record_manifest_schema_version = _nonempty_string(
        scientific_record.get("manifest_schema_version"),
        "scientific_record.manifest_schema_version",
    )
    scientific_record_artifact_role = _nonempty_string(
        scientific_record.get("artifact_role"),
        "scientific_record.artifact_role",
    )
    if not ARTIFACT_ROLE_RE.fullmatch(scientific_record_artifact_role):
        raise ResearchCtlError(
            "runtime contract scientific_record.artifact_role must use "
            "lower_snake_case"
        )

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
        "scientific_record.manifest_fields": _fields(
            scientific_record.get("manifest_fields"),
            "scientific_record.manifest_fields",
        ),
        "scientific_record.record_fields": _fields(
            scientific_record.get("record_fields"),
            "scientific_record.record_fields",
        ),
        "scientific_record.source_fields": _fields(
            scientific_record.get("source_fields"),
            "scientific_record.source_fields",
        ),
        "scientific_record.relation_fields": _fields(
            scientific_record.get("relation_fields"),
            "scientific_record.relation_fields",
        ),
        "scientific_record.record_kinds": _fields(
            scientific_record.get("record_kinds"),
            "scientific_record.record_kinds",
        ),
        "scientific_record.relation_kinds": _fields(
            scientific_record.get("relation_kinds"),
            "scientific_record.relation_kinds",
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
        (
            "scientific_record.manifest_fields",
            {"schema_version", "stage", "records"},
        ),
        (
            "scientific_record.record_fields",
            {"record_id", "record_kind", "source", "supersedes", "relations"},
        ),
        (
            "scientific_record.source_fields",
            {"artifact_role", "artifact_id", "revision", "locator"},
        ),
        (
            "scientific_record.relation_fields",
            {"relation", "target_id"},
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
        (
            "scientific_record.record_kinds",
            {
                "candidate",
                "search_run",
                "passage_evidence",
                "experiment",
                "attempt",
                "analysis",
                "claim",
                "paper_location",
                "review_concern",
            },
        ),
        (
            "scientific_record.relation_kinds",
            {
                "derived_from",
                "discovered_by",
                "supports",
                "contradicts",
                "qualifies",
                "tests",
                "attempt_of",
                "analyzes",
                "expresses",
                "addresses",
            },
        ),
    ):
        _require_runtime_capabilities(values[label], required, label)

    scientific_record_relation_signatures = _relation_signatures(
        scientific_record.get("relation_signatures"),
        relation_kinds=values["scientific_record.relation_kinds"],
        record_kinds=values["scientific_record.record_kinds"],
    )

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
        scientific_record_manifest_schema_version=(
            scientific_record_manifest_schema_version
        ),
        scientific_record_artifact_role=scientific_record_artifact_role,
        scientific_record_manifest_fields=values[
            "scientific_record.manifest_fields"
        ],
        scientific_record_fields=values["scientific_record.record_fields"],
        scientific_record_source_fields=values[
            "scientific_record.source_fields"
        ],
        scientific_record_relation_fields=values[
            "scientific_record.relation_fields"
        ],
        scientific_record_kinds=values["scientific_record.record_kinds"],
        scientific_record_relation_kinds=values[
            "scientific_record.relation_kinds"
        ],
        scientific_record_relation_signatures=(
            scientific_record_relation_signatures
        ),
        raw=raw,
    )
