"""Atomic append commands for the two registered append-only manifests."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from argparse import Namespace
from pathlib import Path
from typing import Any, Callable

from .artifacts import hash_file_with_size
from .commands import cmd_artifact
from .constants import MAX_SNAPSHOT_BYTES, Policy, ResearchCtlError
from .jsonutil import (
    DuplicateJsonKeyError,
    NonStandardJsonConstantError,
    strict_json_loads,
)
from .store import load_state, require_compatible_state


def _read_regular_bytes(path: Path, label: str) -> bytes:
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
                raise ResearchCtlError(f"{label} must be a regular file: {path}")
            if before.st_size > MAX_SNAPSHOT_BYTES:
                raise ResearchCtlError(
                    f"{label} exceeds the {MAX_SNAPSHOT_BYTES}-byte limit: {path}"
                )
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(block)
                if size > MAX_SNAPSHOT_BYTES:
                    raise ResearchCtlError(
                        f"{label} exceeds the {MAX_SNAPSHOT_BYTES}-byte limit: {path}"
                    )
                chunks.append(block)
            after = os.fstat(stream.fileno())
        visible = path.lstat()
    except FileNotFoundError as exc:
        raise ResearchCtlError(f"{label} file not found: {path}") from exc
    except ResearchCtlError:
        raise
    except OSError as exc:
        raise ResearchCtlError(f"cannot read {label} {path}: {exc}") from exc
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
    if (
        before_identity != after_identity
        or size != after.st_size
        or (visible.st_dev, visible.st_ino) != (after.st_dev, after.st_ino)
    ):
        raise ResearchCtlError(f"{label} changed while being read: {path}")
    return b"".join(chunks)


def _parse_object(payload: bytes, path: Path, label: str) -> dict[str, Any]:
    try:
        value = strict_json_loads(payload.decode("utf-8"))
    except (OSError, UnicodeError) as exc:
        raise ResearchCtlError(f"cannot read {label} {path}: {exc}") from exc
    except (DuplicateJsonKeyError, NonStandardJsonConstantError) as exc:
        raise ResearchCtlError(f"{label} contains {exc}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ResearchCtlError(
            f"{label} must be JSON: {path}:{exc.lineno}:{exc.colno}: {exc.msg}"
        ) from exc
    except RecursionError as exc:
        raise ResearchCtlError(f"{label} JSON is nested too deeply: {path}") from exc
    if not isinstance(value, dict):
        raise ResearchCtlError(f"{label} must contain one JSON object")
    return value


def _load_object_with_bytes(
    path: Path, label: str
) -> tuple[dict[str, Any], bytes]:
    payload = _read_regular_bytes(path, label)
    return _parse_object(payload, path, label), payload


def _load_object(path: Path, label: str) -> dict[str, Any]:
    return _load_object_with_bytes(path, label)[0]


def _target_path(root: Path, raw: str) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise ResearchCtlError(
            "atomic manifest append requires a project-local working path"
        ) from exc
    if candidate.is_symlink() or resolved.is_symlink():
        raise ResearchCtlError("atomic manifest append does not accept a symlink path")
    return resolved


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary_name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    except OSError as exc:
        raise ResearchCtlError(f"cannot atomically update manifest {path}: {exc}") from exc
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass


def _registered_entry(
    state: dict[str, Any], stage: str, role: str, artifact_id: str
) -> dict[str, Any] | None:
    artifacts = state.get("artifacts")
    stage_bucket = artifacts.get(stage) if isinstance(artifacts, dict) else None
    role_bucket = stage_bucket.get(role) if isinstance(stage_bucket, dict) else None
    entry = role_bucket.get(artifact_id) if isinstance(role_bucket, dict) else None
    return entry if isinstance(entry, dict) else None


def _assert_current_source(
    root: Path,
    entry: dict[str, Any],
    target: Path,
    *,
    stage: str,
    role: str,
    artifact_id: str,
) -> None:
    revisions = entry.get("revisions")
    current_number = entry.get("current_revision")
    if (
        not isinstance(revisions, list)
        or not revisions
        or type(current_number) is not int
        or current_number != len(revisions)
        or not isinstance(revisions[-1], dict)
    ):
        raise ResearchCtlError(
            f"registered manifest {stage}.{role}.{artifact_id} is invalid; run doctor"
        )
    current = revisions[-1]
    source_value = current.get("source_path")
    if not isinstance(source_value, str):
        raise ResearchCtlError("registered manifest source_path is invalid")
    source = Path(source_value).expanduser()
    if not source.is_absolute():
        source = root / source
    try:
        source = source.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ResearchCtlError(f"registered manifest source is unavailable: {exc}") from exc
    if source != target:
        raise ResearchCtlError(
            "manifest working path must match the current registered source_path"
        )
    actual_hash, actual_size = hash_file_with_size(source)
    if (
        actual_hash != current.get("content_hash")
        or actual_size != current.get("size_bytes")
    ):
        raise ResearchCtlError(
            "manifest working source changed outside researchctl; reconcile it before append"
        )


def _append_and_register(
    root: Path,
    policy: Policy,
    args: Namespace,
    *,
    role: str,
    collection: str,
    item_path: str,
    item_label: str,
    create_manifest: Callable[[dict[str, Any]], dict[str, Any]],
    require_existing: bool,
) -> int:
    state = load_state(root)
    require_compatible_state(state, policy)
    stage = args.stage
    if stage not in policy.stage_order:
        raise ResearchCtlError(f"unknown manifest stage: {stage!r}")
    artifact_id = args.artifact_id.strip()
    target = _target_path(root, args.path)
    item = _load_object(Path(item_path).expanduser().resolve(), item_label)
    entry = _registered_entry(state, stage, role, artifact_id)
    try:
        target.lstat()
        existed = True
    except FileNotFoundError:
        existed = False
    except OSError as exc:
        raise ResearchCtlError(f"cannot inspect manifest {target}: {exc}") from exc
    if existed:
        manifest = _load_object_with_bytes(target, "manifest")[0]
    else:
        manifest = create_manifest(item)

    if entry is not None:
        _assert_current_source(
            root,
            entry,
            target,
            stage=stage,
            role=role,
            artifact_id=artifact_id,
        )
    elif require_existing:
        raise ResearchCtlError(
            f"{item_label} append requires an existing registered {stage}.{role} manifest"
        )

    values = manifest.get(collection)
    if not isinstance(values, list):
        raise ResearchCtlError(f"manifest.{collection} must be a list")
    if not existed:
        # The initializer includes the first item so it can establish every
        # required top-level field in one place.
        pass
    else:
        values.append(item)
    encoded = (
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    _atomic_write(target, encoded)
    # Never unlink or restore a path after registration failure. Portable POSIX
    # has no inode-conditional unlink/replace, so cleanup could delete or replace
    # an unrelated concurrent file. The dirty working manifest remains visible
    # for explicit reconciliation while canonical state and prior snapshots stay
    # authoritative.
    return cmd_artifact(
        root,
        policy,
        Namespace(
            stage=stage,
            role=role,
            artifact_id=artifact_id,
            path=str(target),
            json=bool(getattr(args, "json", False)),
        ),
    )


def cmd_record_append(root: Path, policy: Policy, args: Namespace) -> int:
    item_path = args.record
    return _append_and_register(
        root,
        policy,
        args,
        role=policy.runtime.scientific_record_artifact_role,
        collection="records",
        item_path=item_path,
        item_label="record",
        create_manifest=lambda item: {
            "schema_version": policy.runtime.scientific_record_manifest_schema_version,
            "stage": args.stage,
            "records": [item],
        },
        require_existing=False,
    )


def cmd_adapter_append(root: Path, policy: Policy, args: Namespace) -> int:
    is_request = args.adapter_action == "request-append"
    collection = "requests" if is_request else "receipts"
    item_path = args.request if is_request else args.receipt
    item_label = "adapter request" if is_request else "adapter receipt"

    def create_manifest(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": policy.runtime.adapter_exchange_manifest_schema_version,
            "stage": args.stage,
            "requests": [item] if is_request else [],
            "receipts": [] if is_request else [item],
        }

    return _append_and_register(
        root,
        policy,
        args,
        role=policy.runtime.adapter_exchange_artifact_role,
        collection=collection,
        item_path=item_path,
        item_label=item_label,
        create_manifest=create_manifest,
        require_existing=not is_request,
    )
