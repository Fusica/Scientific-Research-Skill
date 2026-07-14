"""Load and mechanically validate the single workflow policy."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from .constants import (
    ARTIFACT_METADATA_FIELDS,
    ARTIFACT_ROLE_RE,
    DEFAULT_POLICY_PATH,
    GATE_IDS,
    Policy,
    RESEARCH_DIR,
    ResearchCtlError,
    STATE_FIELD_ORDER,
)


def policy_path() -> Path:
    """Return the canonical policy path, with an override for isolated tests."""

    override = os.environ.get("RESEARCHCTL_POLICY")
    return Path(override).expanduser().resolve() if override else DEFAULT_POLICY_PATH

def split_artifact_role(reference: str, stages: Iterable[str]) -> tuple[str, str]:
    """Parse a policy artifact role such as ``idea.idea_card``."""

    stage, separator, role = reference.partition(".")
    if (
        not separator
        or stage not in set(stages)
        or not ARTIFACT_ROLE_RE.fullmatch(role)
    ):
        raise ResearchCtlError(
            f"invalid artifact role {reference!r}; expected <stage>.<lower_snake_role>"
        )
    return stage, role

def validate_required_artifact_roles(
    gate: str, spec: dict[str, Any], stage_order: list[str]
) -> None:
    """Validate the compact Gate-to-artifact role mapping in policy."""

    if gate == "release":
        targets = spec.get("release_targets")
        mapping = spec.get("required_artifact_roles_by_target")
        if not isinstance(targets, list) or not all(
            isinstance(target, str) and target for target in targets
        ):
            raise ResearchCtlError("policy release_targets must be a string list")
        if not isinstance(mapping, dict) or set(mapping) != set(targets):
            raise ResearchCtlError(
                "policy release required_artifact_roles_by_target must define every release target"
            )
        role_lists = mapping.values()
    else:
        roles = spec.get("required_artifact_roles")
        if not isinstance(roles, list) or not roles:
            raise ResearchCtlError(
                f"policy gate {gate} required_artifact_roles must be a non-empty list"
            )
        role_lists = (roles,)

    for roles in role_lists:
        if not isinstance(roles, list) or not roles or not all(
            isinstance(role, str) and role for role in roles
        ):
            raise ResearchCtlError(
                f"policy gate {gate} artifact roles must be non-empty string lists"
            )
        if len(roles) != len(set(roles)):
            raise ResearchCtlError(f"policy gate {gate} artifact roles contain duplicates")
        for role in roles:
            split_artifact_role(role, stage_order)

def load_policy() -> Policy:
    path = policy_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ResearchCtlError(f"policy file not found: {path}") from exc
    except (OSError, UnicodeError) as exc:
        raise ResearchCtlError(f"cannot read policy file {path}: {exc}") from exc
    except (json.JSONDecodeError, RecursionError) as exc:
        if isinstance(exc, RecursionError):
            raise ResearchCtlError(
                f"policy JSON is nested too deeply to validate: {path}"
            ) from exc
        raise ResearchCtlError(
            f"policy must be JSON-compatible YAML: {path}:{exc.lineno}:{exc.colno}: "
            f"{exc.msg}"
        ) from exc

    if not isinstance(raw, dict):
        raise ResearchCtlError("policy root must be an object")
    schema_version = raw.get("schema_version")
    workflow_version = raw.get("workflow_version")
    stage_order = raw.get("stage_order")
    gate_order = raw.get("gate_order")
    gate_specs = raw.get("gates")
    artifact_layout = raw.get("artifact_layout")

    if schema_version is None:
        raise ResearchCtlError("policy is missing schema_version")
    if not isinstance(workflow_version, str) or not workflow_version.strip():
        raise ResearchCtlError("policy workflow_version must be a non-empty string")
    if (
        not isinstance(stage_order, list)
        or not stage_order
        or not all(isinstance(stage, str) and stage for stage in stage_order)
    ):
        raise ResearchCtlError("policy stage_order must be a non-empty string list")
    if len(stage_order) != len(set(stage_order)):
        raise ResearchCtlError("policy stage_order contains duplicates")
    if not isinstance(gate_order, list) or not all(
        isinstance(gate, str) and gate for gate in gate_order
    ):
        raise ResearchCtlError("policy gate_order must be a string list")
    if tuple(gate_order) != GATE_IDS:
        raise ResearchCtlError(
            "policy gate_order must be exactly: " + ", ".join(GATE_IDS)
        )
    if not isinstance(gate_specs, dict):
        raise ResearchCtlError("policy gates must be an object")
    if set(gate_specs) != set(GATE_IDS):
        raise ResearchCtlError("policy gates must define exactly the fixed Gate IDs")
    if not isinstance(artifact_layout, dict):
        raise ResearchCtlError("policy artifact_layout must be an object")
    generated_root = artifact_layout.get("generated_root")
    stage_path_template = artifact_layout.get("stage_path_template")
    layout_instruction = artifact_layout.get("instruction")
    if not isinstance(generated_root, str) or not generated_root.strip():
        raise ResearchCtlError("policy artifact_layout.generated_root must be a path")
    artifact_root = Path(generated_root)
    if (
        artifact_root.is_absolute()
        or not artifact_root.parts
        or artifact_root.parts[0] != RESEARCH_DIR
        or ".." in artifact_root.parts
    ):
        raise ResearchCtlError(
            "policy artifact_layout.generated_root must stay under .research"
        )
    expected_template = f"{artifact_root.as_posix()}/<stage-id>"
    if stage_path_template != expected_template:
        raise ResearchCtlError(
            "policy artifact_layout.stage_path_template must be "
            f"{expected_template!r}"
        )
    if (
        not isinstance(layout_instruction, str)
        or not layout_instruction.strip()
        or expected_template not in layout_instruction
    ):
        raise ResearchCtlError(
            "policy artifact_layout.instruction must state the stage path template"
        )
    state_contract = raw.get("state_contract")
    if not isinstance(state_contract, dict):
        raise ResearchCtlError("policy state_contract must be an object")
    contract_expectations = {
        "required_fields": list(STATE_FIELD_ORDER),
        "stage_ids": stage_order,
        "gate_ids": list(GATE_IDS),
        "gate_statuses": ["pending", "approved", "reopened"],
        "gate_actions": ["approve", "reopen"],
    }
    for field, expected in contract_expectations.items():
        if state_contract.get(field) != expected:
            raise ResearchCtlError(
                f"policy state_contract.{field} must be exactly: "
                + ", ".join(expected)
            )
    pointer_fields = state_contract.get("artifact_pointer_fields")
    expected_pointer_fields = ["path", *ARTIFACT_METADATA_FIELDS]
    if pointer_fields != expected_pointer_fields:
        raise ResearchCtlError(
            "policy state_contract.artifact_pointer_fields must be exactly: "
            + ", ".join(expected_pointer_fields)
        )
    normalized_specs: dict[str, dict[str, Any]] = {}
    for gate in GATE_IDS:
        spec = gate_specs[gate]
        if not isinstance(spec, dict):
            raise ResearchCtlError(f"policy gate {gate} must be an object")
        advance_to = spec.get("advance_to")
        if advance_to is not None and advance_to not in stage_order:
            raise ResearchCtlError(
                f"policy gate {gate} has unknown advance_to stage: {advance_to}"
            )
        reopen_to = spec.get("reopen_to")
        if gate != "release" and reopen_to not in stage_order:
            raise ResearchCtlError(
                f"policy gate {gate} must define a valid reopen_to stage"
            )
        if gate == "release" and reopen_to is not None:
            raise ResearchCtlError(
                "policy release Gate uses target-specific reopen stages, not reopen_to"
            )
        validate_required_artifact_roles(gate, spec, stage_order)
        normalized_specs[gate] = spec

    return Policy(
        schema_version=schema_version,
        workflow_version=workflow_version,
        stage_order=tuple(stage_order),
        gate_order=tuple(gate_order),
        gate_specs=normalized_specs,
        artifact_root=artifact_root,
        raw=raw,
    )
