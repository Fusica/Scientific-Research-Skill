"""No-clobber batch publication for the shipped Reference Stack.

The command owns the narrow transaction seam between external command output and
canonical project evidence. It validates the complete batch before publication,
creates attempt-scoped final files and immutable snapshots with exclusive opens,
and advances state once. A failure never deletes a published path: unregistered
files remain observable orphans until an explicit reconciliation confirms that
canonical state does not reference them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .artifacts import (
    artifact_reference,
    create_revision_snapshot_result,
    current_artifact_revision,
    hash_file_with_size,
    role_is_bound_by_approved_gate,
    validate_artifact_registry,
    verify_revision_files,
)
from .constants import (
    ARTIFACT_ID_RE,
    ARTIFACT_ROLE_RE,
    MAX_SNAPSHOT_BYTES,
    Policy,
    ResearchCtlError,
    SHA256_RE,
)
from .doctor import validate_state
from .jsonutil import (
    DuplicateJsonKeyError,
    NonStandardJsonConstantError,
    strict_json_loads,
)
from .store import load_state, require_compatible_state, write_mutated_state
from .timeutils import next_state_timestamp


PUBLISH_SCHEMA_VERSION = "1.0"
MAX_PUBLICATIONS = 202
MAX_PUBLICATION_MANIFEST_BYTES = 4 * 1024 * 1024
PUBLICATION_FIELDS = {
    "source_path",
    "publish_path",
    "role",
    "artifact_id",
    "expected_content_hash",
    "expected_size_bytes",
}


@dataclass
class Publication:
    source: Path
    source_identity: tuple[int, int]
    source_parent_identities: tuple[tuple[Path, int, int], ...]
    publish_path: str
    destination: Path
    role: str
    artifact_id: str
    content_hash: str
    size_bytes: int
    existing_revision: dict[str, Any] | None
    entry: dict[str, Any] | None
    next_revision: int | None
    snapshot: Path | None = None
    parent_fd: int | None = None


def _load_manifest(path: Path) -> dict[str, Any]:
    chunks: list[bytes] = []
    size = 0
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ResearchCtlError(
                    f"publication manifest must be a regular file: {path}"
                )
            if before.st_size > MAX_PUBLICATION_MANIFEST_BYTES:
                raise ResearchCtlError(
                    "publication manifest exceeds the "
                    f"{MAX_PUBLICATION_MANIFEST_BYTES}-byte limit"
                )
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(block)
                if size > MAX_PUBLICATION_MANIFEST_BYTES:
                    raise ResearchCtlError(
                        "publication manifest exceeds the "
                        f"{MAX_PUBLICATION_MANIFEST_BYTES}-byte limit"
                    )
                chunks.append(block)
            after = os.fstat(stream.fileno())
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if before_identity != after_identity or size != after.st_size:
            raise ResearchCtlError(
                f"publication manifest changed while being read: {path}"
            )
        value = strict_json_loads(b"".join(chunks).decode("utf-8"))
    except FileNotFoundError as exc:
        raise ResearchCtlError(f"publication manifest not found: {path}") from exc
    except ResearchCtlError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ResearchCtlError(f"cannot read publication manifest {path}: {exc}") from exc
    except (DuplicateJsonKeyError, NonStandardJsonConstantError) as exc:
        raise ResearchCtlError(f"publication manifest contains {exc}: {path}") from exc
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ResearchCtlError(f"invalid publication manifest JSON {path}: {exc}") from exc
    if not isinstance(value, dict) or set(value) != {"schema_version", "publications"}:
        raise ResearchCtlError(
            "publication manifest must contain exactly schema_version and publications"
        )
    if value.get("schema_version") != PUBLISH_SCHEMA_VERSION:
        raise ResearchCtlError(
            f"publication manifest schema_version must be {PUBLISH_SCHEMA_VERSION!r}"
        )
    return value


def _safe_publish_path(
    root: Path,
    policy: Policy,
    stage: str,
    attempt_id: str,
    value: Any,
) -> tuple[str, Path]:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ResearchCtlError("publish_path must be a non-empty POSIX relative path")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.as_posix() != value
    ):
        raise ResearchCtlError("publish_path must be a normalized POSIX relative path")
    prefix = (
        PurePosixPath(policy.artifact_root.as_posix())
        / stage
        / "reference-stack"
        / attempt_id
    )
    if relative.parts[: len(prefix.parts)] != prefix.parts or len(relative.parts) < len(
        prefix.parts
    ) + 1:
        raise ResearchCtlError(
            "Reference Stack publish_path must be attempt-scoped under "
            f"{prefix.as_posix()}/"
        )
    destination = root.joinpath(*relative.parts)
    cursor = root.resolve()
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ResearchCtlError(f"publish_path cannot traverse a symlink: {value}")
    try:
        destination.resolve(strict=False).relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise ResearchCtlError(f"publish_path escapes the project: {value}") from exc
    return value, destination


def _source_path(
    root: Path,
    value: Any,
) -> tuple[Path, tuple[int, int], tuple[tuple[Path, int, int], ...]]:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ResearchCtlError("source_path must name a non-empty regular file")
    unresolved = Path(value).expanduser()
    source = Path(os.path.abspath(unresolved))
    physical_root = root.resolve()
    for ancestor in source.parents:
        try:
            ancestor_stat = ancestor.lstat()
            same_project_root = (
                stat.S_ISDIR(ancestor_stat.st_mode)
                and not stat.S_ISLNK(ancestor_stat.st_mode)
                and os.path.samefile(ancestor, physical_root)
            )
        except OSError:
            continue
        if same_project_root:
            project_relative = source.relative_to(ancestor)
            source = physical_root.joinpath(*project_relative.parts)
            break
    parents: list[tuple[Path, int, int]] = []
    try:
        chain = list(reversed(source.parents[:-1]))
        for parent in chain:
            parent_stat = parent.lstat()
            if stat.S_ISLNK(parent_stat.st_mode):
                raise ResearchCtlError(
                    f"publication source cannot traverse a symlink: {value}"
                )
            if not stat.S_ISDIR(parent_stat.st_mode):
                raise ResearchCtlError(
                    f"publication source parent is not a directory: {parent}"
                )
            parents.append((parent, parent_stat.st_dev, parent_stat.st_ino))
        source_stat = source.lstat()
    except ResearchCtlError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise ResearchCtlError(
            f"publication source cannot be inspected: {value}: {exc}"
        ) from exc
    if not stat.S_ISREG(source_stat.st_mode):
        raise ResearchCtlError(f"publication source must be a regular file: {value}")
    return (
        source,
        (source_stat.st_dev, source_stat.st_ino),
        tuple(parents),
    )


def _verify_source_topology(publication: Publication) -> None:
    for parent, expected_device, expected_inode in publication.source_parent_identities:
        try:
            current = parent.lstat()
        except OSError as exc:
            raise ResearchCtlError(
                f"publication source parent changed before copy: {parent}: {exc}"
            ) from exc
        if (
            not stat.S_ISDIR(current.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or (current.st_dev, current.st_ino)
            != (expected_device, expected_inode)
        ):
            raise ResearchCtlError(
                f"publication source topology changed before copy: {parent}"
            )


def _role_bucket(state: dict[str, Any], stage: str, role: str) -> dict[str, Any]:
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


def _validate_state_for_publication(
    root: Path, policy: Policy
) -> dict[str, Any]:
    state = load_state(root)
    require_compatible_state(state, policy)
    lifecycle = state.get("lifecycle")
    status = lifecycle.get("status") if isinstance(lifecycle, dict) else None
    if status != "active":
        raise ResearchCtlError(
            f"project lifecycle is {status!r}; reopen it before research mutations"
        )
    structural_errors, _warnings = validate_state(
        root, state, policy, verify_artifact_integrity=False
    )
    if structural_errors:
        raise ResearchCtlError(
            "state is invalid; run `researchctl doctor`: "
            + "; ".join(structural_errors[:3])
        )
    snapshot_errors: list[str] = []
    ignored_source_warnings: list[str] = []
    validate_artifact_registry(
        root,
        state.get("artifacts"),
        state,
        policy,
        snapshot_errors,
        ignored_source_warnings,
        verify_integrity=True,
    )
    if snapshot_errors:
        raise ResearchCtlError(
            "state is invalid; run `researchctl doctor`: "
            + "; ".join(snapshot_errors[:3])
        )
    return state


def _preflight(
    root: Path,
    policy: Policy,
    state: dict[str, Any],
    stage: str,
    attempt_id: str,
    manifest: dict[str, Any],
) -> list[Publication]:
    raw_items = manifest.get("publications")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= MAX_PUBLICATIONS:
        raise ResearchCtlError(
            f"publication manifest must contain 1 to {MAX_PUBLICATIONS} publications"
        )
    seen_paths: set[str] = set()
    seen_roles: set[str] = set()
    publications: list[Publication] = []
    reserved_roles = {
        policy.runtime.scientific_record_artifact_role,
        policy.runtime.adapter_exchange_artifact_role,
    }
    for index, raw in enumerate(raw_items):
        label = f"publications[{index}]"
        if not isinstance(raw, dict) or set(raw) != PUBLICATION_FIELDS:
            raise ResearchCtlError(
                f"{label} must contain exactly "
                + ", ".join(sorted(PUBLICATION_FIELDS))
            )
        role = raw.get("role")
        artifact_id = raw.get("artifact_id")
        if not isinstance(role, str) or ARTIFACT_ROLE_RE.fullmatch(role) is None:
            raise ResearchCtlError(f"{label}.role must use lower_snake_case")
        if role in reserved_roles:
            raise ResearchCtlError(
                f"{label}.role {role!r} requires its semantic append command"
            )
        if not isinstance(artifact_id, str) or ARTIFACT_ID_RE.fullmatch(artifact_id) is None:
            raise ResearchCtlError(f"{label}.artifact_id contains unsupported characters")
        publish_path, destination = _safe_publish_path(
            root, policy, stage, attempt_id, raw.get("publish_path")
        )
        if publish_path in seen_paths or role in seen_roles:
            raise ResearchCtlError("publication paths and roles must be unique within a batch")
        seen_paths.add(publish_path)
        seen_roles.add(role)
        source, source_identity, source_parents = _source_path(
            root,
            raw.get("source_path")
        )
        content_hash, size_bytes = hash_file_with_size(
            source, max_bytes=MAX_SNAPSHOT_BYTES
        )
        expected_hash = raw.get("expected_content_hash")
        expected_size = raw.get("expected_size_bytes")
        if (
            not isinstance(expected_hash, str)
            or SHA256_RE.fullmatch(expected_hash) is None
            or type(expected_size) is not int
            or not 0 <= expected_size <= MAX_SNAPSHOT_BYTES
        ):
            raise ResearchCtlError(f"{label} expected hash or size is invalid")
        if (content_hash, size_bytes) != (expected_hash, expected_size):
            raise ResearchCtlError(
                f"{label} source no longer matches its observed hash or size"
            )
        frozen_by = role_is_bound_by_approved_gate(state, policy, stage, role)
        if frozen_by is not None:
            raise ResearchCtlError(
                f"artifact role {stage}.{role} is bound by approved Gate {frozen_by}; "
                "reopen that Gate before registering another revision"
            )
        bucket = _role_bucket(state, stage, role)
        existing = bucket.get(artifact_id)
        if existing is None and bucket:
            existing_ids = ", ".join(sorted(str(item) for item in bucket))
            raise ResearchCtlError(
                f"artifact role {stage}.{role} already has its one canonical artifact "
                f"({existing_ids}); reuse that stable artifact ID for the next revision"
            )
        current = current_artifact_revision(existing)
        if existing is not None and current is None:
            raise ResearchCtlError(
                f"artifact entry {stage}.{role}.{artifact_id} is invalid; run doctor"
            )
        idempotent = current is not None and (
            current.get("source_path") == publish_path
            and current.get("content_hash") == content_hash
            and current.get("size_bytes") == size_bytes
        )
        if destination.exists() or destination.is_symlink():
            if not idempotent or destination.is_symlink() or not destination.is_file():
                raise ResearchCtlError(
                    "Reference Stack publication cannot overwrite an existing project path: "
                    f"{publish_path}"
                )
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
        elif idempotent:
            raise ResearchCtlError(
                f"registered Reference Stack source is missing: {publish_path}"
            )
        publications.append(
            Publication(
                source=source,
                source_identity=source_identity,
                source_parent_identities=source_parents,
                publish_path=publish_path,
                destination=destination,
                role=role,
                artifact_id=artifact_id,
                content_hash=content_hash,
                size_bytes=size_bytes,
                existing_revision=current if idempotent else None,
                entry=existing,
                next_revision=(
                    None if idempotent else 1 if current is None else int(current["revision"]) + 1
                ),
            )
        )
    return publications


def _open_publication_parent(root: Path, destination: Path) -> int | None:
    """Open/create the parent below ``root`` without following path races.

    Every newly created directory entry is fsynced before the next level is
    created. The returned POSIX descriptor anchors the final no-replace open.
    """

    relative = destination.parent.relative_to(root)
    if os.name == "nt":  # pragma: no cover - Windows lacks the dir_fd contract
        cursor = root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ResearchCtlError(
                    f"publication parent cannot be a symlink: {cursor}"
                )
            try:
                cursor.mkdir()
            except FileExistsError:
                if cursor.is_symlink() or not cursor.is_dir():
                    raise ResearchCtlError(
                        f"publication parent appeared with an unsafe type: {cursor}"
                    )
        return None

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    current_fd: int | None = None
    try:
        current_fd = os.open(root.resolve(strict=True), flags)
        if not stat.S_ISDIR(os.fstat(current_fd).st_mode):
            raise ResearchCtlError(f"project root is not a directory: {root}")
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
            child_stat = os.fstat(child_fd)
            if not stat.S_ISDIR(child_stat.st_mode):
                os.close(child_fd)
                raise ResearchCtlError(
                    f"publication parent is not a directory: {destination.parent}"
                )
            os.close(current_fd)
            current_fd = child_fd
        return current_fd
    except ResearchCtlError:
        if current_fd is not None:
            os.close(current_fd)
        raise
    except OSError as exc:
        if current_fd is not None:
            os.close(current_fd)
        raise ResearchCtlError(
            f"cannot safely create publication parent {destination.parent}: {exc}"
        ) from exc


def _copy_to_destination(root: Path, publication: Publication) -> None:
    output_fd: int | None = None
    try:
        _verify_source_topology(publication)
        if os.name != "nt":
            publication.parent_fd = _open_publication_parent(
                root, publication.destination
            )
            assert publication.parent_fd is not None
            output_fd = os.open(
                publication.destination.name,
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=publication.parent_fd,
            )
        else:  # pragma: no cover - Windows fallback lacks dir_fd publication
            _open_publication_parent(root, publication.destination)
            output_fd = os.open(
                publication.destination,
                os.O_RDWR | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        destination_identity = os.fstat(output_fd)
        with os.fdopen(output_fd, "w+b") as output:
            output_fd = None
            flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            source_fd = os.open(publication.source, flags)
            try:
                before = os.fstat(source_fd)
                if not stat.S_ISREG(before.st_mode):
                    raise ResearchCtlError(
                        f"publication source must remain a regular file: {publication.source}"
                    )
                if (before.st_dev, before.st_ino) != publication.source_identity:
                    raise ResearchCtlError(
                        f"publication source changed after preflight: {publication.source}"
                    )
                copied = 0
                with os.fdopen(source_fd, "rb", closefd=False) as source:
                    while True:
                        block = source.read(1024 * 1024)
                        if not block:
                            break
                        copied += len(block)
                        if copied > MAX_SNAPSHOT_BYTES:
                            raise ResearchCtlError(
                                "publication source grew beyond the snapshot limit"
                            )
                        output.write(block)
                after = os.fstat(source_fd)
            finally:
                os.close(source_fd)
            output.flush()
            os.fsync(output.fileno())
            output.seek(0)
            destination_digest = hashlib.sha256()
            destination_size = 0
            while True:
                block = output.read(1024 * 1024)
                if not block:
                    break
                destination_digest.update(block)
                destination_size += len(block)
            destination_after = os.fstat(output.fileno())
            # Keep this fd open through the path check so an unlink/recreate
            # cannot recycle the just-published inode and masquerade as it.
            _verify_source_topology(publication)
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
            if identity_before != identity_after:
                raise ResearchCtlError(
                    f"publication source changed while being copied: {publication.source}"
                )
            destination_hash = f"sha256:{destination_digest.hexdigest()}"
            if (
                (destination_after.st_dev, destination_after.st_ino)
                != (destination_identity.st_dev, destination_identity.st_ino)
                or destination_after.st_size != destination_size
                or (destination_hash, destination_size)
                != (
                    publication.content_hash,
                    publication.size_bytes,
                )
            ):
                raise ResearchCtlError(
                    f"publication source changed after preflight: {publication.source}"
                )
            published = (
                os.stat(
                    publication.destination.name,
                    dir_fd=publication.parent_fd,
                    follow_symlinks=False,
                )
                if publication.parent_fd is not None and os.name != "nt"
                else publication.destination.stat(follow_symlinks=False)
            )
            if (
                not stat.S_ISREG(published.st_mode)
                or (published.st_dev, published.st_ino)
                != (destination_identity.st_dev, destination_identity.st_ino)
                or published.st_size != destination_size
            ):
                raise ResearchCtlError(
                    f"publication destination identity changed while being filled: "
                    f"{publication.destination}"
                )
            if publication.parent_fd is not None:
                os.fsync(publication.parent_fd)
    except FileExistsError as exc:
        raise ResearchCtlError(
            "Reference Stack publication cannot overwrite an existing project path: "
            f"{publication.publish_path}"
        ) from exc
    except ResearchCtlError:
        raise
    except OSError as exc:
        raise ResearchCtlError(
            f"cannot publish Reference Stack output {publication.publish_path}: {exc}"
        ) from exc
    finally:
        if output_fd is not None:
            try:
                os.close(output_fd)
            except OSError:
                pass


def _cleanup(publications: list[Publication]) -> None:
    for publication in reversed(publications):
        if publication.parent_fd is not None:
            try:
                os.close(publication.parent_fd)
            except OSError:
                pass
            publication.parent_fd = None


def cmd_publish_batch(root: Path, policy: Policy, args: argparse.Namespace) -> int:
    """Publish and register a complete Reference Stack artifact set once."""

    state = _validate_state_for_publication(root, policy)
    stage = args.stage or state.get("current_stage")
    if stage not in policy.stage_order:
        raise ResearchCtlError(f"unknown artifact stage: {stage!r}")
    attempt_id = args.attempt_id.strip()
    if ARTIFACT_ID_RE.fullmatch(attempt_id) is None:
        raise ResearchCtlError("attempt ID contains unsupported characters")
    manifest = _load_manifest(Path(args.manifest).expanduser())
    publications = _preflight(root, policy, state, stage, attempt_id, manifest)
    try:
        for publication in publications:
            if publication.existing_revision is not None:
                continue
            _copy_to_destination(root, publication)

        for publication in publications:
            if publication.existing_revision is not None:
                continue
            assert publication.next_revision is not None
            snapshot_result = create_revision_snapshot_result(
                root,
                policy,
                source=publication.destination,
                stage=stage,
                role=publication.role,
                artifact_id=publication.artifact_id,
                revision=publication.next_revision,
                expected_hash=publication.content_hash,
                expected_size=publication.size_bytes,
            )
            publication.snapshot = root / snapshot_result.stored_path

        results: list[dict[str, Any]] = []
        for publication in publications:
            if publication.existing_revision is not None:
                revision = publication.existing_revision
                result = "already_registered"
            else:
                assert publication.next_revision is not None
                assert publication.snapshot is not None
                revision = {
                    "revision": publication.next_revision,
                    "source_path": publication.publish_path,
                    "snapshot_path": publication.snapshot.relative_to(root).as_posix(),
                    "content_hash": publication.content_hash,
                    "size_bytes": publication.size_bytes,
                    "registered_at": next_state_timestamp(state),
                }
                final_errors = verify_revision_files(
                    root,
                    policy,
                    revision,
                    (
                        f"artifacts.{stage}.{publication.role}."
                        f"{publication.artifact_id}.revisions[{publication.next_revision - 1}]"
                    ),
                    verify_source=True,
                    verify_snapshot=True,
                )
                if final_errors:
                    raise ResearchCtlError("; ".join(final_errors))
                bucket = _role_bucket(state, stage, publication.role)
                if publication.entry is None:
                    bucket[publication.artifact_id] = {
                        "current_revision": publication.next_revision,
                        "revisions": [revision],
                    }
                else:
                    revisions = publication.entry.get("revisions")
                    if not isinstance(revisions, list):
                        raise ResearchCtlError("artifact revision history is invalid")
                    revisions.append(revision)
                    publication.entry["current_revision"] = publication.next_revision
                result = "registered"
            reference = artifact_reference(
                policy,
                f"artifacts.{stage}.{publication.role}.{publication.artifact_id}",
                publication.artifact_id,
                revision,
            )
            results.append(
                {
                    "result": result,
                    "role": publication.role,
                    "artifact_id": publication.artifact_id,
                    "artifact_ref": reference,
                }
            )
        if any(item["result"] == "registered" for item in results):
            write_mutated_state(root, state)
    finally:
        # Never unlink final or snapshot paths after failure: POSIX has no
        # portable conditional unlink, so stat-then-unlink could delete a
        # concurrent replacement. Unregistered attempt-scoped files are safer
        # orphans and require explicit reconciliation.
        _cleanup(publications)

    payload = {
        "output_schema_version": PUBLISH_SCHEMA_VERSION,
        "result": "published",
        "stage": stage,
        "publications": results,
        "artifact_refs": [item["artifact_ref"] for item in results],
    }
    if bool(getattr(args, "json", False)):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"published and registered {len(results)} Reference Stack artifacts "
            f"for {stage}"
        )
    return 0
