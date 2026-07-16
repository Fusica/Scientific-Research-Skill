"""CLI command orchestration over the functional workflow modules."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .artifacts import (
    create_revision_snapshot,
    current_artifact_revision,
    hash_file_with_size,
    is_research_control_file,
    is_snapshot_path,
    iter_current_artifact_pointers,
    resolve_artifact_path,
    resolved_workspace_roots,
    role_is_bound_by_approved_gate,
    stored_artifact_path,
    verify_revision_files,
)
from .constants import (
    ARTIFACT_ID_RE,
    ARTIFACT_ROLE_RE,
    CLEAN_BREAK_REINIT_GUIDANCE,
    LEGACY_RELATIVE_PATH,
    MAX_SNAPSHOT_BYTES,
    MEMORY_RELATIVE_PATH,
    Policy,
    ResearchCtlError,
    STATE_RELATIVE_PATH,
)
from .doctor import validate_state
from .gates import (
    approval_targets_for_gate,
    command_actor,
    decision_id,
    gate_artifact_refs,
    gate_approval_destination,
    gate_ref_owner_stage,
    gate_ref_prerequisites,
    gate_record,
    latest_approved_artifact_refs,
    selection_artifact_ref,
    transition_requirements,
    waived_artifact_roles_for_refs,
)
from .policy import retrospective_gate_contract
from .records import PendingRecordManifest, inspect_record_manifests
from .store import (
    atomic_write_json,
    default_memory,
    ensure_local_git_exclude,
    load_state,
    new_state,
    record_stage_transition,
    require_compatible_state,
    write_mutated_state,
)
from .timeutils import next_state_timestamp


def cmd_init(root: Path, policy: Policy, _args: argparse.Namespace) -> int:
    state_path = root / STATE_RELATIVE_PATH
    memory_path = root / MEMORY_RELATIVE_PATH
    legacy_path = root / LEGACY_RELATIVE_PATH
    artifact_root, snapshot_root = resolved_workspace_roots(root, policy)
    state_is_new = not state_path.exists()

    if not state_is_new:
        state = load_state(root)
        require_compatible_state(state, policy)
        print(f"state already exists; left unchanged: {state_path}")
    else:
        if legacy_path.exists():
            raise ResearchCtlError(
                f"unsupported legacy state found at {LEGACY_RELATIVE_PATH}; "
                f"{CLEAN_BREAK_REINIT_GUIDANCE}"
            )
        state = new_state(root, policy)

    if memory_path.exists():
        if not memory_path.is_file():
            raise ResearchCtlError(
                f"project memory path exists but is not a regular file: {memory_path}"
            )
        print(f"memory already exists; left unchanged: {memory_path}")
    else:
        try:
            memory_path.parent.mkdir(parents=True, exist_ok=True)
            memory_path.write_text(
                default_memory(str(state.get("project_name") or root.name)),
                encoding="utf-8",
            )
        except OSError as exc:
            raise ResearchCtlError(f"cannot create project memory {memory_path}: {exc}") from exc
        print(f"created {memory_path}")

    for workspace, label in (
        (artifact_root, "artifact workspace"),
        (snapshot_root, "snapshot workspace"),
    ):
        existed = workspace.is_dir()
        try:
            workspace.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ResearchCtlError(f"cannot create {label} {workspace}: {exc}") from exc
        if not existed:
            print(f"created {workspace}")

    if ensure_local_git_exclude(root):
        print("added .research/ to this clone's Git info/exclude")
    if state_is_new:
        atomic_write_json(state_path, state)
        print(f"created {state_path}")
    print(f"research workflow enabled for {state['project_id']}")
    return 0


def cmd_status(root: Path, policy: Policy, args: argparse.Namespace) -> int:
    state = load_state(root)
    require_compatible_state(state, policy)
    if args.json:
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0

    print(f"project_root: {root}")
    print(f"project_id: {state.get('project_id', '<missing>')}")
    print(f"project_name: {state.get('project_name', '<missing>')}")
    print(f"enabled: {str(state.get('enabled', '<missing>')).lower()}")
    lifecycle = state.get("lifecycle")
    lifecycle_status = (
        lifecycle.get("status") if isinstance(lifecycle, dict) else "<missing>"
    )
    print(f"lifecycle: {lifecycle_status}")
    print(f"current_stage: {state.get('current_stage', '<missing>')}")
    print("gates:")
    gates = state.get("gates")
    if isinstance(gates, dict):
        for gate in policy.gate_order:
            record = gates.get(gate)
            targets = approval_targets_for_gate(policy, gate)
            if targets:
                print(f"  {gate}:")
                records = record.get("targets") if isinstance(record, dict) else None
                for target in targets:
                    target_record = (
                        records.get(target) if isinstance(records, dict) else None
                    )
                    status = (
                        target_record.get("status")
                        if isinstance(target_record, dict)
                        else "<missing>"
                    )
                    print(f"    {target}: {status}")
            else:
                status = (
                    record.get("status") if isinstance(record, dict) else "<missing>"
                )
                print(f"  {gate}: {status}")
    else:
        print("  <invalid>")
    checkpoint = state.get("last_checkpoint")
    if isinstance(checkpoint, dict):
        print(f"last_checkpoint: {checkpoint.get('summary', '<missing>')}")
        print(f"checkpoint_at: {checkpoint.get('timestamp', '<missing>')}")
    else:
        print("last_checkpoint: none")
    return 0


def cmd_toggle(
    root: Path, policy: Policy, args: argparse.Namespace, *, enabled: bool
) -> int:
    state = load_state(root)
    require_compatible_state(state, policy)
    reason = args.reason.strip()
    if not reason:
        raise ResearchCtlError("enable and disable require a non-empty --reason")
    if enabled:
        errors, _warnings = validate_state(root, state, policy)
        if errors:
            preview = "; ".join(errors[:3])
            raise ResearchCtlError(
                f"state is invalid and cannot be enabled; run `researchctl doctor`: {preview}"
            )
    if state.get("enabled") is enabled:
        print(f"research workflow already {'enabled' if enabled else 'disabled'}")
        return 0
    history = state.get("activation_history")
    if not isinstance(history, list):
        raise ResearchCtlError("state activation_history must be a list")
    timestamp = next_state_timestamp(state)
    history.append(
        {
            "action": "enable" if enabled else "disable",
            "previous_enabled": not enabled,
            "new_enabled": enabled,
            "reason": reason,
            "actor": command_actor(),
            "decided_at": timestamp,
        }
    )
    state["enabled"] = enabled
    write_mutated_state(root, state)
    print(f"research workflow {'enabled' if enabled else 'disabled'}")
    return 0


def _decision_defense(args: argparse.Namespace) -> dict[str, list[str]]:
    """Normalize the shared mechanical fields for a human decision."""

    values: dict[str, list[str]] = {}
    for destination, field, required in (
        ("supporting_evidence_id", "supporting_evidence_ids", True),
        ("opposing_evidence_id", "opposing_evidence_ids", False),
        ("unresolved_risk", "unresolved_risks", False),
        ("decision_condition", "decision_conditions", True),
    ):
        raw = getattr(args, destination, [])
        normalized = [item.strip() for item in raw if isinstance(item, str) and item.strip()]
        if required and not normalized:
            raise ResearchCtlError(
                f"decision requires at least one --{destination.replace('_', '-')}"
            )
        if len(normalized) != len(set(normalized)):
            raise ResearchCtlError(
                f"decision --{destination.replace('_', '-')} values must be unique"
            )
        values[field] = normalized
    return values


def _require_active_lifecycle(state: dict[str, Any]) -> None:
    lifecycle = state.get("lifecycle")
    status = lifecycle.get("status") if isinstance(lifecycle, dict) else None
    if status != "active":
        raise ResearchCtlError(
            f"project lifecycle is {status!r}; reopen it before research mutations"
        )


def cmd_lifecycle(root: Path, policy: Policy, args: argparse.Namespace) -> int:
    action = args.action
    if action not in policy.runtime.lifecycle_actions:
        raise ResearchCtlError(f"unsupported lifecycle action {action!r}")
    reason = args.reason.strip()
    if not reason:
        raise ResearchCtlError("lifecycle decisions require a non-empty --reason")
    defense = _decision_defense(args)

    state = load_state(root)
    require_compatible_state(state, policy)
    state_errors, _state_warnings = validate_state(
        root,
        state,
        policy,
        verify_artifact_integrity=action != "reopen",
        allow_binding_drift_for=(
            frozenset(policy.gate_order) if action == "reopen" else frozenset()
        ),
    )
    if state_errors:
        raise ResearchCtlError(
            "state is invalid; run `researchctl doctor`: "
            + "; ".join(state_errors[:3])
        )
    lifecycle = state.get("lifecycle")
    if not isinstance(lifecycle, dict) or not isinstance(lifecycle.get("history"), list):
        raise ResearchCtlError("state lifecycle record is invalid; run `researchctl doctor`")
    previous_status = lifecycle.get("status")
    if previous_status not in policy.runtime.lifecycle_statuses:
        raise ResearchCtlError(f"invalid lifecycle status {previous_status!r}")
    if action == "terminate":
        if previous_status != "active":
            raise ResearchCtlError("lifecycle terminate requires active status")
        new_status = "terminated"
    elif action == "complete":
        if previous_status != "active":
            raise ResearchCtlError("lifecycle complete requires active status")
        release_record = gate_record(
            state, policy, policy.release_gate, policy.initial_release_target
        )
        if (
            not isinstance(release_record, dict)
            or release_record.get("status") != "approved"
        ):
            raise ResearchCtlError(
                "lifecycle complete requires approved Gate "
                f"{policy.release_gate}/{policy.initial_release_target}"
            )
        new_status = "completed"
    else:
        if previous_status not in {"terminated", "completed"}:
            raise ResearchCtlError("lifecycle reopen requires terminated or completed status")
        new_status = "active"

    gate_value = getattr(args, "gate", None)
    target_value = getattr(args, "target", None)
    affected_gate = gate_value.strip() if isinstance(gate_value, str) else None
    affected_target = target_value.strip() if isinstance(target_value, str) else None
    if action != "reopen" and (affected_gate is not None or affected_target is not None):
        raise ResearchCtlError("--gate and --target are valid only for lifecycle reopen")
    if affected_gate is None and affected_target is not None:
        raise ResearchCtlError("--target requires --gate")
    if action == "reopen" and previous_status == "completed" and affected_gate is None:
        raise ResearchCtlError("completed lifecycle reopen requires --gate")
    if affected_gate is not None:
        if affected_gate not in policy.gate_order:
            raise ResearchCtlError(f"unknown Gate {affected_gate!r}")
        configured_targets = approval_targets_for_gate(policy, affected_gate)
        if configured_targets and affected_target not in configured_targets:
            raise ResearchCtlError(
                f"Gate {affected_gate} requires --target naming one of: "
                + ", ".join(configured_targets)
            )
        if not configured_targets and affected_target is not None:
            raise ResearchCtlError(
                f"--target is valid only for a targeted Gate; {affected_gate} is untargeted"
            )

    actor = command_actor()
    reopened_gate_decision: str | None = None
    cascaded_reopens: list[tuple[str, str | None, str]] = []
    if action == "reopen" and affected_gate is not None:
        reopened_gate_decision, cascaded_reopens = _record_gate_reopen(
            state,
            policy,
            gate=affected_gate,
            target=affected_target,
            reason=reason,
            actor=actor,
            defense=defense,
        )
    artifact_refs = [
        reference
        for _label, reference in iter_current_artifact_pointers(state, policy)
    ]
    timestamp = next_state_timestamp(state)
    identifier = decision_id()
    decision: dict[str, Any] = {
        "decision_id": identifier,
        "action": action,
        "previous_status": previous_status,
        "new_status": new_status,
        "reason": reason,
        "actor": actor,
        "decided_at": timestamp,
        "artifact_refs": artifact_refs,
        **defense,
        "stage": state.get("current_stage"),
    }
    if reopened_gate_decision is not None and affected_gate is not None:
        decision["gate_ref"] = {
            "gate": affected_gate,
            **({"target": affected_target} if affected_target is not None else {}),
        }
        decision["gate_decision_id"] = reopened_gate_decision
    lifecycle["history"].append(decision)
    lifecycle["status"] = new_status
    lifecycle["latest_decision_id"] = identifier
    write_mutated_state(root, state)
    print(f"lifecycle {action}: {identifier}")
    if reopened_gate_decision is not None and affected_gate is not None:
        gate_label = affected_gate + (
            f"/{affected_target}" if affected_target is not None else ""
        )
        print(f"reopened affected Gate {gate_label}: {reopened_gate_decision}")
    for cascaded_gate, cascaded_target, cascaded_identifier in cascaded_reopens:
        cascaded_label = cascaded_gate + (
            f"/{cascaded_target}" if cascaded_target is not None else ""
        )
        print(
            f"automatically reopened downstream Gate {cascaded_label}: "
            f"{cascaded_identifier}"
        )
    return 0


def _artifact_role_bucket(
    state: dict[str, Any], stage: str, role: str
) -> dict[str, Any]:
    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ResearchCtlError("state artifacts must be a v2 object")
    stage_bucket = artifacts.setdefault(stage, {})
    if not isinstance(stage_bucket, dict):
        raise ResearchCtlError(f"artifacts.{stage} must be a role mapping")
    role_bucket = stage_bucket.setdefault(role, {})
    if not isinstance(role_bucket, dict):
        raise ResearchCtlError(f"artifacts.{stage}.{role} must be an ID mapping")
    return role_bucket


def _cascade_reopen_downstream_gates(
    state: dict[str, Any],
    policy: Policy,
    *,
    upstream_gate: str,
    upstream_target: str | None,
    upstream_decision_id: str,
    reason: str,
    actor: str,
    defense: dict[str, list[str]],
) -> list[tuple[str, str | None, str]]:
    """Invalidate approved downstream GateRefs in reverse stage-exit order."""

    upstream_ref = (upstream_gate, upstream_target)
    try:
        upstream_index = policy.gate_sequence.index(upstream_ref)
    except ValueError as exc:
        raise ResearchCtlError(f"unknown upstream Gate reference {upstream_ref!r}") from exc
    cascaded: list[tuple[str, str | None, str]] = []
    for downstream_gate, downstream_target in reversed(
        policy.gate_sequence[upstream_index + 1 :]
    ):
        record = gate_record(
            state, policy, downstream_gate, downstream_target
        )
        if not isinstance(record, dict) or record.get("status") != "approved":
            continue
        history = record.get("history")
        if not isinstance(history, list):
            raise ResearchCtlError(f"invalid Gate record: {downstream_gate}")
        timestamp = next_state_timestamp(state)
        identifier = decision_id()
        cascade_values = {
            "upstream_gate_ref": {
                "gate": upstream_gate,
                **(
                    {"target": upstream_target}
                    if upstream_target is not None
                    else {}
                ),
            },
            "upstream_decision_id": upstream_decision_id,
            "upstream_reason": reason,
        }
        decision: dict[str, Any] = {
            "decision_id": identifier,
            "action": "reopen",
            "previous_status": "approved",
            "new_status": "reopened",
            "reason": (
                "Automatically invalidated because upstream Gate reference "
                f"{upstream_ref!r} was reopened: {reason}"
            ),
            "actor": actor,
            "decided_at": timestamp,
            "artifact_refs": latest_approved_artifact_refs(history),
            **defense,
            "cascade": {
                field: cascade_values[field]
                for field in policy.runtime.cascade_fields
            },
        }
        history.append(decision)
        record["status"] = "reopened"
        record["latest_decision_id"] = identifier
        cascaded.append((downstream_gate, downstream_target, identifier))
    return cascaded


def _record_gate_reopen(
    state: dict[str, Any],
    policy: Policy,
    *,
    gate: str,
    target: str | None,
    reason: str,
    actor: str,
    defense: dict[str, list[str]],
) -> tuple[str, list[tuple[str, str | None, str]]]:
    """Record one root Gate reopen and its existing downstream cascade."""

    record = gate_record(state, policy, gate, target)
    if not isinstance(record, dict) or not isinstance(record.get("history"), list):
        raise ResearchCtlError(f"invalid Gate record: {gate}")
    if record.get("status") != "approved":
        label = gate + (f"/{target}" if target is not None else "")
        raise ResearchCtlError(f"Gate {label} can only be reopened from approved status")
    current_stage = state.get("current_stage")
    if current_stage not in policy.stage_order:
        raise ResearchCtlError(f"state has unknown current_stage: {current_stage!r}")

    identifier = decision_id()
    cascaded = _cascade_reopen_downstream_gates(
        state,
        policy,
        upstream_gate=gate,
        upstream_target=target,
        upstream_decision_id=identifier,
        reason=reason,
        actor=actor,
        defense=defense,
    )
    timestamp = next_state_timestamp(state)
    decision: dict[str, Any] = {
        "decision_id": identifier,
        "action": "reopen",
        "previous_status": "approved",
        "new_status": "reopened",
        "reason": reason,
        "actor": actor,
        "decided_at": timestamp,
        "artifact_refs": latest_approved_artifact_refs(record["history"]),
        **defense,
    }
    record["history"].append(decision)
    record["status"] = "reopened"
    record["latest_decision_id"] = identifier
    reopen_stage = gate_ref_owner_stage(policy, gate, target)
    if policy.stage_order.index(current_stage) > policy.stage_order.index(reopen_stage):
        record_stage_transition(
            state,
            to_stage=reopen_stage,
            trigger=f"gate-reopen:{identifier}",
            timestamp=timestamp,
        )
    return identifier, cascaded


def cmd_artifact(root: Path, policy: Policy, args: argparse.Namespace) -> int:
    state = load_state(root)
    require_compatible_state(state, policy)
    _require_active_lifecycle(state)
    structural_errors, _warnings = validate_state(
        root, state, policy, verify_artifact_integrity=False
    )
    if structural_errors:
        raise ResearchCtlError(
            "state is invalid; run `researchctl doctor`: "
            + "; ".join(structural_errors[:3])
        )

    stage = args.stage or state.get("current_stage")
    if stage not in policy.stage_order:
        raise ResearchCtlError(f"unknown artifact stage: {stage!r}")
    role = args.role.strip()
    if not ARTIFACT_ROLE_RE.fullmatch(role):
        raise ResearchCtlError("artifact role must use lower_snake_case")
    artifact_id = args.artifact_id.strip()
    if not ARTIFACT_ID_RE.fullmatch(artifact_id):
        raise ResearchCtlError("artifact ID contains unsupported characters")

    source = resolve_artifact_path(root, args.path)
    if not source.is_file():
        raise ResearchCtlError(f"artifact path must be a regular file: {args.path}")
    if is_research_control_file(root, source) or is_snapshot_path(root, policy, source):
        raise ResearchCtlError(
            "research control metadata and immutable snapshots cannot be registered "
            f"as source artifacts: {args.path}"
        )
    content_hash, size_bytes = hash_file_with_size(
        source, max_bytes=MAX_SNAPSHOT_BYTES
    )
    source_path, external = stored_artifact_path(root, source)

    role_bucket = _artifact_role_bucket(state, stage, role)
    existing = role_bucket.get(artifact_id)
    if existing is None and role_bucket:
        existing_ids = ", ".join(sorted(str(value) for value in role_bucket))
        raise ResearchCtlError(
            f"artifact role {stage}.{role} already has its one canonical artifact "
            f"({existing_ids}); reuse that stable artifact ID for the next revision"
        )
    current = current_artifact_revision(existing)
    if current is not None and (
        current.get("source_path") == source_path
        and current.get("content_hash") == content_hash
        and current.get("size_bytes") == size_bytes
    ):
        integrity_errors = verify_revision_files(
            root,
            policy,
            current,
            f"artifacts.{stage}.{role}.{artifact_id}",
            verify_source=True,
            verify_snapshot=True,
        )
        if integrity_errors:
            raise ResearchCtlError("; ".join(integrity_errors))
        print(
            f"artifact already registered: {stage}.{role} {artifact_id} "
            f"r{current['revision']} {content_hash}"
        )
        return 0
    if existing is not None and current is None:
        raise ResearchCtlError(
            f"artifact entry {stage}.{role}.{artifact_id} is invalid; run doctor"
        )

    if role == policy.runtime.scientific_record_artifact_role:
        inspection = inspect_record_manifests(
            root,
            state,
            policy,
            pending=PendingRecordManifest(
                stage=stage,
                artifact_id=artifact_id,
                path=source,
            ),
        )
        if inspection.errors:
            raise ResearchCtlError(
                "record manifest is invalid: " + "; ".join(inspection.errors[:5])
            )

    frozen_by = role_is_bound_by_approved_gate(state, policy, stage, role)
    if frozen_by is not None:
        raise ResearchCtlError(
            f"artifact role {stage}.{role} is bound by approved Gate {frozen_by}; "
            "reopen that Gate before registering another revision"
        )
    next_revision = 1 if current is None else int(current["revision"]) + 1
    registered_at = next_state_timestamp(state)
    snapshot_path = create_revision_snapshot(
        root,
        policy,
        source=source,
        stage=stage,
        role=role,
        artifact_id=artifact_id,
        revision=next_revision,
        expected_hash=content_hash,
        expected_size=size_bytes,
    )
    revision = {
        "revision": next_revision,
        "source_path": source_path,
        "snapshot_path": snapshot_path,
        "content_hash": content_hash,
        "size_bytes": size_bytes,
        "registered_at": registered_at,
    }
    # Reverify both sides immediately before publishing the state pointer.
    final_errors = verify_revision_files(
        root,
        policy,
        revision,
        f"artifacts.{stage}.{role}.{artifact_id}.revisions[{next_revision - 1}]",
        verify_source=True,
        verify_snapshot=True,
    )
    if final_errors:
        raise ResearchCtlError("; ".join(final_errors))
    if existing is None:
        entry = {"current_revision": next_revision, "revisions": [revision]}
        role_bucket[artifact_id] = entry
    else:
        revisions = existing.get("revisions")
        if not isinstance(revisions, list):
            raise ResearchCtlError("artifact revision history is invalid")
        revisions.append(revision)
        existing["current_revision"] = next_revision
    write_mutated_state(root, state)
    print(
        f"registered artifact: {stage}.{role} {artifact_id} r{next_revision} "
        f"{content_hash} snapshot={snapshot_path}"
    )
    if external:
        print(
            "warning: artifact source is outside the project and may not be portable",
            file=sys.stderr,
        )
    return 0


def cmd_gate(root: Path, policy: Policy, args: argparse.Namespace) -> int:
    if args.action not in policy.runtime.gate_actions:
        raise ResearchCtlError(
            f"unsupported Gate action {args.action!r}; expected one of "
            + ", ".join(policy.runtime.gate_actions)
        )
    if args.gate not in policy.gate_order:
        raise ResearchCtlError(
            f"unknown Gate {args.gate!r}; expected one of "
            + ", ".join(policy.gate_order)
        )
    reason = args.reason.strip()
    if not reason:
        raise ResearchCtlError("Gate decisions require a non-empty --reason")
    defense = _decision_defense(args)
    requested_mode_value = getattr(args, "approval_mode", None)
    requested_mode = (
        requested_mode_value.strip()
        if isinstance(requested_mode_value, str) and requested_mode_value.strip()
        else None
    )
    retrospective_alias = getattr(args, "retrospective_mode_requested", None)
    if requested_mode is not None and retrospective_alias is not None:
        raise ResearchCtlError(
            "--approval-mode cannot be combined with the retrospective compatibility flag"
        )
    requested_mode = retrospective_alias or requested_mode
    retrospective_contract = retrospective_gate_contract(policy)
    retrospective_gate = (
        retrospective_contract[0] if retrospective_contract is not None else None
    )
    retrospective_mode = (
        retrospective_contract[1] if retrospective_contract is not None else None
    )
    retrospective_spec = (
        retrospective_contract[2] if retrospective_contract is not None else None
    )
    retrospective_import = (
        args.gate == retrospective_gate
        and requested_mode is not None
        and requested_mode == retrospective_mode
    )
    if retrospective_alias is not None and retrospective_contract is None:
        raise ResearchCtlError("policy does not define a retrospective approval mode")
    if retrospective_alias is not None and not (
        args.action == "approve" and args.gate == retrospective_gate
    ):
        cli_flag = (
            retrospective_spec.get("cli_flag")
            if isinstance(retrospective_spec, dict)
            else "<retrospective-flag>"
        )
        raise ResearchCtlError(
            f"{cli_flag} is valid only with `gate approve {retrospective_gate}`"
        )
    if requested_mode is not None and args.action != "approve":
        raise ResearchCtlError("--approval-mode is valid only with `gate approve`")

    spec = policy.gate_specs[args.gate]
    configured_targets = approval_targets_for_gate(policy, args.gate)
    target_value = getattr(args, "target", None)
    target = target_value.strip() if isinstance(target_value, str) else None
    if configured_targets:
        if target not in configured_targets:
            raise ResearchCtlError(
                f"Gate {args.gate} requires --target naming one of: "
                + ", ".join(configured_targets)
            )
    elif target is not None:
        raise ResearchCtlError(
            f"--target is valid only for a targeted Gate; {args.gate} is untargeted"
        )

    selection_role = spec.get("selection_artifact_role")
    selected_id_value = getattr(args, "selected_id", None)
    selected_id = selected_id_value.strip() if isinstance(selected_id_value, str) else None
    if args.action == "approve" and selection_role is not None:
        if not selected_id:
            raise ResearchCtlError(
                f"Gate {args.gate} requires --selected-id for the selected "
                "candidate recorded inside its portfolio artifact"
            )
    elif selected_id:
        raise ResearchCtlError(
            "--selected-id is valid only when approving a Gate with "
            "selection_artifact_role"
        )

    approval_mode: str | None = None
    approval_modes = spec.get("approval_modes")
    if args.action == "approve" and isinstance(approval_modes, dict):
        approval_mode = requested_mode or spec.get("default_approval_mode")
        if approval_mode not in approval_modes:
            raise ResearchCtlError(
                f"Gate {args.gate} approval mode must name one of: "
                + ", ".join(approval_modes)
            )
    elif requested_mode is not None:
        raise ResearchCtlError(
            f"Gate {args.gate} does not define policy approval modes"
        )

    state = load_state(root)
    require_compatible_state(state, policy)
    _require_active_lifecycle(state)
    state_errors, _state_warnings = validate_state(
        root,
        state,
        policy,
        verify_artifact_integrity=args.action == "approve",
        allow_binding_drift_for=(
            frozenset(policy.gate_order) if args.action == "reopen" else frozenset()
        ),
    )
    if state_errors:
        preview = "; ".join(state_errors[:3])
        suffix = " ..." if len(state_errors) > 3 else ""
        raise ResearchCtlError(
            f"state is invalid; run `researchctl doctor`: {preview}{suffix}"
        )
    gates = state.get("gates")
    if not isinstance(gates, dict) or set(gates) != set(policy.gate_order):
        raise ResearchCtlError("state gates do not match policy gate_order")
    record = gate_record(state, policy, args.gate, target)
    if not isinstance(record, dict) or not isinstance(record.get("history"), list):
        suffix = f"/{target}" if target is not None else ""
        raise ResearchCtlError(f"invalid Gate record: {args.gate}{suffix}")
    history = record["history"]
    previous_status = record.get("status")
    if previous_status not in policy.runtime.gate_statuses:
        raise ResearchCtlError(
            f"Gate {args.gate} has invalid status: {previous_status!r}"
        )

    actor = command_actor()
    if args.action == "reopen":
        identifier, cascaded_reopens = _record_gate_reopen(
            state,
            policy,
            gate=args.gate,
            target=target,
            reason=reason,
            actor=actor,
            defense=defense,
        )
        write_mutated_state(root, state)
        gate_label = args.gate + (f"/{target}" if target is not None else "")
        print(f"reopened Gate {gate_label}: {identifier}")
        for cascaded_gate, cascaded_target, cascaded_identifier in cascaded_reopens:
            cascaded_label = cascaded_gate + (
                f"/{cascaded_target}" if cascaded_target is not None else ""
            )
            print(
                f"automatically reopened downstream Gate {cascaded_label}: "
                f"{cascaded_identifier}"
            )
        return 0

    if previous_status == "approved":
        suffix = f"/{target}" if target is not None else ""
        raise ResearchCtlError(f"Gate {args.gate}{suffix} is already approved")
    for prerequisite_gate, prerequisite_target in gate_ref_prerequisites(
        policy, args.gate, target
    ):
        prerequisite_record = gate_record(
            state, policy, prerequisite_gate, prerequisite_target
        )
        if (
            not isinstance(prerequisite_record, dict)
            or prerequisite_record.get("status") != "approved"
        ):
            prerequisite_label = prerequisite_gate + (
                f"/{prerequisite_target}"
                if prerequisite_target is not None
                else ""
            )
            raise ResearchCtlError(
                f"Gate {args.gate} requires approved Gate {prerequisite_label}"
            )
    new_status = "approved"

    current_stage = state.get("current_stage")
    if current_stage not in policy.stage_order:
        raise ResearchCtlError(f"state has unknown current_stage: {current_stage!r}")
    advance_target = gate_approval_destination(
        policy, current_stage, args.gate, target
    )
    identifier = decision_id()
    timestamp = next_state_timestamp(state)
    artifact_refs = gate_artifact_refs(
        root,
        state,
        policy,
        args.gate,
        target,
        verify_integrity=True,
        approval_mode=approval_mode,
    )
    waived_artifact_roles: list[str] = []
    if retrospective_import and approval_mode is not None:
        waived_artifact_roles = waived_artifact_roles_for_refs(
            policy,
            args.gate,
            target,
            approval_mode,
            artifact_refs,
        )
        if not waived_artifact_roles:
            raise ResearchCtlError(
                "retrospective revision import has no unavailable historical roles; "
                f"use normal {args.gate} approval"
            )

    decision: dict[str, Any] = {
        "decision_id": identifier,
        "action": "approve",
        "previous_status": previous_status,
        "new_status": new_status,
        "reason": reason,
        "actor": actor,
        "decided_at": timestamp,
        "artifact_refs": artifact_refs,
        **defense,
    }
    if selection_role is not None:
        portfolio_ref = selection_artifact_ref(policy, args.gate, artifact_refs)
        if selected_id is None or portfolio_ref is None:
            raise ResearchCtlError(
                f"Gate {args.gate} selection could not bind its portfolio artifact"
            )
        decision["selection"] = {
            "selected_id": selected_id,
            "artifact_ref": portfolio_ref,
        }
    if approval_mode is not None:
        decision["approval_mode"] = approval_mode
        if retrospective_import:
            decision["waived_artifact_roles"] = waived_artifact_roles
    history.append(decision)
    record["status"] = new_status
    record["latest_decision_id"] = identifier

    if (
        advance_target in policy.stage_order
        and policy.stage_order.index(advance_target)
        > policy.stage_order.index(current_stage)
    ):
        record_stage_transition(
            state,
            to_stage=advance_target,
            trigger=f"gate-approve:{identifier}",
            timestamp=timestamp,
        )

    write_mutated_state(root, state)
    gate_label = args.gate + (f"/{target}" if target is not None else "")
    print(f"approved Gate {gate_label}: {identifier}")
    if retrospective_import:
        print(
            f"warning: {args.gate} used {approval_mode}; historical "
            "roles remain unverified: " + ", ".join(waived_artifact_roles),
            file=sys.stderr,
        )
    return 0


def cmd_checkpoint(root: Path, policy: Policy, args: argparse.Namespace) -> int:
    summary = args.summary.strip()
    if not summary:
        raise ResearchCtlError("checkpoint requires a non-empty --summary")
    state = load_state(root)
    require_compatible_state(state, policy)
    _require_active_lifecycle(state)
    state_errors, _state_warnings = validate_state(
        root, state, policy, verify_artifact_integrity=False
    )
    if state_errors:
        raise ResearchCtlError(
            "state is invalid; run `researchctl doctor`: "
            + "; ".join(state_errors[:3])
        )
    timestamp = next_state_timestamp(state)
    if args.stage is not None:
        current_stage = state.get("current_stage")
        if current_stage not in policy.stage_order or args.stage not in policy.stage_order:
            raise ResearchCtlError(f"unknown checkpoint stage: {args.stage!r}")
        if args.stage != current_stage:
            requirements = transition_requirements(policy, current_stage, args.stage)
            trigger = "checkpoint"
            if requirements:
                gate, target = requirements[0]
                label = gate + (f"/{target}" if target is not None else "")
                record = gate_record(state, policy, gate, target)
                history = record.get("history") if isinstance(record, dict) else None
                latest = history[-1] if isinstance(history, list) and history else None
                if (
                    not isinstance(record, dict)
                    or record.get("status") != "approved"
                    or not isinstance(latest, dict)
                    or latest.get("action") != "approve"
                    or latest.get("decision_id") != record.get("latest_decision_id")
                ):
                    raise ResearchCtlError(
                        f"stage transition {current_stage}->{args.stage} is driven by "
                        f"approving Gate {label}, not by checkpoint"
                    )
                gate_artifact_refs(
                    root,
                    state,
                    policy,
                    gate,
                    target,
                    verify_integrity=True,
                    approval_mode=latest.get("approval_mode"),
                )
                trigger = f"gate-approve:{latest['decision_id']}"
            record_stage_transition(
                state,
                to_stage=args.stage,
                trigger=trigger,
                timestamp=timestamp,
            )
    state["last_checkpoint"] = {"summary": summary, "timestamp": timestamp}
    write_mutated_state(root, state)
    print("checkpoint recorded")
    return 0


def cmd_doctor(root: Path, policy: Policy, _args: argparse.Namespace) -> int:
    state_path = root / STATE_RELATIVE_PATH
    if not state_path.is_file():
        print(f"[ERROR] missing {STATE_RELATIVE_PATH}; run `researchctl init`")
        print("doctor: 1 error(s), 0 warning(s)")
        return 1
    try:
        state = load_state(root)
    except ResearchCtlError as exc:
        print(f"[ERROR] {exc}")
        print("doctor: 1 error(s), 0 warning(s)")
        return 1
    if state.get("schema_version") != policy.schema_version:
        print(
            "[ERROR] unsupported state schema_version "
            f"{state.get('schema_version')!r}; v2 requires {policy.schema_version!r}; "
            f"{CLEAN_BREAK_REINIT_GUIDANCE}"
        )
        print("doctor: 1 error(s), 0 warning(s)")
        return 1
    errors, warnings = validate_state(root, state, policy)
    for error in errors:
        print(f"[ERROR] {error}")
    for warning in warnings:
        print(f"[WARNING] {warning}")
    if not errors:
        print("[OK] active v2 state, Gate, revision, snapshot, and workspace contracts are valid")
    print(f"doctor: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0
