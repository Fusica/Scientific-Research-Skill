"""Load and structurally validate the single workflow policy."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

from .constants import (
    ARTIFACT_ROLE_RE,
    DEFAULT_POLICY_PATH,
    Policy,
    RESEARCH_DIR,
    ResearchCtlError,
)
from .jsonutil import (
    DuplicateJsonKeyError,
    NonStandardJsonConstantError,
    strict_json_loads,
)
from .runtime_contract import load_runtime_contract


POLICY_ROOT_FIELDS = {
    "schema_version",
    "workflow_version",
    "workflow_graph",
    "artifact_role_cardinality_default",
    "artifact_layout",
    "review_language",
    "workspace_lifecycle",
    "authority_boundary",
    "gates",
    "stages",
    "global_prohibited_actions",
    "semantic_audit",
}
WORKSPACE_LIFECYCLE_FIELDS = {
    "scope",
    "mainline_identity",
    "decision_review",
    "termination",
    "completion",
    "terminal_access",
    "reopen",
    "inactivity",
    "activation",
    "cross_workspace_reuse",
}
RETROSPECTIVE_MODE_MARKERS = {
    "cli_flag",
    "eligibility",
    "claim_scope",
    "waivable_historical_roles",
}
GATE_CLI_RESERVED_FLAGS = frozenset(
    {
        "--help",
        "--reason",
        "--supporting-evidence-id",
        "--opposing-evidence-id",
        "--unresolved-risk",
        "--decision-condition",
        "--target",
        "--selected-id",
        "--approval-mode",
    }
)


def policy_path() -> Path:
    """Return the canonical policy path, with an override for isolated tests."""

    override = os.environ.get("RESEARCHCTL_POLICY")
    return Path(override).expanduser().resolve() if override else DEFAULT_POLICY_PATH


def policy_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResearchCtlError(f"policy {label} must be an object")
    return value


def _string_list(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        qualifier = "a" if allow_empty else "a non-empty"
        raise ResearchCtlError(f"policy {label} must be {qualifier} string list")
    if len(value) != len(set(value)):
        raise ResearchCtlError(f"policy {label} contains duplicates")
    return value


def _non_empty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchCtlError(f"policy {label} must be a non-empty string")
    return value


def _exact_fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ResearchCtlError(
            f"policy {label} fields must be exactly: " + ", ".join(sorted(expected))
        )


def _project_local_root(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ResearchCtlError(f"policy {label} must be a path")
    path = Path(value)
    if (
        path.is_absolute()
        or not path.parts
        or path.parts[0] != RESEARCH_DIR
        or ".." in path.parts
    ):
        raise ResearchCtlError(f"policy {label} must stay under {RESEARCH_DIR}")
    return path


def _validate_semantic_governance(raw: dict[str, Any]) -> None:
    """Reject ignored or malformed policy prose that carries workflow authority."""

    _exact_fields(raw, POLICY_ROOT_FIELDS, "root")
    review_language = policy_object(raw.get("review_language"), "review_language")
    _exact_fields(
        review_language,
        {"internal_review_default", "formal_output_default", "instruction"},
        "review_language",
    )
    for field, value in review_language.items():
        _non_empty_string(value, f"review_language.{field}")

    lifecycle = policy_object(raw.get("workspace_lifecycle"), "workspace_lifecycle")
    _exact_fields(lifecycle, WORKSPACE_LIFECYCLE_FIELDS, "workspace_lifecycle")
    for field, value in lifecycle.items():
        _non_empty_string(value, f"workspace_lifecycle.{field}")

    _string_list(raw.get("authority_boundary"), "authority_boundary")
    _string_list(
        raw.get("global_prohibited_actions"), "global_prohibited_actions"
    )
    _string_list(raw.get("semantic_audit"), "semantic_audit")


def _validate_stage_semantics(
    stages: dict[str, Any], stage_order: list[str]
) -> None:
    expected_fields = {
        "label",
        "reference",
        "required_inputs",
        "allowed_actions",
        "required_evidence",
        "exit_criteria",
        "prohibited_actions",
    }
    references: list[str] = []
    for stage in stage_order:
        spec = policy_object(stages[stage], f"stage {stage}")
        _exact_fields(spec, expected_fields, f"stage {stage}")
        _non_empty_string(spec.get("label"), f"stage {stage}.label")
        reference = _non_empty_string(
            spec.get("reference"), f"stage {stage}.reference"
        )
        if re.fullmatch(r"\d{2}-[a-z0-9-]+\.md", reference) is None:
            raise ResearchCtlError(
                f"policy stage {stage}.reference must name one numbered Markdown file"
            )
        references.append(reference)
        for field in expected_fields - {"label", "reference"}:
            _string_list(spec.get(field), f"stage {stage}.{field}")
    if len(references) != len(set(references)):
        raise ResearchCtlError("policy stage references must be unique")


def _validate_gate_semantics(gate: str, spec: dict[str, Any]) -> None:
    """Validate Gate prose and contract variants without duplicating content."""

    if ARTIFACT_ROLE_RE.fullmatch(gate) is None:
        raise ResearchCtlError(f"policy Gate ID {gate!r} must be lower_snake_case")
    _non_empty_string(spec.get("label"), f"Gate {gate}.label")
    _string_list(
        spec.get("reopen_when_changed"), f"Gate {gate}.reopen_when_changed"
    )
    targets = spec.get("approval_targets")
    modes = spec.get("approval_modes")
    base_fields = {"label", "reopen_when_changed"}
    if targets is not None:
        _exact_fields(
            spec,
            base_fields | {"approval_targets", "approval_requires"},
            f"Gate {gate}",
        )
        _string_list(
            spec.get("approval_requires"), f"Gate {gate}.approval_requires"
        )
        target_specs = policy_object(targets, f"Gate {gate}.approval_targets")
        for target, value in target_specs.items():
            if not isinstance(target, str) or ARTIFACT_ROLE_RE.fullmatch(target) is None:
                raise ResearchCtlError(
                    f"policy Gate {gate} target IDs must be lower_snake_case"
                )
            contract = policy_object(value, f"Gate {gate} target {target}")
            allowed = {"required_artifact_roles", "mutable_after_approval_roles"}
            unknown = set(contract) - allowed
            if unknown:
                raise ResearchCtlError(
                    f"policy Gate {gate} target {target} has unknown fields: "
                    + ", ".join(sorted(unknown))
                )
    elif modes is not None:
        _exact_fields(
            spec,
            base_fields | {"approval_modes", "default_approval_mode"},
            f"Gate {gate}",
        )
        mode_specs = policy_object(modes, f"Gate {gate}.approval_modes")
        common = {
            "required_artifact_roles",
            "mutable_after_approval_roles",
            "approval_requires",
        }
        for mode, value in mode_specs.items():
            if not isinstance(mode, str) or ARTIFACT_ROLE_RE.fullmatch(mode) is None:
                raise ResearchCtlError(
                    f"policy Gate {gate} approval mode IDs must be lower_snake_case"
                )
            contract = policy_object(value, f"Gate {gate} approval mode {mode}")
            special = bool(RETROSPECTIVE_MODE_MARKERS & set(contract))
            allowed = common | (RETROSPECTIVE_MODE_MARKERS if special else set())
            unknown = set(contract) - allowed
            missing = {"required_artifact_roles", "approval_requires"} - set(contract)
            if special:
                missing |= RETROSPECTIVE_MODE_MARKERS - set(contract)
            if unknown or missing:
                details = []
                if missing:
                    details.append("missing " + ", ".join(sorted(missing)))
                if unknown:
                    details.append("unknown " + ", ".join(sorted(unknown)))
                raise ResearchCtlError(
                    f"policy Gate {gate} approval mode {mode} fields are invalid: "
                    + "; ".join(details)
                )
            _string_list(
                contract.get("approval_requires"),
                f"Gate {gate} approval mode {mode}.approval_requires",
            )
    else:
        expected = base_fields | {"required_artifact_roles", "approval_requires"}
        if "selection_artifact_role" in spec:
            expected.add("selection_artifact_role")
        _exact_fields(spec, expected, f"Gate {gate}")
        _string_list(
            spec.get("approval_requires"), f"Gate {gate}.approval_requires"
        )


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
    """Validate Gate role contracts without duplicating their scientific content."""

    targets = spec.get("approval_targets")
    modes = spec.get("approval_modes")
    if targets is not None and modes is not None:
        raise ResearchCtlError(
            f"policy Gate {gate} cannot define both approval_targets and approval_modes"
        )
    contracts: list[dict[str, Any]]
    if targets is not None:
        target_specs = policy_object(targets, f"Gate {gate}.approval_targets")
        if not target_specs:
            raise ResearchCtlError(
                f"policy Gate {gate}.approval_targets must not be empty"
            )
        contracts = [
            policy_object(value, f"Gate {gate} target {target}")
            for target, value in target_specs.items()
        ]
    elif modes is not None:
        mode_specs = policy_object(modes, f"Gate {gate}.approval_modes")
        if not mode_specs:
            raise ResearchCtlError(
                f"policy Gate {gate}.approval_modes must not be empty"
            )
        default_mode = spec.get("default_approval_mode")
        if not isinstance(default_mode, str) or default_mode not in mode_specs:
            raise ResearchCtlError(
                f"policy Gate {gate} default_approval_mode must name one approval mode"
            )
        contracts = [
            policy_object(value, f"Gate {gate} mode {mode}")
            for mode, value in mode_specs.items()
        ]
    else:
        contracts = [spec]

    for contract in contracts:
        roles_value = contract.get("required_artifact_roles")
        roles = _string_list(roles_value, f"Gate {gate} artifact roles")
        for role in roles:
            split_artifact_role(role, stage_order)
        mutable = _string_list(
            contract.get("mutable_after_approval_roles", []),
            f"Gate {gate} mutable_after_approval_roles",
            allow_empty=True,
        )
        if not set(mutable) <= set(roles):
            raise ResearchCtlError(
                f"policy Gate {gate} mutable roles must be required artifact roles"
            )
        for role in mutable:
            split_artifact_role(role, stage_order)

    selection_role = spec.get("selection_artifact_role")
    if selection_role is not None:
        if not isinstance(selection_role, str):
            raise ResearchCtlError(
                f"policy Gate {gate} selection_artifact_role must be a string"
            )
        split_artifact_role(selection_role, stage_order)
        if targets is not None or modes is not None:
            raise ResearchCtlError(
                f"policy Gate {gate} cannot combine selection with targets or modes"
            )
        required_roles = spec.get("required_artifact_roles")
        if not isinstance(required_roles, list) or selection_role not in required_roles:
            raise ResearchCtlError(
                f"policy Gate {gate} selection_artifact_role must also be required"
            )


def validate_retrospective_revision_import(
    gate: str, mode: str, exception_value: Any, stage_order: Iterable[str]
) -> None:
    """Validate one policy-named legacy-work evidence exception safely."""

    exception = policy_object(exception_value, f"{gate}.approval_modes.{mode}")
    for field in ("cli_flag", "eligibility", "claim_scope"):
        if not isinstance(exception.get(field), str) or not exception[field].strip():
            raise ResearchCtlError(
                f"policy {gate} approval mode {mode}.{field} "
                "must be a non-empty string"
            )
    if not re.fullmatch(r"--[a-z][a-z0-9-]*", exception["cli_flag"]):
        raise ResearchCtlError(
            f"policy {gate} approval mode {mode}.cli_flag must be a "
            "long kebab-case option"
        )
    if exception["cli_flag"] in GATE_CLI_RESERVED_FLAGS:
        raise ResearchCtlError(
            f"policy {gate} approval mode {mode}.cli_flag conflicts with the "
            f"built-in Gate option {exception['cli_flag']}"
        )
    required = _string_list(
        exception.get("required_artifact_roles"),
        f"{gate} retrospective required_artifact_roles",
    )
    waivable = _string_list(
        exception.get("waivable_historical_roles"),
        f"{gate} retrospective waivable_historical_roles",
        allow_empty=True,
    )
    mutable = _string_list(
        exception.get("mutable_after_approval_roles", []),
        f"{gate} retrospective mutable_after_approval_roles",
        allow_empty=True,
    )
    if set(required) & set(waivable):
        raise ResearchCtlError(
            "policy retrospective required and waivable artifact roles must be disjoint"
        )
    if not set(mutable) <= set(required):
        raise ResearchCtlError(
            "policy retrospective mutable_after_approval_roles must be a subset "
            "of required_artifact_roles"
        )
    stages = tuple(stage_order)
    for role in (*required, *waivable, *mutable):
        split_artifact_role(role, stages)


def mutable_after_approval_roles(
    policy: Policy,
    gate: str,
    target: str | None,
    approval_mode: Any,
) -> tuple[str, ...]:
    """Return policy-scoped roles whose approval authority is snapshot-only later."""

    spec = policy.gate_specs.get(gate)
    modes = spec.get("approval_modes") if isinstance(spec, dict) else None
    targets = spec.get("approval_targets") if isinstance(spec, dict) else None
    contract = (
        modes.get(approval_mode)
        if isinstance(modes, dict)
        else targets.get(target)
        if isinstance(targets, dict)
        else None
    )
    if not isinstance(contract, dict):
        return ()
    roles = contract.get("mutable_after_approval_roles", [])
    if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
        raise ResearchCtlError(
            f"policy Gate {gate} has invalid mutable_after_approval_roles"
        )
    return tuple(roles)


def retrospective_gate_contract(
    policy: Policy,
) -> tuple[str, str, dict[str, Any]] | None:
    """Return the sole structurally marked retrospective approval mode."""

    matches: list[tuple[str, str, dict[str, Any]]] = []
    for gate, spec in policy.gate_specs.items():
        modes = spec.get("approval_modes")
        if not isinstance(modes, dict):
            continue
        for mode, mode_spec in modes.items():
            if (
                isinstance(mode, str)
                and isinstance(mode_spec, dict)
                and RETROSPECTIVE_MODE_MARKERS <= set(mode_spec)
            ):
                matches.append((gate, mode, mode_spec))
    if not matches:
        return None
    if len(matches) != 1:
        raise ResearchCtlError(
            "policy must define at most one retrospective approval mode"
        )
    return matches[0]


def _validate_workflow_graph(
    graph: dict[str, Any],
    stage_order: list[str],
    gate_specs: dict[str, dict[str, Any]],
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, dict[str, str] | None],
    tuple[tuple[str, str | None], ...],
]:
    expected_fields = {
        "stage_order",
        "stage_transitions",
        "stage_exit_requirements",
    }
    if set(graph) != expected_fields:
        raise ResearchCtlError(
            "policy workflow_graph fields do not match the runtime contract"
        )
    transitions = policy_object(
        graph.get("stage_transitions"), "workflow_graph.stage_transitions"
    )
    if set(transitions) != set(stage_order):
        raise ResearchCtlError(
            "policy workflow_graph.stage_transitions must define every stage"
        )
    normalized_transitions: dict[str, list[dict[str, Any]]] = {}
    for source, candidates in transitions.items():
        if not isinstance(candidates, list):
            raise ResearchCtlError(
                f"policy workflow_graph.stage_transitions.{source} must be a list"
            )
        destinations: set[str] = set()
        normalized_candidates: list[dict[str, Any]] = []
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict) or set(candidate) != {"to", "trigger"}:
                raise ResearchCtlError(
                    f"policy transition {source}[{index}] must be an object"
                )
            target = candidate.get("to")
            if target not in stage_order or target in destinations:
                raise ResearchCtlError(
                    f"policy transition from {source!r} has an invalid or duplicate target"
                )
            destinations.add(target)
            trigger = policy_object(
                candidate.get("trigger"), f"transition {source}->{target}.trigger"
            )
            trigger_type = trigger.get("type")
            if trigger_type == "checkpoint":
                if set(trigger) != {"type"}:
                    raise ResearchCtlError(
                        f"policy checkpoint transition {source}->{target} has extra fields"
                    )
            elif trigger_type == "stage_exit":
                if set(trigger) != {"type", "stage"}:
                    raise ResearchCtlError(
                        f"policy stage-exit transition {source}->{target} has invalid fields"
                    )
                exit_stage = trigger.get("stage")
                if exit_stage not in stage_order:
                    raise ResearchCtlError(
                        f"policy transition {source}->{target} references an unknown exit stage"
                    )
            else:
                raise ResearchCtlError(
                    f"policy transition {source}->{target} has unknown trigger type"
                )
            normalized_candidates.append(candidate)
        normalized_transitions[source] = normalized_candidates

    raw_exits = policy_object(
        graph.get("stage_exit_requirements"),
        "workflow_graph.stage_exit_requirements",
    )
    if set(raw_exits) != set(stage_order):
        raise ResearchCtlError(
            "policy workflow_graph.stage_exit_requirements must define every stage"
        )
    exits: dict[str, dict[str, str] | None] = {}
    gate_ref_owners: dict[tuple[str, str | None], str] = {}
    for stage, value in raw_exits.items():
        if value is None:
            exits[stage] = None
            continue
        requirement = policy_object(value, f"stage exit requirement {stage}")
        if set(requirement) not in ({"gate"}, {"gate", "target"}):
            raise ResearchCtlError(
                f"policy stage exit requirement {stage} has invalid fields"
            )
        gate = requirement.get("gate")
        if not isinstance(gate, str) or gate not in gate_specs:
            raise ResearchCtlError(
                f"policy stage exit requirement {stage} references an unknown Gate"
            )
        target = requirement.get("target")
        target_specs = gate_specs[gate].get("approval_targets")
        if target is None and isinstance(target_specs, dict):
            raise ResearchCtlError(
                f"policy stage exit requirement {stage} requires a Gate target"
            )
        if target is not None:
            if not isinstance(target_specs, dict) or target not in target_specs:
                raise ResearchCtlError(
                    f"policy stage exit requirement {stage} has an unknown target"
                )
        owner_key = (gate, target)
        if owner_key in gate_ref_owners:
            suffix = f"/{target}" if target is not None else ""
            raise ResearchCtlError(
                f"policy Gate reference {gate}{suffix} has multiple exit stages"
            )
        gate_ref_owners[owner_key] = stage
        exits[stage] = dict(requirement)

    for gate, spec in gate_specs.items():
        target_specs = spec.get("approval_targets")
        if isinstance(target_specs, dict):
            declared = {(gate, target) for target in target_specs}
            owned = {key for key in gate_ref_owners if key[0] == gate}
            if declared != owned:
                raise ResearchCtlError(
                    f"policy Gate {gate} targets must each own one stage exit requirement"
                )
        elif (gate, None) not in gate_ref_owners:
            raise ResearchCtlError(
                f"policy Gate {gate} must own exactly one stage exit requirement"
            )

    for source, candidates in normalized_transitions.items():
        for candidate in candidates:
            trigger = candidate["trigger"]
            exit_stage = trigger.get("stage") if trigger.get("type") == "stage_exit" else None
            if exit_stage is not None and exits.get(exit_stage) is None:
                raise ResearchCtlError(
                    f"policy transition {source}->{candidate['to']} references a stage "
                    f"without an exit Gate"
                )

    gate_sequence = tuple(
        (requirement["gate"], requirement.get("target"))
        for stage in stage_order
        if (requirement := exits[stage]) is not None
    )
    if not gate_sequence:
        raise ResearchCtlError("policy workflow graph must define at least one Gate")
    derived_gate_order = tuple(dict.fromkeys(gate for gate, _target in gate_sequence))
    if set(derived_gate_order) != set(gate_specs):
        raise ResearchCtlError(
            "policy Gates must match the Gate references owned by stage exits"
        )
    for gate in derived_gate_order:
        occurrences = [target for candidate, target in gate_sequence if candidate == gate]
        targets = gate_specs[gate].get("approval_targets")
        if isinstance(targets, dict):
            if any(target is None for target in occurrences):
                raise ResearchCtlError(
                    f"policy targeted Gate {gate} cannot own an untargeted stage exit"
                )
        elif occurrences != [None]:
            raise ResearchCtlError(
                f"policy Gate {gate} must own exactly one untargeted stage exit"
            )
    return normalized_transitions, exits, gate_sequence


def load_policy() -> Policy:
    runtime = load_runtime_contract()
    path = policy_path()
    try:
        raw = strict_json_loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ResearchCtlError(f"policy file not found: {path}") from exc
    except (OSError, UnicodeError) as exc:
        raise ResearchCtlError(f"cannot read policy file {path}: {exc}") from exc
    except (DuplicateJsonKeyError, NonStandardJsonConstantError) as exc:
        raise ResearchCtlError(f"policy contains {exc}: {path}") from exc
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
    _validate_semantic_governance(raw)
    schema_version = raw.get("schema_version")
    if schema_version != runtime.state_schema_version:
        raise ResearchCtlError(
            "unsupported policy schema_version "
            f"{schema_version!r}; the runtime contract requires "
            f"{runtime.state_schema_version!r}"
        )
    workflow_version = raw.get("workflow_version")
    if not isinstance(workflow_version, str) or not workflow_version.strip():
        raise ResearchCtlError("policy workflow_version must be a non-empty string")
    workflow_graph = policy_object(raw.get("workflow_graph"), "workflow_graph")
    stage_order = _string_list(
        workflow_graph.get("stage_order"), "workflow_graph.stage_order"
    )
    raw_stage_exits = policy_object(
        workflow_graph.get("stage_exit_requirements"),
        "workflow_graph.stage_exit_requirements",
    )
    if set(raw_stage_exits) != set(stage_order):
        raise ResearchCtlError(
            "policy workflow_graph.stage_exit_requirements must define every stage"
        )
    gate_order: list[str] = []
    for stage in stage_order:
        requirement = raw_stage_exits[stage]
        if requirement is None:
            continue
        requirement = policy_object(requirement, f"stage exit requirement {stage}")
        gate = requirement.get("gate")
        if not isinstance(gate, str) or not gate.strip():
            raise ResearchCtlError(
                f"policy stage exit requirement {stage} must name a Gate"
            )
        if gate not in gate_order:
            gate_order.append(gate)
    if raw.get("artifact_role_cardinality_default") != "one":
        raise ResearchCtlError(
            "policy artifact_role_cardinality_default must be 'one'"
        )

    artifact_layout = policy_object(raw.get("artifact_layout"), "artifact_layout")
    _exact_fields(
        artifact_layout,
        {
            "generated_root",
            "stage_path_template",
            "snapshot_root",
            "snapshot_stage_path_template",
            "instruction",
        },
        "artifact_layout",
    )
    _non_empty_string(
        artifact_layout.get("instruction"), "artifact_layout.instruction"
    )
    artifact_root = _project_local_root(
        artifact_layout.get("generated_root"), "artifact_layout.generated_root"
    )
    snapshot_root = _project_local_root(
        artifact_layout.get("snapshot_root"), "artifact_layout.snapshot_root"
    )
    if (
        artifact_root == snapshot_root
        or artifact_root in snapshot_root.parents
        or snapshot_root in artifact_root.parents
    ):
        raise ResearchCtlError(
            "policy artifact and snapshot roots must be disjoint directories"
        )
    expected_stage_template = f"{artifact_root.as_posix()}/<stage-id>"
    if artifact_layout.get("stage_path_template") != expected_stage_template:
        raise ResearchCtlError(
            "policy artifact_layout.stage_path_template must be "
            f"{expected_stage_template!r}"
        )
    if expected_stage_template not in artifact_layout["instruction"]:
        raise ResearchCtlError(
            "policy artifact_layout.instruction must reference "
            f"{expected_stage_template!r}"
        )
    expected_snapshot_template = f"{snapshot_root.as_posix()}/<stage-id>"
    if (
        artifact_layout.get("snapshot_stage_path_template")
        != expected_snapshot_template
    ):
        raise ResearchCtlError(
            "policy artifact_layout.snapshot_stage_path_template must be "
            f"{expected_snapshot_template!r}"
        )

    gate_specs = policy_object(raw.get("gates"), "gates")
    if set(gate_specs) != set(gate_order):
        raise ResearchCtlError("policy gates must define exactly gate_order")
    normalized_specs: dict[str, dict[str, Any]] = {}
    retrospective_mode_count = 0
    retrospective_flags: set[str] = set()
    for gate in gate_order:
        spec = policy_object(gate_specs[gate], f"gate {gate}")
        _validate_gate_semantics(gate, spec)
        validate_required_artifact_roles(gate, spec, stage_order)
        approval_modes = spec.get("approval_modes")
        if approval_modes is not None:
            mode_specs = policy_object(
                approval_modes, f"gate {gate}.approval_modes"
            )
            default_mode = spec.get("default_approval_mode")
            for mode, mode_spec in mode_specs.items():
                if not isinstance(mode, str) or not mode:
                    raise ResearchCtlError(
                        f"policy Gate {gate} approval mode names must be non-empty strings"
                    )
                contract = policy_object(
                    mode_spec, f"gate {gate}.approval_modes.{mode}"
                )
                markers = RETROSPECTIVE_MODE_MARKERS & set(contract)
                if markers:
                    if markers != RETROSPECTIVE_MODE_MARKERS:
                        raise ResearchCtlError(
                            f"policy Gate {gate} approval mode {mode} has an incomplete "
                            "retrospective exception contract"
                        )
                    if mode == default_mode:
                        raise ResearchCtlError(
                            f"policy Gate {gate} default approval mode cannot be the "
                            "retrospective exception"
                        )
                    validate_retrospective_revision_import(
                        gate, mode, contract, stage_order
                    )
                    flag = contract["cli_flag"]
                    if flag in retrospective_flags:
                        raise ResearchCtlError(
                            f"policy approval mode cli_flag {flag!r} is duplicated"
                        )
                    retrospective_flags.add(flag)
                    retrospective_mode_count += 1
        normalized_specs[gate] = spec
    if retrospective_mode_count != 1:
        raise ResearchCtlError(
            "policy must define exactly one retrospective approval mode"
        )

    stage_transitions, stage_exit_requirements, gate_sequence = (
        _validate_workflow_graph(
            workflow_graph, stage_order, normalized_specs
        )
    )
    release_gates = [
        gate
        for gate, spec in normalized_specs.items()
        if isinstance(spec.get("approval_targets"), dict)
    ]
    if len(release_gates) != 1:
        raise ResearchCtlError(
            "policy must define exactly one targeted external-release Gate"
        )
    release_gate = release_gates[0]
    release_targets = tuple(
        target
        for gate, target in gate_sequence
        if gate == release_gate and target is not None
    )
    if not release_targets:
        raise ResearchCtlError(
            f"policy external-release Gate {release_gate} must define targets"
        )
    stages = policy_object(raw.get("stages"), "stages")
    if set(stages) != set(stage_order):
        raise ResearchCtlError("policy stages must define exactly stage_order")
    _validate_stage_semantics(stages, stage_order)

    policy = Policy(
        schema_version=schema_version,
        workflow_version=workflow_version,
        stage_order=tuple(stage_order),
        gate_order=tuple(gate_order),
        gate_sequence=gate_sequence,
        stage_transitions=stage_transitions,
        stage_exit_requirements=stage_exit_requirements,
        gate_specs=normalized_specs,
        release_gate=release_gate,
        release_targets=release_targets,
        initial_release_target=release_targets[0],
        artifact_root=artifact_root,
        snapshot_root=snapshot_root,
        runtime=runtime,
        raw=raw,
    )
    # Validate the machine contract against the actual initial-state writer while
    # loading policy, so an incompatible schema cannot pass discovery and fail
    # only when the first workspace is created.  The local import avoids making
    # the store module part of policy parsing's import graph.
    from .store import validate_runtime_writer_contract

    validate_runtime_writer_contract(policy)
    return policy
