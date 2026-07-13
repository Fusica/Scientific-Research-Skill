#!/usr/bin/env python3
"""Deterministic project-local state management for research projects.

The command deliberately uses only the Python standard library.  The policy
file has a ``.yaml`` suffix for documentation tooling, but its contents are
JSON-compatible YAML and are therefore parsed with :mod:`json`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = PLUGIN_ROOT / "skills/research/references/policy.yaml"
DEFAULT_MEMORY_TEMPLATE = PLUGIN_ROOT / "skills/research/assets/memory.template.md"
RESEARCH_DIR = ".research"
STATE_RELATIVE_PATH = Path(RESEARCH_DIR) / "state.json"
MEMORY_RELATIVE_PATH = Path(RESEARCH_DIR) / "memory.md"
LEGACY_RELATIVE_PATH = Path(RESEARCH_DIR) / "project-state.yaml"

GATE_IDS = (
    "idea_freeze",
    "method_experiment_approval",
    "claim_freeze",
    "release",
)
GATE_STATUSES = {"pending", "approved", "reopened"}
GATE_ACTIONS = {"approve", "reopen"}
ARTIFACT_METADATA_FIELDS = ("artifact_id", "version", "content_hash", "status")
REQUIRED_STATE_FIELDS = {
    "schema_version",
    "workflow_version",
    "enabled",
    "project_id",
    "project_name",
    "current_stage",
    "gates",
    "artifacts",
    "last_checkpoint",
    "stage_history",
    "created_at",
    "updated_at",
}


class ResearchCtlError(RuntimeError):
    """An expected, user-actionable command failure."""


@dataclass(frozen=True)
class Policy:
    schema_version: Any
    workflow_version: str
    stage_order: tuple[str, ...]
    gate_order: tuple[str, ...]
    gate_specs: dict[str, dict[str, Any]]
    raw: dict[str, Any]


def utc_now() -> str:
    """Return a stable, timezone-explicit UTC timestamp."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def run_git(cwd: Path, *arguments: str) -> str | None:
    """Run a read-only Git query, returning stripped stdout on success."""

    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None


def find_project_root(start: Path | None = None) -> Path:
    """Resolve the target project, preferring the enclosing Git worktree."""

    current = (start or Path.cwd()).resolve()
    git_root = run_git(current, "rev-parse", "--show-toplevel")
    if git_root:
        return Path(git_root).resolve()

    for candidate in (current, *current.parents):
        if (candidate / STATE_RELATIVE_PATH).is_file():
            return candidate
        if (candidate / LEGACY_RELATIVE_PATH).is_file():
            return candidate
    return current


def policy_path() -> Path:
    """Return the canonical policy path, with an override for isolated tests."""

    override = os.environ.get("RESEARCHCTL_POLICY")
    return Path(override).expanduser().resolve() if override else DEFAULT_POLICY_PATH


def load_policy() -> Policy:
    path = policy_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ResearchCtlError(f"policy file not found: {path}") from exc
    except OSError as exc:
        raise ResearchCtlError(f"cannot read policy file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
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
        normalized_specs[gate] = spec

    return Policy(
        schema_version=schema_version,
        workflow_version=workflow_version,
        stage_order=tuple(stage_order),
        gate_order=tuple(gate_order),
        gate_specs=normalized_specs,
        raw=raw,
    )


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically replace a JSON file without exposing a partial state."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(value, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def load_state(root: Path) -> dict[str, Any]:
    path = root / STATE_RELATIVE_PATH
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ResearchCtlError(
            f"research project is not initialized at {root}; run `researchctl init`"
        ) from exc
    except OSError as exc:
        raise ResearchCtlError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ResearchCtlError(
            f"invalid state JSON at {path}:{exc.lineno}:{exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise ResearchCtlError(f"state root must be an object: {path}")
    return value


def require_compatible_state(state: dict[str, Any], policy: Policy) -> None:
    missing = REQUIRED_STATE_FIELDS - set(state)
    if missing:
        raise ResearchCtlError(
            "state is missing required fields: " + ", ".join(sorted(missing))
        )
    if state.get("schema_version") != policy.schema_version:
        raise ResearchCtlError(
            "state schema_version does not match policy; run `researchctl doctor`"
        )
    if state.get("workflow_version") != policy.workflow_version:
        raise ResearchCtlError(
            "state workflow_version does not match policy; run `researchctl doctor`"
        )


def default_memory(project_name: str) -> str:
    if DEFAULT_MEMORY_TEMPLATE.is_file():
        template = DEFAULT_MEMORY_TEMPLATE.read_text(encoding="utf-8")
        return template.replace("{{PROJECT_NAME}}", project_name)
    return (
        f"# Research Memory: {project_name}\n\n"
        "## Research Kernel\n\n"
        "- Problem:\n"
        "- Intended contribution:\n"
        "- Scope and constraints:\n\n"
        "## Verified Facts\n\n"
        "## Decisions and Rationale\n\n"
        "## Failed Attempts and Lessons\n\n"
        "## Open Questions\n\n"
        "## Next Checkpoint\n"
    )


def new_gate_state() -> dict[str, Any]:
    return {"status": "pending", "latest_decision_id": None, "history": []}


def new_state(root: Path, policy: Policy) -> dict[str, Any]:
    timestamp = utc_now()
    return {
        "schema_version": policy.schema_version,
        "workflow_version": policy.workflow_version,
        "enabled": True,
        "project_id": f"PROJECT-{uuid.uuid4().hex[:12].upper()}",
        "project_name": root.name,
        "current_stage": policy.stage_order[0],
        "gates": {gate: new_gate_state() for gate in GATE_IDS},
        "artifacts": {},
        "last_checkpoint": None,
        "stage_history": [],
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def record_stage_transition(
    state: dict[str, Any],
    *,
    to_stage: str,
    trigger: str,
    timestamp: str,
) -> None:
    stage_history = state.get("stage_history")
    if not isinstance(stage_history, list):
        raise ResearchCtlError("state stage_history must be a list")
    previous_stage = state.get("current_stage")
    stage_history.append(
        {
            "from_stage": previous_stage,
            "to_stage": to_stage,
            "trigger": trigger,
            "timestamp": timestamp,
        }
    )
    state["current_stage"] = to_stage


def parse_legacy_scalar(raw: str) -> Any:
    value = raw.strip()
    if not value:
        return ""
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    if re.fullmatch(r"-?[0-9]+", value):
        return int(value)
    return value.split(" #", 1)[0].strip()


def read_legacy_fields(path: Path) -> tuple[dict[str, Any], list[str]]:
    """Read only safe top-level legacy scalars; never import Gate approvals."""

    notes: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return {}, [f"could not parse legacy state: {exc}; legacy file was preserved"]

    fields: dict[str, Any] = {}
    for line in text.splitlines():
        match = re.fullmatch(
            r"(schema_version|project_id|title|project_name|current_stage|last_updated):\s*(.*)",
            line,
        )
        if match:
            fields[match.group(1)] = parse_legacy_scalar(match.group(2))
    if not fields:
        notes.append(
            "legacy state format was not safely parseable; defaults were used and the "
            "legacy file was preserved"
        )
    if re.search(r"^\s+status:\s*approved\s*(?:#.*)?$", text, re.MULTILINE):
        notes.append(
            "legacy Gate approvals were intentionally not migrated; approve them "
            "explicitly with researchctl"
        )
    return fields, notes


def migrate_legacy_state(
    root: Path, policy: Policy, legacy_path: Path
) -> tuple[dict[str, Any], list[str]]:
    state = new_state(root, policy)
    fields, notes = read_legacy_fields(legacy_path)
    project_id = fields.get("project_id")
    if isinstance(project_id, str) and project_id.strip():
        state["project_id"] = project_id.strip()
    project_name = fields.get("project_name") or fields.get("title")
    if isinstance(project_name, str) and project_name.strip():
        state["project_name"] = project_name.strip()

    legacy_stage = fields.get("current_stage")
    stage_aliases = {
        "intake": policy.stage_order[0],
        "experiment": "experiment_results",
        "result": "experiment_results",
    }
    candidate = stage_aliases.get(legacy_stage, legacy_stage)
    ungated_legacy_stages = {"idea", "literature"}
    if candidate in policy.stage_order and candidate in ungated_legacy_stages:
        if candidate != policy.stage_order[0]:
            record_stage_transition(
                state,
                to_stage=candidate,
                trigger="legacy-migration",
                timestamp=state["created_at"],
            )
        else:
            state["current_stage"] = candidate
    elif legacy_stage:
        notes.append(
            f"legacy stage {legacy_stage!r} was not migrated because its Gate "
            "approvals cannot be imported safely; "
            f"defaulted to {policy.stage_order[0]!r}"
        )
    notes.append(f"legacy state was retained at {legacy_path}")
    return state, notes


def git_exclude_path(root: Path) -> Path | None:
    value = run_git(root, "rev-parse", "--git-path", "info/exclude")
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else (root / path).resolve()


def ensure_local_git_exclude(root: Path) -> bool:
    """Ignore project memory in this clone without touching tracked files."""

    path = git_exclude_path(root)
    if path is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if any(line.strip() == ".research/" for line in existing.splitlines()):
        return False
    separator = "" if not existing or existing.endswith("\n") else "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{separator}.research/\n")
    return True


def cmd_init(root: Path, policy: Policy, _args: argparse.Namespace) -> int:
    state_path = root / STATE_RELATIVE_PATH
    memory_path = root / MEMORY_RELATIVE_PATH
    legacy_path = root / LEGACY_RELATIVE_PATH
    notes: list[str] = []

    if state_path.exists():
        state = load_state(root)
        require_compatible_state(state, policy)
        print(f"state already exists; left unchanged: {state_path}")
    else:
        if legacy_path.is_file():
            state, notes = migrate_legacy_state(root, policy, legacy_path)
        else:
            state = new_state(root, policy)
        atomic_write_json(state_path, state)
        print(f"created {state_path}")

    if memory_path.exists():
        print(f"memory already exists; left unchanged: {memory_path}")
    else:
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        memory_path.write_text(
            default_memory(str(state.get("project_name") or root.name)),
            encoding="utf-8",
        )
        print(f"created {memory_path}")

    if ensure_local_git_exclude(root):
        print("added .research/ to this clone's Git info/exclude")
    for note in notes:
        print(f"warning: {note}", file=sys.stderr)
    print(f"research workflow enabled for {state['project_id']}")
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


def write_mutated_state(root: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    atomic_write_json(root / STATE_RELATIVE_PATH, state)


def cmd_toggle(
    root: Path, policy: Policy, _args: argparse.Namespace, *, enabled: bool
) -> int:
    state = load_state(root)
    # Disabling is the emergency off switch after an incompatible plugin
    # update, so it must remain available even when doctor reports a version
    # mismatch. Re-enabling still requires a compatible state.
    if enabled:
        require_compatible_state(state, policy)
    if state.get("enabled") is enabled:
        print(f"research workflow already {'enabled' if enabled else 'disabled'}")
        return 0
    state["enabled"] = enabled
    write_mutated_state(root, state)
    print(f"research workflow {'enabled' if enabled else 'disabled'}")
    return 0


def command_actor() -> str:
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


def cmd_gate(root: Path, policy: Policy, args: argparse.Namespace) -> int:
    reason = args.reason.strip()
    if not reason:
        raise ResearchCtlError("Gate decisions require a non-empty --reason")
    state = load_state(root)
    require_compatible_state(state, policy)
    state_errors, _state_warnings = validate_state(root, state, policy)
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

    timestamp = utc_now()
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
    entry: dict[str, Any] = {
        "decision_id": identifier,
        "action": args.action,
        "previous_status": previous_status,
        "new_status": new_status,
        "reason": reason,
        "actor": command_actor(),
        "decided_at": timestamp,
        "artifact_refs": snapshot_artifact_refs(state.get("artifacts")),
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
        if args.gate != "release" and reopen_target != state.get("current_stage"):
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


def cmd_checkpoint(root: Path, policy: Policy, args: argparse.Namespace) -> int:
    summary = args.summary.strip()
    if not summary:
        raise ResearchCtlError("checkpoint requires a non-empty --summary")
    state = load_state(root)
    require_compatible_state(state, policy)
    timestamp = utc_now()
    if args.stage is not None:
        current_stage = state.get("current_stage")
        if current_stage not in policy.stage_order:
            raise ResearchCtlError(f"state has unknown current_stage: {current_stage!r}")
        if args.stage not in policy.stage_order:
            raise ResearchCtlError(f"unknown target stage: {args.stage!r}")
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


def valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def iter_artifact_pointers(
    value: Any, label: str = "artifacts"
) -> Iterable[tuple[str, str] | tuple[str, None]]:
    """Yield artifact paths; ``None`` marks a malformed non-empty pointer."""

    if value is None:
        return
    if isinstance(value, str):
        yield label, value
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_artifact_pointers(child, f"{label}[{index}]")
        return
    if isinstance(value, dict):
        if "path" in value:
            path = value["path"]
            if path is None:
                return
            if isinstance(path, str):
                yield f"{label}.path", path
            else:
                yield f"{label}.path", None
            return
        if any(field in value for field in ARTIFACT_METADATA_FIELDS):
            yield label, None
            return
        for key, child in value.items():
            yield from iter_artifact_pointers(child, f"{label}.{key}")
        return
    yield label, None


def snapshot_artifact_refs(value: Any, label: str = "artifacts") -> list[dict[str, Any]]:
    """Snapshot registered artifact pointers into a Gate decision record."""

    result: list[dict[str, Any]] = []
    if isinstance(value, str):
        result.append({"label": label, "path": value})
        return result
    if isinstance(value, list):
        for index, child in enumerate(value):
            result.extend(snapshot_artifact_refs(child, f"{label}[{index}]"))
        return result
    if not isinstance(value, dict):
        return result
    if "path" in value:
        if value.get("path") is None:
            return result
        reference: dict[str, Any] = {"label": label, "path": value.get("path")}
        for field in ARTIFACT_METADATA_FIELDS:
            if field in value:
                reference[field] = value[field]
        result.append(reference)
        return result
    for key, child in value.items():
        result.extend(snapshot_artifact_refs(child, f"{label}.{key}"))
    return result


def validate_state(
    root: Path, state: dict[str, Any], policy: Policy
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    missing = REQUIRED_STATE_FIELDS - set(state)
    if missing:
        errors.append("missing state fields: " + ", ".join(sorted(missing)))
    if state.get("schema_version") != policy.schema_version:
        errors.append(
            f"schema_version {state.get('schema_version')!r} does not match "
            f"policy {policy.schema_version!r}"
        )
    if state.get("workflow_version") != policy.workflow_version:
        errors.append(
            f"workflow_version {state.get('workflow_version')!r} does not match "
            f"policy {policy.workflow_version!r}"
        )
    if not isinstance(state.get("enabled"), bool):
        errors.append("enabled must be a boolean")
    for field in ("project_id", "project_name"):
        if not isinstance(state.get(field), str) or not state[field].strip():
            errors.append(f"{field} must be a non-empty string")
    if state.get("current_stage") not in policy.stage_order:
        errors.append(f"unknown current_stage: {state.get('current_stage')!r}")
    for field in ("created_at", "updated_at"):
        if not valid_timestamp(state.get(field)):
            errors.append(f"{field} must be an ISO-8601 timestamp")

    gates = state.get("gates")
    if not isinstance(gates, dict):
        errors.append("gates must be an object")
    else:
        missing_gates = set(GATE_IDS) - set(gates)
        extra_gates = set(gates) - set(GATE_IDS)
        if missing_gates:
            errors.append("missing Gates: " + ", ".join(sorted(missing_gates)))
        if extra_gates:
            errors.append("unknown Gates: " + ", ".join(sorted(extra_gates)))
        for gate in GATE_IDS:
            record = gates.get(gate)
            if not isinstance(record, dict):
                errors.append(f"Gate {gate} must be an object")
                continue
            status = record.get("status")
            history = record.get("history")
            latest = record.get("latest_decision_id")
            if status not in GATE_STATUSES:
                errors.append(f"Gate {gate} has invalid status {status!r}")
            if not isinstance(history, list):
                errors.append(f"Gate {gate} history must be a list")
                continue
            if not history:
                if latest is not None:
                    errors.append(
                        f"Gate {gate} latest_decision_id must be null without history"
                    )
                if status != "pending":
                    errors.append(f"Gate {gate} without history must be pending")
                continue
            seen_ids: set[str] = set()
            for index, decision in enumerate(history):
                prefix = f"Gate {gate} history[{index}]"
                if not isinstance(decision, dict):
                    errors.append(f"{prefix} must be an object")
                    continue
                required = {
                    "decision_id",
                    "action",
                    "previous_status",
                    "new_status",
                    "reason",
                    "actor",
                    "decided_at",
                    "artifact_refs",
                }
                absent = required - set(decision)
                if absent:
                    errors.append(
                        f"{prefix} missing fields: {', '.join(sorted(absent))}"
                    )
                    continue
                identifier = decision.get("decision_id")
                if not isinstance(identifier, str) or not identifier:
                    errors.append(f"{prefix} decision_id must be non-empty")
                elif identifier in seen_ids:
                    errors.append(f"{prefix} duplicates decision_id {identifier}")
                else:
                    seen_ids.add(identifier)
                if decision.get("action") not in GATE_ACTIONS:
                    errors.append(f"{prefix} has invalid action")
                if decision.get("previous_status") not in GATE_STATUSES:
                    errors.append(f"{prefix} has invalid previous_status")
                if decision.get("new_status") not in GATE_STATUSES:
                    errors.append(f"{prefix} has invalid new_status")
                if not isinstance(decision.get("reason"), str) or not decision[
                    "reason"
                ].strip():
                    errors.append(f"{prefix} reason must be non-empty")
                if not isinstance(decision.get("actor"), str) or not decision[
                    "actor"
                ].strip():
                    errors.append(f"{prefix} actor must be non-empty")
                if not valid_timestamp(decision.get("decided_at")):
                    errors.append(f"{prefix} decided_at must be an ISO timestamp")
                if not isinstance(decision.get("artifact_refs"), list):
                    errors.append(f"{prefix} artifact_refs must be a list")
                if gate == "release" and decision.get("action") == "approve":
                    allowed_targets = policy.gate_specs["release"].get(
                        "release_targets", []
                    )
                    if decision.get("release_target") not in allowed_targets:
                        errors.append(f"{prefix} has invalid or missing release_target")
            last = history[-1] if isinstance(history[-1], dict) else {}
            if latest != last.get("decision_id"):
                errors.append(
                    f"Gate {gate} latest_decision_id does not match its last history entry"
                )
            if status != last.get("new_status"):
                errors.append(f"Gate {gate} status does not match its last decision")

        current_stage = state.get("current_stage")
        if current_stage in policy.stage_order:
            current_index = policy.stage_order.index(current_stage)
            for gate in policy.gate_order:
                target = policy.gate_specs[gate].get("advance_to")
                record = gates.get(gate)
                release_stage_is_satisfied = False
                if gate == "release" and current_stage == "revision":
                    release_history = (
                        record.get("history") if isinstance(record, dict) else []
                    )
                    release_stage_is_satisfied = isinstance(
                        release_history, list
                    ) and any(
                        isinstance(decision, dict)
                        and decision.get("action") == "approve"
                        and decision.get("release_target") == "initial_submission"
                        for decision in release_history
                    )
                if (
                    target in policy.stage_order
                    and current_index >= policy.stage_order.index(target)
                    and isinstance(record, dict)
                    and record.get("status") != "approved"
                    and not release_stage_is_satisfied
                ):
                    errors.append(
                        f"current_stage {current_stage!r} requires approved Gate {gate}"
                    )

    artifacts = state.get("artifacts")
    if not isinstance(artifacts, (dict, list)):
        errors.append("artifacts must be an object or list")
    else:
        for label, pointer in iter_artifact_pointers(artifacts):
            if pointer is None:
                errors.append(f"{label} is not a valid artifact path")
                continue
            if not pointer.strip():
                errors.append(f"{label} is an empty artifact path")
                continue
            artifact_path = Path(pointer).expanduser()
            if not artifact_path.is_absolute():
                artifact_path = root / artifact_path
            if not artifact_path.exists():
                warnings.append(f"artifact pointer does not exist: {label} -> {pointer}")

    checkpoint = state.get("last_checkpoint")
    if checkpoint is not None:
        if not isinstance(checkpoint, dict):
            errors.append("last_checkpoint must be null or an object")
        else:
            if not isinstance(checkpoint.get("summary"), str) or not checkpoint[
                "summary"
            ].strip():
                errors.append("last_checkpoint summary must be non-empty")
            if not valid_timestamp(checkpoint.get("timestamp")):
                errors.append("last_checkpoint timestamp must be ISO-8601")
    stage_history = state.get("stage_history")
    if not isinstance(stage_history, list):
        errors.append("stage_history must be a list")
    else:
        expected_stage = policy.stage_order[0]
        for index, transition in enumerate(stage_history):
            prefix = f"stage_history[{index}]"
            if not isinstance(transition, dict):
                errors.append(f"{prefix} must be an object")
                continue
            required = {"from_stage", "to_stage", "trigger", "timestamp"}
            absent = required - set(transition)
            if absent:
                errors.append(
                    f"{prefix} missing fields: {', '.join(sorted(absent))}"
                )
                continue
            from_stage = transition.get("from_stage")
            to_stage = transition.get("to_stage")
            if from_stage != expected_stage:
                errors.append(
                    f"{prefix} starts at {from_stage!r}, expected {expected_stage!r}"
                )
            if from_stage not in policy.stage_order:
                errors.append(f"{prefix} has unknown from_stage {from_stage!r}")
            if to_stage not in policy.stage_order:
                errors.append(f"{prefix} has unknown to_stage {to_stage!r}")
            if from_stage in policy.stage_order and to_stage in policy.stage_order:
                try:
                    transition_requirements(policy, from_stage, to_stage)
                except ResearchCtlError as exc:
                    errors.append(f"{prefix}: {exc}")
            if not isinstance(transition.get("trigger"), str) or not transition[
                "trigger"
            ].strip():
                errors.append(f"{prefix} trigger must be non-empty")
            if not valid_timestamp(transition.get("timestamp")):
                errors.append(f"{prefix} timestamp must be ISO-8601")
            if to_stage in policy.stage_order:
                expected_stage = to_stage
        if expected_stage != state.get("current_stage"):
            errors.append(
                "current_stage does not match the final recorded stage transition"
            )
    if not (root / MEMORY_RELATIVE_PATH).is_file():
        errors.append(f"missing project memory: {MEMORY_RELATIVE_PATH}")
    exclude_path = git_exclude_path(root)
    if exclude_path is None:
        warnings.append(
            "Git worktree not detected; clone-local .research/ exclusion could not be verified"
        )
    else:
        try:
            exclude_lines = (
                exclude_path.read_text(encoding="utf-8").splitlines()
                if exclude_path.exists()
                else []
            )
        except OSError as exc:
            warnings.append(f"could not read Git info/exclude: {exc}")
        else:
            if not any(line.strip() == ".research/" for line in exclude_lines):
                warnings.append(
                    ".research/ is not present in this clone's Git info/exclude"
                )
        tracked_research = run_git(root, "ls-files", "--", ".research")
        if tracked_research:
            warnings.append(
                ".research/ contains tracked files; info/exclude does not untrack them"
            )
    if (root / LEGACY_RELATIVE_PATH).exists():
        warnings.append(
            f"legacy state retained at {LEGACY_RELATIVE_PATH}; state.json is authoritative"
        )
    return errors, warnings


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
        print("[OK] state, Gate history, stage, and memory contracts are valid")
    print(f"doctor: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="researchctl",
        description="Manage project-local Scientific Research Skill state.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="initialize and enable the current project")
    status = subparsers.add_parser("status", help="show current research state")
    status.add_argument("--json", action="store_true", help="emit raw state JSON")
    subparsers.add_parser("enable", help="enable hooks for this project")
    subparsers.add_parser("disable", help="disable hooks for this project")

    gate = subparsers.add_parser("gate", help="record an explicit Gate decision")
    gate.add_argument("action", choices=sorted(GATE_ACTIONS))
    gate.add_argument("gate", choices=GATE_IDS)
    gate.add_argument("--reason", required=True, help="non-empty decision rationale")

    checkpoint = subparsers.add_parser(
        "checkpoint", help="record a bounded resumption checkpoint"
    )
    checkpoint.add_argument("--summary", required=True, help="checkpoint summary")
    checkpoint.add_argument(
        "--stage",
        help="optionally move to a policy-allowed stage while recording the checkpoint",
    )
    subparsers.add_parser("doctor", help="validate project state and pointers")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        policy = load_policy()
        root = find_project_root()
        if args.command == "init":
            return cmd_init(root, policy, args)
        if args.command == "status":
            return cmd_status(root, policy, args)
        if args.command == "enable":
            return cmd_toggle(root, policy, args, enabled=True)
        if args.command == "disable":
            return cmd_toggle(root, policy, args, enabled=False)
        if args.command == "gate":
            return cmd_gate(root, policy, args)
        if args.command == "checkpoint":
            return cmd_checkpoint(root, policy, args)
        if args.command == "doctor":
            return cmd_doctor(root, policy, args)
    except ResearchCtlError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
