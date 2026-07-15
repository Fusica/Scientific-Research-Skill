"""Project discovery, state construction, locking, and atomic persistence."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .constants import (
    CLEAN_BREAK_REINIT_GUIDANCE,
    DEFAULT_MEMORY_TEMPLATE,
    LOCK_RELATIVE_PATH,
    LOCK_TIMEOUT_SECONDS,
    Policy,
    ResearchCtlError,
    STATE_RELATIVE_PATH,
)
from .gate_records import approval_targets_for_gate
from .jsonutil import (
    DuplicateJsonKeyError,
    NonStandardJsonConstantError,
    strict_json_loads,
)
from .timeutils import next_state_timestamp, utc_now


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
    return current

@contextmanager
def state_mutation_lock(root: Path, *, create: bool) -> Iterable[None]:
    """Serialize the complete state read-modify-write transaction across processes."""

    state_path = root / STATE_RELATIVE_PATH
    lock_path = root / LOCK_RELATIVE_PATH
    if not create and not state_path.is_file():
        raise ResearchCtlError(
            f"research project is not initialized at {root}; run `researchctl init`"
        )
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
    except OSError as exc:
        raise ResearchCtlError(f"cannot open project state lock {lock_path}: {exc}") from exc

    acquired = False
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    try:
        while not acquired:
            try:
                if os.name == "nt":  # pragma: no cover - platform-specific branch
                    import msvcrt

                    handle.seek(0, os.SEEK_END)
                    if handle.tell() == 0:
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (BlockingIOError, OSError) as exc:
                if time.monotonic() >= deadline:
                    raise ResearchCtlError(
                        f"timed out waiting for project state lock {lock_path}"
                    ) from exc
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            try:
                if os.name == "nt":  # pragma: no cover - platform-specific branch
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()

def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically and durably replace JSON without exposing a partial state."""

    temporary_name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
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
        if os.name != "nt":
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                # Atomic replacement already succeeded. Some filesystems do not
                # support directory fsync, so durability hardening is best-effort.
                pass
    except OSError as exc:
        raise ResearchCtlError(f"cannot atomically write {path}: {exc}") from exc
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass

def load_state(root: Path) -> dict[str, Any]:
    path = root / STATE_RELATIVE_PATH
    try:
        value = strict_json_loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ResearchCtlError(
            f"research project is not initialized at {root}; run `researchctl init`"
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise ResearchCtlError(f"cannot read {path}: {exc}") from exc
    except (DuplicateJsonKeyError, NonStandardJsonConstantError) as exc:
        raise ResearchCtlError(f"state contains {exc}: {path}") from exc
    except (json.JSONDecodeError, RecursionError) as exc:
        if isinstance(exc, RecursionError):
            raise ResearchCtlError(f"state JSON is nested too deeply: {path}") from exc
        raise ResearchCtlError(
            f"invalid state JSON at {path}:{exc.lineno}:{exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise ResearchCtlError(f"state root must be an object: {path}")
    return value

def require_compatible_state(state: dict[str, Any], policy: Policy) -> None:
    if state.get("schema_version") != policy.schema_version:
        raise ResearchCtlError(
            "unsupported state schema_version "
            f"{state.get('schema_version')!r}; v2 requires {policy.schema_version!r}; "
            f"{CLEAN_BREAK_REINIT_GUIDANCE}"
        )
    missing = set(policy.runtime.state_required_fields) - set(state)
    if missing:
        raise ResearchCtlError(
            "state is missing required fields: " + ", ".join(sorted(missing))
        )
    if state.get("workflow_version") != policy.workflow_version:
        raise ResearchCtlError(
            "state workflow_version does not match policy; run `researchctl doctor`"
        )

def default_memory(project_name: str) -> str:
    if DEFAULT_MEMORY_TEMPLATE.is_file():
        try:
            template = DEFAULT_MEMORY_TEMPLATE.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ResearchCtlError(
                f"cannot read memory template {DEFAULT_MEMORY_TEMPLATE}: {exc}"
            ) from exc
        return template.replace("{{PROJECT_NAME}}", project_name)
    return (
        f"# 研究记忆：{project_name}\n\n"
        "## 研究内核\n\n"
        "- 研究问题：\n"
        "- 当前假设或贡献：\n"
        "- 范围与边界条件：\n\n"
        "## 已验证事实\n\n"
        "## 决策及理由\n\n"
        "## 失败尝试与经验\n\n"
        "## 开放问题\n\n"
        "## 下一检查点\n"
    )

def new_gate_record(policy: Policy) -> dict[str, Any]:
    record = {"status": "pending", "latest_decision_id": None, "history": []}
    if set(record) != set(policy.runtime.gate_record_fields):
        raise ResearchCtlError("runtime contract does not match the Gate writer")
    return record


def new_gate_state(policy: Policy, gate: str) -> dict[str, Any]:
    targets = approval_targets_for_gate(policy, gate)
    if targets:
        container_field = policy.runtime.gate_target_container_fields[0]
        return {
            container_field: {
                target: new_gate_record(policy) for target in targets
            }
        }
    return new_gate_record(policy)


def new_state(root: Path, policy: Policy) -> dict[str, Any]:
    timestamp = utc_now()
    state = {
        "schema_version": policy.schema_version,
        "workflow_version": policy.workflow_version,
        "enabled": True,
        "project_id": f"PROJECT-{uuid.uuid4().hex[:12].upper()}",
        "project_name": root.name,
        "current_stage": policy.stage_order[0],
        "lifecycle": {
            "status": "active",
            "latest_decision_id": None,
            "history": [],
        },
        "activation_history": [],
        "gates": {gate: new_gate_state(policy, gate) for gate in policy.gate_order},
        "artifacts": {},
        "last_checkpoint": None,
        "stage_history": [],
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    if set(state) != set(policy.runtime.state_required_fields):
        raise ResearchCtlError("runtime contract does not match the state writer")
    return state


def validate_runtime_writer_contract(policy: Policy) -> None:
    """Fail policy loading before an incompatible v2 writer schema can be used."""

    # ``new_state`` is the authority for the emitted shape, including nested Gate
    # records.  Probe its nested containers too so ``init`` cannot succeed with a
    # state that the same runtime's ``doctor`` immediately rejects.
    state = new_state(Path("runtime-contract-validation"), policy)
    lifecycle = state.get("lifecycle")
    if not isinstance(lifecycle, dict) or set(lifecycle) != set(
        policy.runtime.lifecycle_record_fields
    ):
        raise ResearchCtlError(
            "runtime contract does not match the lifecycle record writer"
        )
    gates = state.get("gates")
    if not isinstance(gates, dict):
        raise ResearchCtlError("runtime contract does not match the Gate writer")
    expected_container_fields = set(policy.runtime.gate_target_container_fields)
    for gate in policy.gate_order:
        if not approval_targets_for_gate(policy, gate):
            continue
        container = gates.get(gate)
        if (
            not isinstance(container, dict)
            or set(container) != expected_container_fields
        ):
            raise ResearchCtlError(
                "runtime contract does not match the targeted Gate writer"
            )

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


def write_mutated_state(root: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = next_state_timestamp(state)
    atomic_write_json(root / STATE_RELATIVE_PATH, state)
