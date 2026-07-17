"""Artifact revision history, immutable snapshots, and registry validation."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .constants import (
    ARTIFACT_ID_RE,
    ARTIFACT_ROLE_RE,
    DASHBOARD_RELATIVE_PATH,
    LEGACY_RELATIVE_PATH,
    LOCK_RELATIVE_PATH,
    MAX_SNAPSHOT_BYTES,
    MEMORY_RELATIVE_PATH,
    Policy,
    ResearchCtlError,
    SHA256_RE,
    STATE_RELATIVE_PATH,
)
from .gate_records import iter_gate_records
from .policy import mutable_after_approval_roles
from .timeutils import valid_timestamp


@dataclass(frozen=True)
class SnapshotCreation:
    """Result of an immutable no-replace snapshot publication."""

    stored_path: str
    created: bool
    identity: tuple[int, int] | None


def resolve_artifact_path(root: Path, value: str) -> Path:
    try:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        return candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ResearchCtlError(f"artifact file cannot be resolved: {value}: {exc}") from exc


def stored_artifact_path(root: Path, path: Path) -> tuple[str, bool]:
    """Return a portable relative path where possible and an external-path flag."""

    try:
        return path.relative_to(root.resolve()).as_posix(), False
    except ValueError:
        return str(path), True


def _hash_open_regular(
    descriptor: int, path: Path, *, max_bytes: int | None = None
) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ResearchCtlError(f"artifact path must be a regular file: {path}")
        if max_bytes is not None and before.st_size > max_bytes:
            raise ResearchCtlError(
                f"artifact is {before.st_size} bytes; snapshot limit is "
                f"{max_bytes} bytes. Register a small manifest for large outputs"
            )
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            if max_bytes is not None and size + len(block) > max_bytes:
                raise ResearchCtlError(
                    f"artifact grew beyond the {max_bytes}-byte snapshot limit; "
                    "register a small manifest for large outputs"
                )
            digest.update(block)
            size += len(block)
        after = os.fstat(stream.fileno())
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after or size != after.st_size:
        raise ResearchCtlError(
            f"artifact file changed while it was being hashed: {path}; retry"
        )
    return f"sha256:{digest.hexdigest()}", size


def hash_file_with_size(
    path: Path, *, max_bytes: int | None = None
) -> tuple[str, int]:
    """Hash a stable regular file and return its digest and byte count."""

    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        return _hash_open_regular(descriptor, path, max_bytes=max_bytes)
    except ResearchCtlError:
        raise
    except OSError as exc:
        raise ResearchCtlError(f"cannot hash artifact file {path}: {exc}") from exc


def sha256_file(path: Path) -> str:
    return hash_file_with_size(path)[0]


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def resolved_workspace_root(root: Path, configured: Path, label: str) -> Path:
    """Resolve one configured workspace root without aliases or project escape."""

    project = root.resolve()
    unresolved = root / configured
    if unresolved.is_symlink():
        raise ResearchCtlError(f"{label} must not itself be a symlink: {configured}")
    try:
        resolved = unresolved.resolve(strict=False)
        resolved.relative_to(project)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ResearchCtlError(
            f"{label} escapes the project worktree: {configured}"
        ) from exc
    return resolved


def resolved_workspace_roots(root: Path, policy: Policy) -> tuple[Path, Path]:
    artifact_root = resolved_workspace_root(
        root, policy.artifact_root, "artifact workspace root"
    )
    snapshot_root = resolved_workspace_root(
        root, policy.snapshot_root, "snapshot workspace root"
    )
    if (
        artifact_root == snapshot_root
        or artifact_root in snapshot_root.parents
        or snapshot_root in artifact_root.parents
    ):
        raise ResearchCtlError(
            "artifact and snapshot workspace roots must be disjoint physical directories"
        )
    try:
        if artifact_root.exists() and snapshot_root.exists() and artifact_root.samefile(
            snapshot_root
        ):
            raise ResearchCtlError(
                "artifact and snapshot workspace roots must not be physical aliases"
            )
    except OSError as exc:
        raise ResearchCtlError(f"cannot verify workspace root identity: {exc}") from exc
    return artifact_root, snapshot_root


def resolved_snapshot_root(root: Path, policy: Policy) -> Path:
    """Resolve the configured snapshot root without allowing a symlink escape."""

    return resolved_workspace_roots(root, policy)[1]


def is_snapshot_path(root: Path, policy: Policy, path: Path) -> bool:
    return _path_within(path, resolved_snapshot_root(root, policy))


def is_research_control_file(root: Path, path: Path) -> bool:
    """Return whether ``path`` is workflow control metadata, not evidence."""

    control_files = (
        root / STATE_RELATIVE_PATH,
        root / LOCK_RELATIVE_PATH,
        root / MEMORY_RELATIVE_PATH,
        root / DASHBOARD_RELATIVE_PATH,
        root / LEGACY_RELATIVE_PATH,
    )
    for candidate in control_files:
        try:
            if path == candidate.resolve(strict=False):
                return True
            if path.exists() and candidate.exists() and path.samefile(candidate):
                return True
        except (OSError, RuntimeError, ValueError):
            continue
    return False


def _snapshot_artifact_component(artifact_id: str) -> str:
    visible = re.sub(r"[^A-Za-z0-9._-]+", "_", artifact_id).strip("._-")
    visible = (visible or "artifact")[:48]
    identity = hashlib.sha256(artifact_id.encode("utf-8")).hexdigest()[:12]
    return f"{visible}-{identity}"


def _snapshot_destination(
    root: Path,
    policy: Policy,
    *,
    stage: str,
    role: str,
    artifact_id: str,
    revision: int,
    content_hash: str,
    source: Path,
) -> Path:
    digest = content_hash.removeprefix("sha256:")
    suffix = source.suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,12}", source.suffix) else ".bin"
    destination = (
        resolved_snapshot_root(root, policy)
        / stage
        / role
        / _snapshot_artifact_component(artifact_id)
        / f"r{revision:06d}-{digest}{suffix}"
    )
    if not _path_within(destination, resolved_snapshot_root(root, policy)):
        raise ResearchCtlError("generated snapshot path escapes snapshot_root")
    return destination


def revision_snapshot_destination(
    root: Path,
    policy: Policy,
    *,
    source: Path,
    stage: str,
    role: str,
    artifact_id: str,
    revision: int,
    content_hash: str,
) -> Path:
    """Return the deterministic snapshot destination for one revision."""

    return _snapshot_destination(
        root,
        policy,
        source=source,
        stage=stage,
        role=role,
        artifact_id=artifact_id,
        revision=revision,
        content_hash=content_hash,
    )


@contextmanager
def _snapshot_parent_descriptor(
    root: Path, policy: Policy, destination: Path
) -> Iterable[int | None]:
    """Yield an anchored parent descriptor and durably create missing levels."""

    snapshot_root = resolved_snapshot_root(root, policy)
    relative = destination.parent.relative_to(snapshot_root)
    if os.name == "nt":  # pragma: no cover - Windows lacks the dir_fd contract
        cursor = snapshot_root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ResearchCtlError(
                    f"snapshot parent cannot be a symlink: {cursor}"
                )
            try:
                cursor.mkdir()
            except FileExistsError:
                if cursor.is_symlink() or not cursor.is_dir():
                    raise ResearchCtlError(
                        f"snapshot parent is not a directory: {cursor}"
                    )
        yield None
        return

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    current_fd: int | None = None
    try:
        current_fd = os.open(snapshot_root.resolve(strict=True), flags)
        if not stat.S_ISDIR(os.fstat(current_fd).st_mode):
            raise ResearchCtlError(
                f"snapshot root is not a directory: {snapshot_root}"
            )
        for part in relative.parts:
            try:
                child_fd = os.open(part, flags, dir_fd=current_fd)
            except FileNotFoundError:
                created = False
                try:
                    os.mkdir(part, 0o700, dir_fd=current_fd)
                    created = True
                except FileExistsError:
                    pass
                if created:
                    os.fsync(current_fd)
                child_fd = os.open(part, flags, dir_fd=current_fd)
            if not stat.S_ISDIR(os.fstat(child_fd).st_mode):
                os.close(child_fd)
                raise ResearchCtlError(
                    f"snapshot parent is not a directory: {destination.parent}"
                )
            os.close(current_fd)
            current_fd = child_fd
        yield current_fd
    except ResearchCtlError:
        raise
    except OSError as exc:
        raise ResearchCtlError(
            f"cannot safely create snapshot parent {destination.parent}: {exc}"
        ) from exc
    finally:
        if current_fd is not None:
            try:
                os.close(current_fd)
            except OSError:
                pass


def _snapshot_stat(parent_fd: int | None, destination: Path) -> os.stat_result:
    if parent_fd is None:  # pragma: no cover - Windows fallback
        return destination.lstat()
    return os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)


def _open_snapshot_path(parent_fd: int | None, destination: Path, flags: int) -> int:
    if parent_fd is None:  # pragma: no cover - Windows fallback
        return os.open(destination, flags, 0o600)
    return os.open(destination.name, flags, 0o600, dir_fd=parent_fd)


def _hash_snapshot_path(
    parent_fd: int | None, destination: Path
) -> tuple[str, int]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = _open_snapshot_path(parent_fd, destination, flags)
    return _hash_open_regular(descriptor, destination)


def create_revision_snapshot_result(
    root: Path,
    policy: Policy,
    *,
    source: Path,
    stage: str,
    role: str,
    artifact_id: str,
    revision: int,
    expected_hash: str,
    expected_size: int,
) -> SnapshotCreation:
    """Store a snapshot without replacement and report whether this call created it.

    The final path can be visible while its exclusively created inode is filled;
    state never references it until the completed bytes have been reverified.
    """

    if expected_size > MAX_SNAPSHOT_BYTES:
        raise ResearchCtlError(
            f"artifact is {expected_size} bytes; snapshot limit is "
            f"{MAX_SNAPSHOT_BYTES} bytes. Register a small manifest for large outputs"
        )
    destination = _snapshot_destination(
        root,
        policy,
        stage=stage,
        role=role,
        artifact_id=artifact_id,
        revision=revision,
        content_hash=expected_hash,
        source=source,
    )
    stored_destination, external = stored_artifact_path(root, destination)
    if external:
        raise ResearchCtlError("snapshot destination is outside the project")

    try:
        if not _path_within(destination.parent, resolved_snapshot_root(root, policy)):
            raise ResearchCtlError("snapshot directory escapes snapshot_root")
        with _snapshot_parent_descriptor(root, policy, destination) as parent_fd:
            try:
                existing_stat = _snapshot_stat(parent_fd, destination)
            except FileNotFoundError:
                existing_stat = None
            if existing_stat is not None:
                if not stat.S_ISREG(existing_stat.st_mode):
                    raise ResearchCtlError(
                        "snapshot destination is not a regular file: "
                        f"{stored_destination}"
                    )
                actual_hash, actual_size = _hash_snapshot_path(
                    parent_fd, destination
                )
                if (actual_hash, actual_size) != (expected_hash, expected_size):
                    raise ResearchCtlError(
                        "existing immutable snapshot conflicts with revision "
                        f"r{revision}: {stored_destination}"
                    )
                return SnapshotCreation(
                    stored_path=stored_destination,
                    created=False,
                    identity=None,
                )

            try:
                output_fd = _open_snapshot_path(
                    parent_fd,
                    destination,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                )
            except FileExistsError:
                existing_hash, existing_size = _hash_snapshot_path(
                    parent_fd, destination
                )
                if (existing_hash, existing_size) != (expected_hash, expected_size):
                    raise ResearchCtlError(
                        f"immutable snapshot appeared with conflicting content: "
                        f"{stored_destination}"
                    )
                return SnapshotCreation(
                    stored_path=stored_destination,
                    created=False,
                    identity=None,
                )
            output_stat = os.fstat(output_fd)
            created_identity = (output_stat.st_dev, output_stat.st_ino)
            try:
                source_fd = os.open(
                    source,
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0),
                )
            except OSError:
                os.close(output_fd)
                raise
            with (
                os.fdopen(source_fd, "rb") as input_stream,
                os.fdopen(output_fd, "wb") as output_stream,
            ):
                before = os.fstat(input_stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ResearchCtlError(
                        f"snapshot source must be a regular file: {source}"
                    )
                digest = hashlib.sha256()
                copied = 0
                while True:
                    block = input_stream.read(1024 * 1024)
                    if not block:
                        break
                    copied += len(block)
                    if copied > MAX_SNAPSHOT_BYTES:
                        raise ResearchCtlError(
                            "artifact grew beyond the snapshot limit; register a manifest"
                        )
                    output_stream.write(block)
                    digest.update(block)
                output_stream.flush()
                os.fsync(output_stream.fileno())
                after = os.fstat(input_stream.fileno())
            identity_before = (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            identity_after = (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            copied_hash = f"sha256:{digest.hexdigest()}"
            if identity_before != identity_after:
                raise ResearchCtlError(
                    f"artifact changed while its snapshot was being created: "
                    f"{source}; retry"
                )
            if (copied_hash, copied) != (expected_hash, expected_size):
                raise ResearchCtlError(
                    f"artifact changed before its snapshot was created: {source}; retry"
                )
            published_stat = _snapshot_stat(parent_fd, destination)
            if (published_stat.st_dev, published_stat.st_ino) != created_identity:
                raise ResearchCtlError(
                    "snapshot destination identity changed while being written"
                )
            if parent_fd is not None:
                os.fsync(parent_fd)
            actual_hash, actual_size = _hash_snapshot_path(parent_fd, destination)
            if (actual_hash, actual_size) != (expected_hash, expected_size):
                raise ResearchCtlError(
                    "snapshot verification failed before state update: "
                    f"{stored_destination}"
                )
    except ResearchCtlError:
        raise
    except OSError as exc:
        raise ResearchCtlError(f"cannot create immutable snapshot: {exc}") from exc
    return SnapshotCreation(
        stored_path=stored_destination,
        created=True,
        identity=created_identity,
    )


def create_revision_snapshot(
    root: Path,
    policy: Policy,
    *,
    source: Path,
    stage: str,
    role: str,
    artifact_id: str,
    revision: int,
    expected_hash: str,
    expected_size: int,
) -> str:
    """Compatibility wrapper returning the stored snapshot path."""

    return create_revision_snapshot_result(
        root,
        policy,
        source=source,
        stage=stage,
        role=role,
        artifact_id=artifact_id,
        revision=revision,
        expected_hash=expected_hash,
        expected_size=expected_size,
    ).stored_path


def current_artifact_revision(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    current = entry.get("current_revision")
    revisions = entry.get("revisions")
    if type(current) is not int or not isinstance(revisions, list):
        return None
    for revision in reversed(revisions):
        if isinstance(revision, dict) and revision.get("revision") == current:
            return revision
    return None


def artifact_reference(
    policy: Policy, label: str, artifact_id: str, revision: dict[str, Any]
) -> dict[str, Any]:
    reference = {"label": label, "artifact_id": artifact_id}
    reference.update(
        {
            field: revision.get(field)
            for field in policy.runtime.artifact_revision_fields
        }
    )
    return reference


def retained_artifact_reference(
    state: dict[str, Any], policy: Policy, reference: dict[str, Any]
) -> dict[str, Any] | None:
    """Resolve an immutable decision reference to its retained registry revision."""

    label = reference.get("label")
    if not isinstance(label, str):
        return None
    match = re.fullmatch(r"artifacts\.([^.]+)\.([^.]+)\.(.+)", label)
    if match is None or reference.get("artifact_id") != match.group(3):
        return None
    artifacts = state.get("artifacts")
    stage_bucket = artifacts.get(match.group(1)) if isinstance(artifacts, dict) else None
    role_bucket = (
        stage_bucket.get(match.group(2)) if isinstance(stage_bucket, dict) else None
    )
    entry = role_bucket.get(match.group(3)) if isinstance(role_bucket, dict) else None
    revisions = entry.get("revisions") if isinstance(entry, dict) else None
    if not isinstance(revisions, list):
        return None
    matches = [
        revision
        for revision in revisions
        if isinstance(revision, dict)
        and revision.get("revision") == reference.get("revision")
    ]
    if len(matches) != 1:
        return None
    return artifact_reference(policy, label, match.group(3), matches[0])


def iter_current_artifact_pointers(
    state: dict[str, Any], policy: Policy
) -> Iterable[tuple[str, dict[str, Any]]]:
    """Yield flattened references for each canonical current artifact revision."""

    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        return
    for stage in policy.stage_order:
        stage_bucket = artifacts.get(stage)
        if not isinstance(stage_bucket, dict):
            continue
        for role, role_bucket in stage_bucket.items():
            if not isinstance(role_bucket, dict):
                continue
            for artifact_id, entry in role_bucket.items():
                revision = current_artifact_revision(entry)
                if isinstance(artifact_id, str) and revision is not None:
                    label = f"artifacts.{stage}.{role}.{artifact_id}"
                    yield label, artifact_reference(
                        policy, label, artifact_id, revision
                    )


def iter_gate_artifact_refs(
    state: dict[str, Any], policy: Policy
) -> Iterable[tuple[str, dict[str, Any]]]:
    for gate, target, record in iter_gate_records(state, policy):
        history = record.get("history") if isinstance(record, dict) else None
        if not isinstance(history, list):
            continue
        gate_label = gate + (f"/{target}" if target is not None else "")
        for index, decision in enumerate(history):
            refs = decision.get("artifact_refs") if isinstance(decision, dict) else None
            if not isinstance(refs, list):
                continue
            for ref_index, reference in enumerate(refs):
                if isinstance(reference, dict):
                    yield (
                        f"Gate {gate_label} history[{index}].artifact_refs[{ref_index}]",
                        reference,
                    )


def latest_gate_approval(record: Any) -> dict[str, Any] | None:
    history = record.get("history") if isinstance(record, dict) else None
    if not isinstance(history, list):
        return None
    return next(
        (
            decision
            for decision in reversed(history)
            if isinstance(decision, dict) and decision.get("action") == "approve"
        ),
        None,
    )


def role_is_bound_by_approved_gate(
    state: dict[str, Any], policy: Policy, stage: str, role: str
) -> str | None:
    """Return the active Gate whose exact approval references this role."""

    prefix = f"artifacts.{stage}.{role}."
    for gate, target, record in iter_gate_records(state, policy):
        if not isinstance(record, dict) or record.get("status") != "approved":
            continue
        approval = latest_gate_approval(record)
        mutable_roles = mutable_after_approval_roles(
            policy,
            str(gate),
            target,
            approval.get("approval_mode") if isinstance(approval, dict) else None,
        )
        if f"{stage}.{role}" in mutable_roles:
            continue
        refs = approval.get("artifact_refs") if isinstance(approval, dict) else None
        if isinstance(refs, list) and any(
            isinstance(reference, dict)
            and isinstance(reference.get("label"), str)
            and reference["label"].startswith(prefix)
            for reference in refs
        ):
            return gate + (f"/{target}" if target is not None else "")
    return None


def _revision_structure_errors(
    policy: Policy, value: Any, label: str
) -> list[str]:
    if not isinstance(value, dict):
        return [f"{label} must be an artifact revision object"]
    errors: list[str] = []
    configured_fields = set(policy.runtime.artifact_revision_fields)
    missing = configured_fields - set(value)
    extra = set(value) - configured_fields
    if missing:
        errors.append(f"{label} missing fields: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"{label} has unknown fields: {', '.join(sorted(extra))}")
    if missing:
        return errors
    if type(value.get("revision")) is not int or value["revision"] <= 0:
        errors.append(f"{label}.revision must be a positive integer")
    for field in ("source_path", "snapshot_path"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            errors.append(f"{label}.{field} must be a non-empty string")
    if not isinstance(value.get("content_hash"), str) or not SHA256_RE.fullmatch(
        value["content_hash"]
    ):
        errors.append(f"{label}.content_hash must be sha256:<64 lowercase hex>")
    if type(value.get("size_bytes")) is not int or value["size_bytes"] < 0:
        errors.append(f"{label}.size_bytes must be a non-negative integer")
    if not valid_timestamp(value.get("registered_at")):
        errors.append(f"{label}.registered_at must be a timezone-explicit UTC timestamp")
    return errors


def verify_revision_files(
    root: Path,
    policy: Policy,
    revision: dict[str, Any],
    label: str,
    *,
    verify_source: bool,
    verify_snapshot: bool,
) -> list[str]:
    errors = _revision_structure_errors(policy, revision, label)
    if errors:
        return errors
    source_value = revision["source_path"]
    snapshot_value = revision["snapshot_path"]
    try:
        snapshot_unresolved = Path(snapshot_value).expanduser()
        if snapshot_unresolved.is_absolute():
            errors.append(f"{label}.snapshot_path must be project-relative")
        elif not _path_within(root / snapshot_unresolved, resolved_snapshot_root(root, policy)):
            errors.append(f"{label}.snapshot_path must stay under policy snapshot_root")
    except (OSError, RuntimeError, ValueError) as exc:
        errors.append(f"{label}.snapshot_path cannot be resolved: {exc}")
    if errors:
        return errors

    expected = (revision["content_hash"], revision["size_bytes"])
    if verify_source:
        try:
            source = resolve_artifact_path(root, source_value)
            if not source.is_file():
                raise ResearchCtlError(f"artifact path must be a regular file: {source}")
            if is_research_control_file(root, source) or is_snapshot_path(root, policy, source):
                raise ResearchCtlError("source path points to research control or snapshot data")
            actual = hash_file_with_size(source)
            if actual != expected:
                errors.append(
                    f"{label} source mismatch: registered {expected[0]} / {expected[1]} "
                    f"bytes, actual {actual[0]} / {actual[1]} bytes"
                )
        except ResearchCtlError as exc:
            errors.append(f"{label}: {exc}")
    if verify_snapshot:
        try:
            snapshot = resolve_artifact_path(root, snapshot_value)
            if not snapshot.is_file():
                raise ResearchCtlError(
                    f"snapshot path must be a regular file: {snapshot_value}"
                )
            actual = hash_file_with_size(snapshot)
            if actual != expected:
                errors.append(
                    f"{label} snapshot mismatch: registered {expected[0]} / "
                    f"{expected[1]} bytes, actual {actual[0]} / {actual[1]} bytes"
                )
        except ResearchCtlError as exc:
            errors.append(f"{label}: {exc}")
    return errors


def artifact_ref_errors(
    root: Path,
    policy: Policy,
    reference: Any,
    label: str,
    *,
    verify_source: bool,
    verify_snapshot: bool,
) -> list[str]:
    if not isinstance(reference, dict):
        return [f"{label} must be an artifact reference"]
    expected_fields = {
        *policy.runtime.artifact_reference_prefix_fields,
        *policy.runtime.artifact_revision_fields,
    }
    missing = expected_fields - set(reference)
    extra = set(reference) - expected_fields
    errors: list[str] = []
    if missing:
        errors.append(f"{label} missing fields: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"{label} has unknown fields: {', '.join(sorted(extra))}")
    if missing:
        return errors
    if not isinstance(reference.get("label"), str) or not reference["label"].strip():
        errors.append(f"{label}.label must be non-empty")
    artifact_id = reference.get("artifact_id")
    if not isinstance(artifact_id, str) or not ARTIFACT_ID_RE.fullmatch(artifact_id):
        errors.append(f"{label}.artifact_id has an invalid format")
    revision = {
        field: reference.get(field)
        for field in policy.runtime.artifact_revision_fields
    }
    errors.extend(
        verify_revision_files(
            root,
            policy,
            revision,
            label,
            verify_source=verify_source,
            verify_snapshot=verify_snapshot,
        )
    )
    return errors


def validate_artifact_registry(
    root: Path,
    artifacts: Any,
    _state: dict[str, Any],
    policy: Policy,
    errors: list[str],
    warnings: list[str],
    *,
    verify_integrity: bool,
) -> None:
    """Validate v2 ``artifacts[stage][role][artifact_id]`` revision histories."""

    if not isinstance(artifacts, dict):
        errors.append("artifacts must be an object using the v2 revision registry")
        return
    snapshot_owners: dict[str, str] = {}
    for stage, stage_bucket in artifacts.items():
        stage_label = f"artifacts.{stage}"
        if stage not in policy.stage_order:
            errors.append(f"{stage_label} uses an unknown stage")
            continue
        if not isinstance(stage_bucket, dict):
            errors.append(f"{stage_label} must be a role mapping")
            continue
        for role, role_bucket in stage_bucket.items():
            role_label = f"{stage_label}.{role}"
            if not isinstance(role, str) or not ARTIFACT_ROLE_RE.fullmatch(role):
                errors.append(f"{role_label} role must use lower_snake_case")
                continue
            if not isinstance(role_bucket, dict):
                errors.append(f"{role_label} must be an artifact-ID mapping")
                continue
            if len(role_bucket) != 1:
                errors.append(
                    f"{role_label} must contain exactly one canonical artifact; "
                    f"found {len(role_bucket)}"
                )
            for artifact_id, entry in role_bucket.items():
                entry_label = f"{role_label}.{artifact_id}"
                if not isinstance(artifact_id, str) or not ARTIFACT_ID_RE.fullmatch(
                    artifact_id
                ):
                    errors.append(f"{entry_label} has an invalid artifact ID")
                    continue
                if not isinstance(entry, dict):
                    errors.append(f"{entry_label} must be an artifact entry")
                    continue
                configured_entry_fields = set(policy.runtime.artifact_entry_fields)
                missing = configured_entry_fields - set(entry)
                extra = set(entry) - configured_entry_fields
                if missing:
                    errors.append(
                        f"{entry_label} missing fields: {', '.join(sorted(missing))}"
                    )
                if extra:
                    errors.append(
                        f"{entry_label} has unknown fields: {', '.join(sorted(extra))}"
                    )
                if missing:
                    continue
                current = entry.get("current_revision")
                revisions = entry.get("revisions")
                if type(current) is not int or current <= 0:
                    errors.append(
                        f"{entry_label}.current_revision must be a positive integer"
                    )
                if not isinstance(revisions, list) or not revisions:
                    errors.append(f"{entry_label}.revisions must be a non-empty list")
                    continue
                expected_numbers = list(range(1, len(revisions) + 1))
                actual_numbers = [
                    revision.get("revision") if isinstance(revision, dict) else None
                    for revision in revisions
                ]
                if actual_numbers != expected_numbers:
                    errors.append(
                        f"{entry_label}.revisions must be contiguous and ordered from 1"
                    )
                if current != len(revisions):
                    errors.append(
                        f"{entry_label}.current_revision must identify the final revision"
                    )
                for index, revision in enumerate(revisions):
                    revision_label = f"{entry_label}.revisions[{index}]"
                    revision_errors = verify_revision_files(
                        root,
                        policy,
                        revision if isinstance(revision, dict) else {},
                        revision_label,
                        verify_source=False,
                        verify_snapshot=verify_integrity,
                    )
                    errors.extend(revision_errors)
                    if (
                        verify_integrity
                        and index == len(revisions) - 1
                        and not revision_errors
                    ):
                        source_problems = verify_revision_files(
                            root,
                            policy,
                            revision if isinstance(revision, dict) else {},
                            revision_label,
                            verify_source=True,
                            verify_snapshot=False,
                        )
                        warnings.extend(
                            "artifact live source is dirty or unavailable; immutable "
                            f"snapshot remains authoritative: {problem}"
                            for problem in source_problems
                        )
                    if isinstance(revision, dict) and isinstance(
                        revision.get("snapshot_path"), str
                    ):
                        snapshot_path = revision["snapshot_path"]
                        prior = snapshot_owners.setdefault(snapshot_path, revision_label)
                        if prior != revision_label:
                            errors.append(
                                f"{revision_label}.snapshot_path duplicates immutable "
                                f"snapshot owned by {prior}"
                            )
