#!/usr/bin/env python3
"""Deterministic project-local state management for research projects.

The command deliberately uses only the Python standard library.  The policy
file has a ``.yaml`` suffix for documentation tooling, but its contents are
JSON-compatible YAML and are therefore parsed with :mod:`json`.
"""

from __future__ import annotations

import argparse
import hashlib
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
ARTIFACT_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
ARTIFACT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
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
    artifact_root: Path
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
    pointer_fields = state_contract.get("artifact_pointer_fields")
    expected_pointer_fields = ["path", *ARTIFACT_METADATA_FIELDS]
    if pointer_fields != expected_pointer_fields:
        raise ResearchCtlError(
            "policy artifact_pointer_fields must be exactly: "
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
    artifact_root = root / policy.artifact_root
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


def resolve_artifact_path(root: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ResearchCtlError(f"artifact file cannot be resolved: {value}: {exc}") from exc


def stored_artifact_path(root: Path, path: Path) -> tuple[str, bool]:
    """Return a portable relative path where possible and an external-path flag."""

    try:
        return path.relative_to(root.resolve()).as_posix(), False
    except ValueError:
        return str(path), True


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ResearchCtlError(f"cannot hash artifact file {path}: {exc}") from exc
    return f"sha256:{digest.hexdigest()}"


def is_research_control_file(root: Path, path: Path) -> bool:
    """Return whether ``path`` is workflow control metadata, not evidence."""

    control_files = (
        root / STATE_RELATIVE_PATH,
        root / MEMORY_RELATIVE_PATH,
        root / LEGACY_RELATIVE_PATH,
    )
    return any(path == candidate.resolve() for candidate in control_files)


def artifact_pointer_errors(
    root: Path,
    pointer: Any,
    label: str,
    *,
    verify_integrity: bool,
) -> list[str]:
    """Validate a canonical pointer; scientific adequacy remains outside this check."""

    errors: list[str] = []
    if not isinstance(pointer, dict):
        return [f"{label} must be a structured artifact pointer"]
    missing = {"path", *ARTIFACT_METADATA_FIELDS} - set(pointer)
    if missing:
        errors.append(f"{label} missing fields: {', '.join(sorted(missing))}")
        return errors
    path_value = pointer.get("path")
    artifact_id = pointer.get("artifact_id")
    version = pointer.get("version")
    content_hash = pointer.get("content_hash")
    status = pointer.get("status")
    if not isinstance(path_value, str) or not path_value.strip():
        errors.append(f"{label}.path must be a non-empty string")
    if not isinstance(artifact_id, str) or not ARTIFACT_ID_RE.fullmatch(artifact_id):
        errors.append(f"{label}.artifact_id has an invalid format")
    if (
        isinstance(version, bool)
        or not isinstance(version, (str, int))
        or not str(version).strip()
    ):
        errors.append(f"{label}.version must be a non-empty string or integer")
    if not isinstance(content_hash, str) or not SHA256_RE.fullmatch(content_hash):
        errors.append(f"{label}.content_hash must be sha256:<64 lowercase hex>")
    if not isinstance(status, str) or not status.strip():
        errors.append(f"{label}.status must be a non-empty string")
    if errors:
        return errors
    assert isinstance(path_value, str)
    unresolved_path = Path(path_value).expanduser()
    if not unresolved_path.is_absolute():
        unresolved_path = root / unresolved_path
    if is_research_control_file(root, unresolved_path.resolve(strict=False)):
        return [
            f"{label} points to research control metadata, which cannot be evidence: "
            f"{path_value}"
        ]
    if not verify_integrity:
        return errors
    try:
        path = resolve_artifact_path(root, path_value)
    except ResearchCtlError as exc:
        return [f"{label}: {exc}"]
    if not path.is_file():
        return [f"{label} must point to a regular file: {path_value}"]
    actual_hash = sha256_file(path)
    if actual_hash != content_hash:
        errors.append(
            f"{label} hash mismatch: registered {content_hash}, actual {actual_hash}"
        )
    return errors


def iter_gate_artifact_refs(state: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    gates = state.get("gates")
    if not isinstance(gates, dict):
        return
    for gate, record in gates.items():
        if not isinstance(record, dict) or not isinstance(record.get("history"), list):
            continue
        for index, decision in enumerate(record["history"]):
            if not isinstance(decision, dict) or not isinstance(
                decision.get("artifact_refs"), list
            ):
                continue
            for ref_index, reference in enumerate(decision["artifact_refs"]):
                if isinstance(reference, dict):
                    yield (
                        f"Gate {gate} history[{index}].artifact_refs[{ref_index}]",
                        reference,
                    )


def role_is_bound_by_approved_gate(
    state: dict[str, Any], policy: Policy, stage: str, role: str
) -> str | None:
    role_reference = f"{stage}.{role}"
    gates = state.get("gates")
    if not isinstance(gates, dict):
        return None
    for gate, record in gates.items():
        if not isinstance(record, dict) or record.get("status") != "approved":
            continue
        spec = policy.gate_specs.get(gate, {})
        if gate == "release":
            mapping = spec.get("required_artifact_roles_by_target", {})
            required_roles = (
                {
                    item
                    for roles in mapping.values()
                    if isinstance(roles, list)
                    for item in roles
                }
                if isinstance(mapping, dict)
                else set()
            )
        else:
            roles = spec.get("required_artifact_roles", [])
            required_roles = set(roles) if isinstance(roles, list) else set()
        if role_reference in required_roles:
            return str(gate)
    return None


def stash_legacy_artifact(
    artifacts: dict[str, Any], origin: str, value: Any
) -> None:
    """Preserve a conflicting legacy value outside the canonical stage namespace."""

    index = 1
    key = "_legacy"
    while key in artifacts:
        index += 1
        key = f"_legacy_{index}"
    artifacts[key] = {origin: value}


def prepare_artifact_bucket(
    root: Path, state: dict[str, Any], stage: str, role: str
) -> tuple[dict[str, Any], bool]:
    """Conservatively make room for a canonical artifact while retaining old data."""

    migrated = False
    raw_artifacts = state.get("artifacts")
    if isinstance(raw_artifacts, list):
        artifacts: dict[str, Any] = {}
        if raw_artifacts:
            stash_legacy_artifact(artifacts, "artifacts", raw_artifacts)
        state["artifacts"] = artifacts
        migrated = True
    elif isinstance(raw_artifacts, dict):
        artifacts = raw_artifacts
    else:
        raise ResearchCtlError("state artifacts must be an object or list")

    raw_stage = artifacts.get(stage)
    if raw_stage is None:
        stage_bucket: dict[str, Any] = {}
        artifacts[stage] = stage_bucket
    elif isinstance(raw_stage, dict) and "path" not in raw_stage:
        stage_bucket = raw_stage
    else:
        stash_legacy_artifact(artifacts, stage, raw_stage)
        stage_bucket = {}
        artifacts[stage] = stage_bucket
        migrated = True

    raw_role = stage_bucket.get(role)
    if raw_role is None:
        role_bucket: dict[str, Any] = {}
        stage_bucket[role] = role_bucket
    elif isinstance(raw_role, dict) and "path" in raw_role:
        legacy_id = raw_role.get("artifact_id")
        if (
            isinstance(legacy_id, str)
            and ARTIFACT_ID_RE.fullmatch(legacy_id)
            and not artifact_pointer_errors(
                root,
                raw_role,
                f"artifacts.{stage}.{role}",
                verify_integrity=True,
            )
        ):
            role_bucket = {legacy_id: raw_role}
            stage_bucket[role] = role_bucket
            migrated = True
        else:
            stash_legacy_artifact(artifacts, f"{stage}.{role}", raw_role)
            role_bucket = {}
            stage_bucket[role] = role_bucket
            migrated = True
    elif isinstance(raw_role, dict):
        role_bucket = raw_role
    else:
        stash_legacy_artifact(artifacts, f"{stage}.{role}", raw_role)
        role_bucket = {}
        stage_bucket[role] = role_bucket
        migrated = True
    return role_bucket, migrated


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

    for history_label, reference in iter_gate_artifact_refs(state):
        old_path = reference.get("path")
        old_hash = reference.get("content_hash")
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
        if not isinstance(role_bucket, dict) or not role_bucket or "path" in role_bucket:
            failures.append(f"missing required artifact role {role_reference}")
            continue
        role_references: list[dict[str, Any]] = []
        for key, pointer in sorted(role_bucket.items()):
            label = f"artifacts.{stage}.{role}.{key}"
            pointer_errors = artifact_pointer_errors(
                root, pointer, label, verify_integrity=True
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


def validate_artifact_tree(
    root: Path,
    value: Any,
    label: str,
    policy: Policy,
    errors: list[str],
    warnings: list[str],
    *,
    verify_integrity: bool,
) -> None:
    """Validate new canonical pointers strictly while retaining legacy readability."""

    if value is None:
        return
    if isinstance(value, str):
        if not value.strip():
            parts = label.split(".")
            canonical = (
                len(parts) >= 4
                and parts[0] == "artifacts"
                and parts[1] in policy.stage_order
            )
            message = f"{label} is an empty artifact path"
            (errors if canonical else warnings).append(
                message if canonical else f"legacy artifact pointer: {message}"
            )
            return
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        if not candidate.exists():
            warnings.append(f"artifact pointer does not exist: {label} -> {value}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            validate_artifact_tree(
                root,
                child,
                f"{label}[{index}]",
                policy,
                errors,
                warnings,
                verify_integrity=verify_integrity,
            )
        return
    if not isinstance(value, dict):
        parts = label.split(".")
        canonical = (
            len(parts) >= 4
            and parts[0] == "artifacts"
            and parts[1] in policy.stage_order
        )
        message = f"{label} is not a valid artifact path"
        (errors if canonical else warnings).append(
            message if canonical else f"legacy artifact pointer: {message}"
        )
        return
    if "path" in value:
        parts = label.split(".")
        canonical = (
            len(parts) >= 4
            and parts[0] == "artifacts"
            and parts[1] in policy.stage_order
        )
        pointer_errors = artifact_pointer_errors(
            root, value, label, verify_integrity=verify_integrity
        )
        if canonical:
            errors.extend(pointer_errors)
        elif pointer_errors:
            warnings.extend(
                f"legacy artifact pointer: {error}" for error in pointer_errors
            )
            path_value = value.get("path")
            if isinstance(path_value, str) and path_value.strip():
                candidate = Path(path_value).expanduser()
                if not candidate.is_absolute():
                    candidate = root / candidate
                if not candidate.exists():
                    warnings.append(
                        f"artifact pointer does not exist: {label} -> {path_value}"
                    )
        return
    if any(field in value for field in ARTIFACT_METADATA_FIELDS):
        parts = label.split(".")
        canonical = (
            len(parts) >= 4
            and parts[0] == "artifacts"
            and parts[1] in policy.stage_order
        )
        message = f"{label} is not a valid artifact path"
        (errors if canonical else warnings).append(
            message if canonical else f"legacy artifact pointer: {message}"
        )
        return
    for key, child in value.items():
        validate_artifact_tree(
            root,
            child,
            f"{label}.{key}",
            policy,
            errors,
            warnings,
            verify_integrity=verify_integrity,
        )


def validate_state(
    root: Path,
    state: dict[str, Any],
    policy: Policy,
    *,
    verify_artifact_integrity: bool = True,
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
        validate_artifact_tree(
            root,
            artifacts,
            "artifacts",
            policy,
            errors,
            warnings,
            verify_integrity=verify_artifact_integrity,
        )

    for history_label, reference in iter_gate_artifact_refs(state):
        structural_pointer_errors = artifact_pointer_errors(
            root,
            reference,
            history_label,
            verify_integrity=False,
        )
        if structural_pointer_errors:
            warnings.extend(
                f"historical Gate artifact reference is invalid: {error}"
                for error in structural_pointer_errors
            )
            continue
        if verify_artifact_integrity:
            integrity_errors = artifact_pointer_errors(
                root,
                reference,
                history_label,
                verify_integrity=True,
            )
            warnings.extend(
                f"historical Gate artifact is no longer verifiable: {error}"
                for error in integrity_errors
            )
    if isinstance(gates, dict):
        for gate, record in gates.items():
            history = record.get("history") if isinstance(record, dict) else None
            if not isinstance(history, list):
                continue
            for index, decision in enumerate(history):
                if (
                    isinstance(decision, dict)
                    and decision.get("action") == "approve"
                    and decision.get("artifact_refs") == []
                ):
                    warnings.append(
                        f"Gate {gate} history[{index}] predates required artifact binding"
                    )

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
        print("[OK] active state, Gate, stage, and memory contracts are valid")
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

    artifact = subparsers.add_parser(
        "artifact", help="register a versioned, hash-verified canonical artifact"
    )
    artifact_actions = artifact.add_subparsers(dest="artifact_action", required=True)
    register = artifact_actions.add_parser(
        "register", help="register or replace the current version of an artifact"
    )
    register.add_argument("role", help="lower_snake_case role within the stage")
    register.add_argument("--path", required=True, help="existing regular file path")
    register.add_argument("--artifact-id", required=True, help="stable artifact ID")
    register.add_argument("--version", required=True, help="artifact version")
    register.add_argument(
        "--status",
        default="current",
        help="descriptive lifecycle status, not Gate approval (default: current)",
    )
    register.add_argument(
        "--stage", help="producer stage; defaults to the current stage"
    )

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
        if args.command == "artifact":
            return cmd_artifact(root, policy, args)
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
