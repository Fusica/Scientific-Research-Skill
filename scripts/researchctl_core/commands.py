"""CLI command orchestration over the functional workflow modules."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .artifacts import (
    artifact_identity_payload,
    is_research_control_file,
    iter_current_artifact_pointers,
    iter_gate_artifact_refs,
    prepare_artifact_bucket,
    resolve_artifact_path,
    role_is_bound_by_approved_gate,
    sha256_file,
    stored_artifact_path,
)
from .constants import (
    ARTIFACT_ID_RE,
    ARTIFACT_ROLE_RE,
    GATE_IDS,
    GATE_STATUSES,
    LEGACY_RELATIVE_PATH,
    MEMORY_RELATIVE_PATH,
    Policy,
    RESERVED_ARTIFACT_IDS,
    ResearchCtlError,
    STATE_RELATIVE_PATH,
)
from .doctor import validate_state
from .gates import (
    command_actor,
    decision_id,
    gate_artifact_refs,
    latest_approved_artifact_refs,
    required_gates,
    transition_requirements,
)
from .migration import migrate_legacy_state, read_legacy_fields
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
    artifact_root = root / policy.artifact_root
    notes: list[str] = []
    state_is_new = not state_path.exists()

    if not state_is_new:
        state = load_state(root)
        require_compatible_state(state, policy)
        print(f"state already exists; left unchanged: {state_path}")
    else:
        if legacy_path.is_file():
            state, notes = migrate_legacy_state(root, policy, legacy_path)
        else:
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

    artifact_root_existed = artifact_root.is_dir()
    try:
        artifact_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ResearchCtlError(
            f"cannot create artifact workspace {artifact_root}: {exc}"
        ) from exc
    if not artifact_root_existed:
        print(f"created {artifact_root}")

    if ensure_local_git_exclude(root):
        print("added .research/ to this clone's Git info/exclude")
    if state_is_new:
        atomic_write_json(state_path, state)
        print(f"created {state_path}")
    for note in notes:
        print(f"warning: {note}", file=sys.stderr)
    if state.get("enabled") is True:
        print(f"research workflow enabled for {state['project_id']}")
    else:
        print(
            f"research workflow remains disabled for {state['project_id']}; "
            "run `researchctl enable` to activate it"
        )
    return 0

def cmd_status(root: Path, _policy: Policy, args: argparse.Namespace) -> int:
    state = load_state(root)
    if args.json:
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0

    print(f"project_root: {root}")
    print(f"project_id: {state.get('project_id', '<missing>')}")
    print(f"project_name: {state.get('project_name', '<missing>')}")
    print(f"enabled: {str(state.get('enabled', '<missing>')).lower()}")
    print(f"current_stage: {state.get('current_stage', '<missing>')}")
    print("gates:")
    gates = state.get("gates")
    if isinstance(gates, dict):
        for gate in GATE_IDS:
            record = gates.get(gate)
            status = record.get("status") if isinstance(record, dict) else "<missing>"
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
    root: Path, policy: Policy, _args: argparse.Namespace, *, enabled: bool
) -> int:
    state = load_state(root)
    # Disabling is the emergency off switch after an incompatible plugin
    # update, so it must remain available even when doctor reports a version
    # mismatch. Re-enabling still requires a compatible state.
    if enabled:
        require_compatible_state(state, policy)
        errors, _warnings = validate_state(root, state, policy)
        if errors:
            preview = "; ".join(errors[:3])
            raise ResearchCtlError(
                f"state is invalid and cannot be enabled; run `researchctl doctor`: {preview}"
            )
    if state.get("enabled") is enabled:
        print(f"research workflow already {'enabled' if enabled else 'disabled'}")
        return 0
    state["enabled"] = enabled
    write_mutated_state(
        root,
        state,
        allow_timestamp_exhaustion=not enabled,
    )
    print(f"research workflow {'enabled' if enabled else 'disabled'}")
    return 0

def cmd_artifact(root: Path, policy: Policy, args: argparse.Namespace) -> int:
    state = load_state(root)
    require_compatible_state(state, policy)
    stage = args.stage or state.get("current_stage")
    if stage not in policy.stage_order:
        raise ResearchCtlError(f"unknown artifact stage: {stage!r}")
    role = args.role.strip()
    if not ARTIFACT_ROLE_RE.fullmatch(role):
        raise ResearchCtlError("artifact role must use lower_snake_case")
    artifact_id = args.artifact_id.strip()
    if not ARTIFACT_ID_RE.fullmatch(artifact_id):
        raise ResearchCtlError("artifact ID contains unsupported characters")
    if artifact_id in RESERVED_ARTIFACT_IDS:
        raise ResearchCtlError(
            f"artifact ID {artifact_id!r} is reserved by the artifact pointer structure"
        )
    version = args.version.strip()
    status = args.status.strip()
    if not version or not status:
        raise ResearchCtlError("artifact version and status must be non-empty")

    source = resolve_artifact_path(root, args.path)
    if not source.is_file():
        raise ResearchCtlError(f"artifact path must be a regular file: {args.path}")
    if is_research_control_file(root, source):
        raise ResearchCtlError(
            "research control metadata cannot be registered as scientific evidence: "
            f"{args.path}"
        )
    stored_path, external = stored_artifact_path(root, source)
    content_hash = sha256_file(source)
    pointer = {
        "path": stored_path,
        "artifact_id": artifact_id,
        "version": version,
        "content_hash": content_hash,
        "status": status,
    }

    role_bucket, migrated = prepare_artifact_bucket(root, state, stage, role)
    structural_errors, _warnings = validate_state(
        root, state, policy, verify_artifact_integrity=False
    )
    if structural_errors:
        preview = "; ".join(structural_errors[:3])
        raise ResearchCtlError(
            f"state is invalid; run `researchctl doctor`: {preview}"
        )
    existing = role_bucket.get(artifact_id)
    if isinstance(existing, dict) and existing == pointer:
        if migrated:
            write_mutated_state(root, state)
        print(
            f"artifact already registered: {stage}.{role} "
            f"{artifact_id}@{version} {content_hash}"
        )
        return 0

    frozen_by = role_is_bound_by_approved_gate(state, policy, stage, role)
    if frozen_by is not None:
        raise ResearchCtlError(
            f"artifact role {stage}.{role} is bound by approved Gate {frozen_by}; "
            "reopen that Gate before registering a replacement"
        )
    if isinstance(existing, dict) and str(existing.get("version")) == version:
        raise ResearchCtlError(
            f"artifact {artifact_id}@{version} already exists with different metadata; "
            "use a new version"
        )

    identity_sources = (
        *iter_current_artifact_pointers(state, policy),
        *iter_gate_artifact_refs(state),
    )
    for history_label, reference in identity_sources:
        old_path = reference.get("path")
        old_hash = reference.get("content_hash")
        if (
            reference.get("artifact_id") == artifact_id
            and str(reference.get("version")) == version
        ):
            historical_pointer = artifact_identity_payload(reference)
            if historical_pointer != pointer:
                raise ResearchCtlError(
                    f"artifact identity {artifact_id}@{version} was already bound to "
                    f"different metadata in {history_label}; use a new version"
                )
        if not isinstance(old_path, str) or not isinstance(old_hash, str):
            continue
        try:
            same_path = resolve_artifact_path(root, old_path) == source
        except ResearchCtlError:
            same_path = old_path == stored_path
        if same_path and old_hash != content_hash:
            raise ResearchCtlError(
                f"artifact path was already approved with different content in {history_label}; "
                "preserve that file and register the new version at a new path"
            )

    role_bucket[artifact_id] = pointer
    write_mutated_state(root, state)
    print(f"registered artifact: {stage}.{role} {artifact_id}@{version} {content_hash}")
    if external:
        print(
            "warning: artifact path is outside the project and may not be portable",
            file=sys.stderr,
        )
    return 0

def cmd_gate(root: Path, policy: Policy, args: argparse.Namespace) -> int:
    reason = args.reason.strip()
    if not reason:
        raise ResearchCtlError("Gate decisions require a non-empty --reason")
    state = load_state(root)
    require_compatible_state(state, policy)
    state_errors, _state_warnings = validate_state(
        root,
        state,
        policy,
        verify_artifact_integrity=args.action == "approve",
        # Reopening must remain a recovery path when any currently approved Gate
        # has stale bindings. Reverse-order Gate rules still prevent unsafe skips.
        allow_binding_drift_for=(
            frozenset(GATE_IDS) if args.action == "reopen" else frozenset()
        ),
    )
    if state_errors:
        preview = "; ".join(state_errors[:3])
        suffix = " ..." if len(state_errors) > 3 else ""
        raise ResearchCtlError(
            f"state is invalid; run `researchctl doctor`: {preview}{suffix}"
        )
    gates = state.get("gates")
    if not isinstance(gates, dict) or set(gates) != set(GATE_IDS):
        raise ResearchCtlError("state gates do not match the fixed Gate contract")
    record = gates.get(args.gate)
    if not isinstance(record, dict):
        raise ResearchCtlError(f"invalid Gate record: {args.gate}")
    history = record.get("history")
    if not isinstance(history, list):
        raise ResearchCtlError(f"Gate history must be a list: {args.gate}")
    previous_status = record.get("status")
    if previous_status not in GATE_STATUSES:
        raise ResearchCtlError(
            f"Gate {args.gate} has invalid status: {previous_status!r}"
        )

    if args.action == "approve":
        if previous_status == "approved":
            raise ResearchCtlError(f"Gate {args.gate} is already approved")
        spec = policy.gate_specs[args.gate]
        inferred_prerequisites = policy.gate_order[: policy.gate_order.index(args.gate)]
        prerequisites = tuple(
            dict.fromkeys((*inferred_prerequisites, *required_gates(spec)))
        )
        for prerequisite in prerequisites:
            prerequisite_record = gates.get(prerequisite)
            if not isinstance(prerequisite_record, dict):
                raise ResearchCtlError(
                    f"policy references unknown prerequisite Gate: {prerequisite}"
                )
            if prerequisite_record.get("status") != "approved":
                raise ResearchCtlError(
                    f"Gate {args.gate} requires approved Gate {prerequisite}"
                )
        required_stage = spec.get("required_stage")
        if required_stage is not None and state.get("current_stage") != required_stage:
            raise ResearchCtlError(
                f"Gate {args.gate} requires current_stage {required_stage!r}"
            )
        new_status = "approved"
    else:
        if previous_status != "approved":
            raise ResearchCtlError(
                f"Gate {args.gate} can only be reopened from approved status"
            )
        current_index = policy.gate_order.index(args.gate)
        for downstream in reversed(policy.gate_order[current_index + 1 :]):
            downstream_record = gates.get(downstream)
            if (
                isinstance(downstream_record, dict)
                and downstream_record.get("status") == "approved"
            ):
                raise ResearchCtlError(
                    f"Gate {args.gate} cannot be reopened while downstream Gate "
                    f"{downstream} is approved; reopen {downstream} first"
                )
        new_status = "reopened"

    timestamp = next_state_timestamp(state)
    identifier = decision_id()
    release_target: str | None = None
    if args.gate == "release":
        configured_targets = policy.gate_specs["release"].get("release_targets")
        if not isinstance(configured_targets, list) or not all(
            isinstance(target, str) for target in configured_targets
        ):
            raise ResearchCtlError("policy release_targets must be a string list")
        if args.action == "approve":
            stage_targets = {
                "paper": "initial_submission",
                "revision": "revision_rebuttal",
            }
            release_target = stage_targets.get(state.get("current_stage"))
            if release_target is None:
                raise ResearchCtlError(
                    "release Gate can only be approved from paper or revision stage"
                )
            if release_target not in configured_targets:
                raise ResearchCtlError(
                    f"policy does not permit release target {release_target!r}"
                )
        else:
            for previous_decision in reversed(history):
                if isinstance(previous_decision, dict) and isinstance(
                    previous_decision.get("release_target"), str
                ):
                    release_target = previous_decision["release_target"]
                    break
    artifact_refs = (
        gate_artifact_refs(root, state, policy, args.gate, release_target)
        if args.action == "approve"
        else latest_approved_artifact_refs(history)
    )
    entry: dict[str, Any] = {
        "decision_id": identifier,
        "action": args.action,
        "previous_status": previous_status,
        "new_status": new_status,
        "reason": reason,
        "actor": command_actor(),
        "decided_at": timestamp,
        "artifact_refs": artifact_refs,
    }
    if release_target is not None:
        entry["release_target"] = release_target
    history.append(entry)
    record["status"] = new_status
    record["latest_decision_id"] = identifier

    if args.action == "approve":
        target = policy.gate_specs[args.gate].get("advance_to")
        current_stage = state.get("current_stage")
        if current_stage not in policy.stage_order:
            raise ResearchCtlError(f"state has unknown current_stage: {current_stage!r}")
        should_advance = target is not None and policy.stage_order.index(
            target
        ) > policy.stage_order.index(current_stage)
        if should_advance:
            record_stage_transition(
                state,
                to_stage=target,
                trigger=f"gate:{args.gate}:{identifier}",
                timestamp=timestamp,
            )
    else:
        reopen_target = policy.gate_specs[args.gate].get("reopen_to")
        current_stage = state.get("current_stage")
        if current_stage not in policy.stage_order:
            raise ResearchCtlError(f"state has unknown current_stage: {current_stage!r}")
        should_move_back = (
            args.gate != "release"
            and reopen_target in policy.stage_order
            and policy.stage_order.index(current_stage)
            > policy.stage_order.index(reopen_target)
        )
        if should_move_back:
            record_stage_transition(
                state,
                to_stage=reopen_target,
                trigger=f"gate-reopen:{args.gate}:{identifier}",
                timestamp=timestamp,
            )

    write_mutated_state(root, state)
    past_tense = {"approve": "approved", "reopen": "reopened"}[args.action]
    print(f"{past_tense} Gate {args.gate}: {identifier}")
    return 0

def cmd_checkpoint(root: Path, policy: Policy, args: argparse.Namespace) -> int:
    summary = args.summary.strip()
    if not summary:
        raise ResearchCtlError("checkpoint requires a non-empty --summary")
    state = load_state(root)
    require_compatible_state(state, policy)
    state_errors, _state_warnings = validate_state(
        root, state, policy, verify_artifact_integrity=False
    )
    if state_errors:
        preview = "; ".join(state_errors[:3])
        raise ResearchCtlError(
            f"state is invalid; run `researchctl doctor`: {preview}"
        )
    timestamp = next_state_timestamp(state)
    if args.stage is not None:
        current_stage = state.get("current_stage")
        if current_stage not in policy.stage_order:
            raise ResearchCtlError(f"state has unknown current_stage: {current_stage!r}")
        if args.stage not in policy.stage_order:
            raise ResearchCtlError(f"unknown target stage: {args.stage!r}")
        if args.stage != current_stage:
            requirements = transition_requirements(policy, current_stage, args.stage)
            gates = state.get("gates")
            if not isinstance(gates, dict):
                raise ResearchCtlError("state gates must be an object")
            for gate in requirements:
                record = gates.get(gate)
                if not isinstance(record, dict) or record.get("status") != "approved":
                    raise ResearchCtlError(
                        f"stage transition {current_stage}->{args.stage} requires "
                        f"approved Gate {gate}"
                    )
            record_stage_transition(
                state,
                to_stage=args.stage,
                trigger="checkpoint",
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
        legacy = root / LEGACY_RELATIVE_PATH
        if legacy.exists():
            _fields, notes = read_legacy_fields(legacy)
            print(
                f"[WARNING] found legacy {LEGACY_RELATIVE_PATH}; init will preserve it "
                "and will not migrate Gate approvals"
            )
            for note in notes:
                print(f"[WARNING] {note}")
        summary = (
            "doctor: 1 error(s), 1 or more warning(s)"
            if legacy.exists()
            else "doctor: 1 error(s), 0 warning(s)"
        )
        print(summary)
        return 1

    try:
        state = load_state(root)
    except ResearchCtlError as exc:
        print(f"[ERROR] {exc}")
        print("doctor: 1 error(s), 0 warning(s)")
        return 1
    errors, warnings = validate_state(root, state, policy)
    for error in errors:
        print(f"[ERROR] {error}")
    for warning in warnings:
        print(f"[WARNING] {warning}")
    if not errors:
        print("[OK] active state, Gate, stage, and memory contracts are valid")
    print(f"doctor: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0
