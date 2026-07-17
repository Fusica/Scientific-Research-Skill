#!/usr/bin/env python3
"""Reference isolated-command Adapter over the public researchctl protocol.

This executable is deliberately outside Core.  It never imports the state writer:
all request verification, attempt journaling, artifact registration, and receipt
updates go through ``scripts/researchctl.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

if __package__:
    from .researchctl_core.jsonutil import (
        DuplicateJsonKeyError,
        NonStandardJsonConstantError,
        strict_json_loads,
    )
else:
    from researchctl_core.jsonutil import (
        DuplicateJsonKeyError,
        NonStandardJsonConstantError,
        strict_json_loads,
    )


SCHEMA_VERSION = "1.0"
ADAPTER_ID = "scientific-research-reference-isolated-command"
ADAPTER_VERSION = "1.0.0"
PROTOCOL_VERSION = "1.0"
PAYLOAD_LOCATOR = "#reference-stack-v1"
MAX_MATERIALS = 100
MAX_OUTPUTS = 100
MAX_STEPS = 20
MAX_TOOL_PROBES = 20
MAX_TIMEOUT_SECONDS = 24 * 60 * 60
MAX_LOG_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024
ROLE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
FORBIDDEN_PUBLISH_PATHS = {
    PurePosixPath(".research/state.json"),
    PurePosixPath(".research/state.lock"),
    PurePosixPath(".research/memory.md"),
    PurePosixPath(".research/dashboard.html"),
}


class ReferenceStackError(RuntimeError):
    """Expected, user-actionable Reference Stack failure."""


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = strict_json_loads(
            _read_regular_bytes(path, label=label, maximum=MAX_JSON_BYTES).decode(
                "utf-8"
            )
        )
    except FileNotFoundError as exc:
        raise ReferenceStackError(f"{label} file not found: {path}") from exc
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        DuplicateJsonKeyError,
        NonStandardJsonConstantError,
        RecursionError,
    ) as exc:
        raise ReferenceStackError(f"invalid {label} JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReferenceStackError(f"{label} must contain one JSON object")
    return value


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
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
                raise ReferenceStackError(
                    f"Reference Stack source must be a regular file: {path}"
                )
            if before.st_size > MAX_LOG_BYTES:
                raise ReferenceStackError(
                    f"Reference Stack source exceeds {MAX_LOG_BYTES} bytes: {path}"
                )
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
                if size > MAX_LOG_BYTES:
                    raise ReferenceStackError(
                        f"Reference Stack source exceeds {MAX_LOG_BYTES} bytes: {path}"
                    )
            after = os.fstat(stream.fileno())
    except ReferenceStackError:
        raise
    except OSError as exc:
        raise ReferenceStackError(f"cannot hash {path}: {exc}") from exc
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
        raise ReferenceStackError(f"Reference Stack source changed while hashing: {path}")
    return f"sha256:{digest.hexdigest()}", size


def _read_regular_bytes(path: Path, *, label: str, maximum: int) -> bytes:
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
                raise ReferenceStackError(f"{label} must be a regular file")
            if before.st_size > maximum:
                raise ReferenceStackError(f"{label} exceeds {maximum} bytes")
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(block)
                if size > maximum:
                    raise ReferenceStackError(f"{label} exceeds {maximum} bytes")
                chunks.append(block)
            after = os.fstat(stream.fileno())
    except ReferenceStackError:
        raise
    except FileNotFoundError as exc:
        raise ReferenceStackError(f"{label} file not found: {path}") from exc
    except OSError as exc:
        raise ReferenceStackError(f"cannot read {label} {path}: {exc}") from exc
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
        raise ReferenceStackError(f"{label} changed while being read")
    return b"".join(chunks)


def _strict_fields(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReferenceStackError(f"{label} must be an object")
    missing = expected - set(value)
    extra = set(value) - expected
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if extra:
            details.append("unknown " + ", ".join(sorted(extra)))
        raise ReferenceStackError(f"{label} fields are invalid: {'; '.join(details)}")
    return value


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ReferenceStackError(f"{label} must be a non-empty POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReferenceStackError(f"{label} must be a normalized relative path")
    if path.as_posix() != value:
        raise ReferenceStackError(f"{label} must use canonical POSIX spelling")
    return path


def _project_path(root: Path, value: Any, label: str) -> Path:
    relative = _safe_relative(value, label)
    if relative in FORBIDDEN_PUBLISH_PATHS or (
        len(relative.parts) >= 2
        and relative.parts[:2] == (".research", "snapshots")
    ):
        raise ReferenceStackError(f"{label} cannot target research control metadata")
    candidate = root.joinpath(*relative.parts)
    try:
        resolved_root = root.resolve()
        cursor = resolved_root
        for part in relative.parts[:-1]:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ReferenceStackError(f"{label} cannot traverse a symlink")
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(resolved_root)
    except ReferenceStackError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise ReferenceStackError(f"cannot resolve {label}: {exc}") from exc
    if candidate.is_symlink():
        raise ReferenceStackError(f"{label} cannot be a symlink")
    return resolved


def _sandbox_path(root: Path, value: Any, label: str) -> Path:
    relative = _safe_relative(value, label)
    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*relative.parts)
    try:
        cursor = resolved_root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ReferenceStackError(f"{label} cannot traverse a symlink")
        candidate.resolve(strict=False).relative_to(resolved_root)
    except ReferenceStackError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise ReferenceStackError(f"cannot resolve {label}: {exc}") from exc
    return candidate


def _validate_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or IDENTIFIER_RE.fullmatch(value) is None:
        raise ReferenceStackError(f"{label} must be a stable identifier")
    return value


def _validate_role(value: Any, label: str) -> str:
    if not isinstance(value, str) or ROLE_RE.fullmatch(value) is None:
        raise ReferenceStackError(f"{label} must use lower_snake_case")
    return value


def _validate_argv(value: Any, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 256
        or not all(isinstance(item, str) and item and "\x00" not in item for item in value)
    ):
        raise ReferenceStackError(f"{label} must be a non-empty string argv list")
    return list(value)


def _validate_timeout(value: Any, label: str) -> int:
    if type(value) is not int or not 1 <= value <= MAX_TIMEOUT_SECONDS:
        raise ReferenceStackError(
            f"{label} must be an integer from 1 to {MAX_TIMEOUT_SECONDS}"
        )
    return value


def _artifact_spec(value: Any, label: str, *, expected: bool) -> dict[str, Any]:
    fields = {"publish_path", "role", "artifact_id"}
    if expected:
        fields |= {"source_path", "classification"}
    spec = _strict_fields(value, fields, label)
    _safe_relative(spec["publish_path"], f"{label}.publish_path")
    _validate_role(spec["role"], f"{label}.role")
    _validate_identifier(spec["artifact_id"], f"{label}.artifact_id")
    if expected:
        _safe_relative(spec["source_path"], f"{label}.source_path")
        if spec["classification"] not in {"output", "log"}:
            raise ReferenceStackError(
                f"{label}.classification must be 'output' or 'log'"
            )
    return spec


def _artifact_ref(value: Any, label: str) -> dict[str, Any]:
    expected = {
        "label",
        "artifact_id",
        "revision",
        "source_path",
        "snapshot_path",
        "content_hash",
        "size_bytes",
        "registered_at",
    }
    reference = _strict_fields(value, expected, label)
    if not isinstance(reference["label"], str) or not reference["label"].startswith(
        "artifacts."
    ):
        raise ReferenceStackError(f"{label}.label is invalid")
    _validate_identifier(reference["artifact_id"], f"{label}.artifact_id")
    if type(reference["revision"]) is not int or reference["revision"] < 1:
        raise ReferenceStackError(f"{label}.revision must be a positive integer")
    if (
        not isinstance(reference["content_hash"], str)
        or SHA256_RE.fullmatch(reference["content_hash"]) is None
        or type(reference["size_bytes"]) is not int
        or reference["size_bytes"] < 0
    ):
        raise ReferenceStackError(f"{label} hash or size is invalid")
    return reference


def _validate_config(
    value: dict[str, Any],
    request: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    fields = {
        "schema_version",
        "adapter_kind",
        "operation_kind",
        "working_directory",
        "environment",
        "network",
        "materials",
        "tool_probes",
        "steps",
        "expected_outputs",
        "log_artifact",
        "result_artifact",
    }
    config = _strict_fields(value, fields, "Reference Stack payload")
    if config["schema_version"] != SCHEMA_VERSION:
        raise ReferenceStackError("unsupported Reference Stack payload schema")
    if config["adapter_kind"] != "isolated_command":
        raise ReferenceStackError("adapter_kind must be 'isolated_command'")
    if config["operation_kind"] != request.get("operation_kind"):
        raise ReferenceStackError(
            "payload operation_kind must equal the registered Adapter Request"
        )
    if config["operation_kind"] not in {"experiment_execution", "paper_production"}:
        raise ReferenceStackError(
            "Reference isolated-command Adapter supports experiment_execution "
            "and paper_production only"
        )
    _safe_relative(config["working_directory"], "working_directory")
    if config["network"] not in {"declared_disabled", "declared_required"}:
        raise ReferenceStackError(
            "network must be declared_disabled or declared_required"
        )

    environment = _strict_fields(
        config["environment"], {"inherit", "set"}, "environment"
    )
    inherited = environment["inherit"]
    if (
        not isinstance(inherited, list)
        or len(inherited) != len(set(inherited))
        or not all(
            isinstance(item, str) and ENV_NAME_RE.fullmatch(item) is not None
            for item in inherited
        )
    ):
        raise ReferenceStackError("environment.inherit must be a unique variable list")
    assigned = environment["set"]
    if not isinstance(assigned, dict) or not all(
        isinstance(key, str)
        and ENV_NAME_RE.fullmatch(key) is not None
        and isinstance(item, str)
        and "\x00" not in item
        for key, item in assigned.items()
    ):
        raise ReferenceStackError("environment.set must map variable names to strings")
    if set(inherited) & set(assigned):
        raise ReferenceStackError("environment variables cannot be inherited and set")

    input_refs = request.get("input_artifact_refs")
    if not isinstance(input_refs, list):
        raise ReferenceStackError("registered request input_artifact_refs is invalid")
    if not all(isinstance(item, dict) for item in input_refs):
        raise ReferenceStackError(
            "registered request input_artifact_refs must be artifact references"
        )
    input_fingerprint_list = [
        json.dumps(item, sort_keys=True, separators=(",", ":"))
        for item in input_refs
    ]
    input_fingerprints = set(input_fingerprint_list)
    if len(input_fingerprints) != len(input_fingerprint_list):
        raise ReferenceStackError(
            "registered request input_artifact_refs must be unique"
        )
    payload = request.get("payload")
    payload_reference = payload.get("artifact_ref") if isinstance(payload, dict) else None
    if not isinstance(payload_reference, dict):
        raise ReferenceStackError("registered request payload artifact_ref is invalid")
    payload_fingerprint = json.dumps(
        payload_reference, sort_keys=True, separators=(",", ":")
    )
    if payload_fingerprint not in input_fingerprints:
        raise ReferenceStackError(
            "registered payload artifact_ref must be an exact request input"
        )
    operational_fingerprints = input_fingerprints - {payload_fingerprint}
    materials = config["materials"]
    if not isinstance(materials, list) or not 1 <= len(materials) <= MAX_MATERIALS:
        raise ReferenceStackError(
            f"materials must contain 1 to {MAX_MATERIALS} exact registered inputs"
        )
    destinations: set[str] = set()
    material_fingerprints: set[str] = set()
    for index, item in enumerate(materials):
        material = _strict_fields(
            item, {"artifact_ref", "destination"}, f"materials[{index}]"
        )
        reference = _artifact_ref(
            material["artifact_ref"], f"materials[{index}].artifact_ref"
        )
        fingerprint = json.dumps(reference, sort_keys=True, separators=(",", ":"))
        if fingerprint not in input_fingerprints:
            raise ReferenceStackError(
                f"materials[{index}].artifact_ref is not an exact request input"
            )
        if fingerprint == payload_fingerprint:
            raise ReferenceStackError(
                "the consumed payload artifact_ref cannot also be a material"
            )
        if fingerprint in material_fingerprints:
            raise ReferenceStackError("material artifact references must be unique")
        material_fingerprints.add(fingerprint)
        destination = _safe_relative(
            material["destination"], f"materials[{index}].destination"
        ).as_posix()
        if destination in destinations:
            raise ReferenceStackError("material destinations must be unique")
        destinations.add(destination)
    if material_fingerprints != operational_fingerprints:
        raise ReferenceStackError(
            "materials must cover every non-payload request input exactly once"
        )

    probes = config["tool_probes"]
    if not isinstance(probes, list) or len(probes) > MAX_TOOL_PROBES:
        raise ReferenceStackError(
            f"tool_probes must be a list with at most {MAX_TOOL_PROBES} entries"
        )
    probe_ids: set[str] = set()
    for index, item in enumerate(probes):
        probe = _strict_fields(
            item, {"tool_id", "argv", "timeout_seconds"}, f"tool_probes[{index}]"
        )
        identifier = _validate_identifier(
            probe["tool_id"], f"tool_probes[{index}].tool_id"
        )
        if identifier in probe_ids:
            raise ReferenceStackError("tool probe IDs must be unique")
        probe_ids.add(identifier)
        _validate_argv(probe["argv"], f"tool_probes[{index}].argv")
        _validate_timeout(
            probe["timeout_seconds"], f"tool_probes[{index}].timeout_seconds"
        )

    steps = config["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ReferenceStackError(f"steps must contain 1 to {MAX_STEPS} commands")
    step_ids: set[str] = set()
    for index, item in enumerate(steps):
        step = _strict_fields(
            item, {"step_id", "argv", "timeout_seconds"}, f"steps[{index}]"
        )
        identifier = _validate_identifier(step["step_id"], f"steps[{index}].step_id")
        if identifier in step_ids:
            raise ReferenceStackError("step IDs must be unique")
        step_ids.add(identifier)
        _validate_argv(step["argv"], f"steps[{index}].argv")
        _validate_timeout(step["timeout_seconds"], f"steps[{index}].timeout_seconds")

    outputs = config["expected_outputs"]
    if not isinstance(outputs, list) or not 1 <= len(outputs) <= MAX_OUTPUTS:
        raise ReferenceStackError(
            f"expected_outputs must contain 1 to {MAX_OUTPUTS} files"
        )
    publish_paths: set[str] = set()
    artifact_keys: set[tuple[str, str]] = set()
    artifact_roles: set[str] = set()
    for index, item in enumerate(outputs):
        spec = _artifact_spec(item, f"expected_outputs[{index}]", expected=True)
        source_path = _safe_relative(
            spec["source_path"], f"expected_outputs[{index}].source_path"
        ).as_posix()
        if source_path in destinations:
            raise ReferenceStackError("an expected output cannot overwrite a material")
        publish = _safe_relative(
            spec["publish_path"], f"expected_outputs[{index}].publish_path"
        ).as_posix()
        key = (spec["role"], spec["artifact_id"])
        if (
            publish in publish_paths
            or key in artifact_keys
            or spec["role"] in artifact_roles
        ):
            raise ReferenceStackError(
                "output publish paths, roles, and artifact identities must be unique"
            )
        publish_paths.add(publish)
        artifact_keys.add(key)
        artifact_roles.add(spec["role"])

    for field in ("log_artifact", "result_artifact"):
        spec = _artifact_spec(config[field], field, expected=False)
        publish = _safe_relative(spec["publish_path"], f"{field}.publish_path").as_posix()
        key = (spec["role"], spec["artifact_id"])
        if (
            publish in publish_paths
            or key in artifact_keys
            or spec["role"] in artifact_roles
        ):
            raise ReferenceStackError(
                "published artifact paths, roles, and identities must be unique"
            )
        publish_paths.add(publish)
        artifact_keys.add(key)
        artifact_roles.add(spec["role"])

    for field in ("log_artifact", "result_artifact"):
        _project_path(root, config[field]["publish_path"], f"{field}.publish_path")
    for index, spec in enumerate(outputs):
        _project_path(
            root,
            spec["publish_path"],
            f"expected_outputs[{index}].publish_path",
        )
    return config


def _run_researchctl(
    researchctl: Path,
    project_root: Path,
    arguments: list[str],
    *,
    allow_failure: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            [sys.executable, str(researchctl), *arguments],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as exc:
        raise ReferenceStackError(f"cannot invoke researchctl: {exc}") from exc
    if result.returncode != 0 and not allow_failure:
        detail = (result.stderr or result.stdout).strip()
        raise ReferenceStackError(
            f"researchctl {' '.join(arguments[:2])} failed: {detail}"
        )
    return result


def _verification_envelope(
    researchctl: Path,
    root: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    arguments = [
        "adapter",
        "verify",
        args.request_id,
        "--attempt-id",
        args.attempt_id,
    ]
    if args.retry_of_attempt_id is not None:
        arguments.extend(["--retry-of-attempt-id", args.retry_of_attempt_id])
    result = _run_researchctl(researchctl, root, arguments)
    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReferenceStackError("researchctl returned an invalid verification envelope") from exc
    if not isinstance(envelope, dict) or envelope.get("verification") != "accepted":
        raise ReferenceStackError("researchctl did not accept the Adapter Request")
    return envelope


def _state_updated_at(researchctl: Path, root: Path) -> str:
    result = _run_researchctl(researchctl, root, ["status", "--json"])
    try:
        state = json.loads(result.stdout)
        updated_at = state["updated_at"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ReferenceStackError("researchctl returned an invalid state timestamp") from exc
    if not isinstance(updated_at, str) or not updated_at:
        raise ReferenceStackError("researchctl state timestamp is invalid")
    return updated_at


def _resolve_snapshot(root: Path, reference: dict[str, Any], label: str) -> Path:
    raw = reference["snapshot_path"]
    if not isinstance(raw, str):
        raise ReferenceStackError(f"{label}.snapshot_path is invalid")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise ReferenceStackError(f"{label} snapshot is unavailable: {exc}") from exc
    if not resolved.is_file() or path.is_symlink():
        raise ReferenceStackError(f"{label} snapshot must be a regular non-symlink file")
    content_hash, size = _hash_file(resolved)
    if content_hash != reference["content_hash"] or size != reference["size_bytes"]:
        raise ReferenceStackError(f"{label} snapshot hash or size changed")
    return resolved


def _resolve_registered_source(root: Path, reference: dict[str, Any]) -> Path:
    value = reference["source_path"]
    source = Path(value).expanduser()
    if not source.is_absolute():
        source = root / source
    try:
        return source.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ReferenceStackError(
            f"cannot resolve registered input source {value!r}: {exc}"
        ) from exc


def _payload_config(
    root: Path,
    envelope: dict[str, Any],
) -> tuple[dict[str, Any], Path]:
    request = envelope.get("request")
    if not isinstance(request, dict):
        raise ReferenceStackError("verification envelope request is invalid")
    payload = request.get("payload")
    if not isinstance(payload, dict) or payload.get("locator") != PAYLOAD_LOCATOR:
        raise ReferenceStackError(
            f"Reference Stack payload locator must be {PAYLOAD_LOCATOR!r}"
        )
    reference = _artifact_ref(payload.get("artifact_ref"), "payload.artifact_ref")
    snapshot = _resolve_snapshot(root, reference, "payload.artifact_ref")
    config = _validate_config(_load_json_object(snapshot, "payload"), request, root)
    return config, snapshot


def _receipt(
    *,
    envelope: dict[str, Any],
    receipt_id: str,
    status: str,
    observed_at: str,
    supersedes: str | None,
    outputs: list[dict[str, Any]],
    logs: list[dict[str, Any]],
    message: str,
) -> dict[str, Any]:
    return {
        "receipt_id": receipt_id,
        "request_id": envelope["request"]["request_id"],
        "request_hash": envelope["request_hash"],
        "attempt_id": envelope["attempt_id"],
        "retry_of_attempt_id": envelope["retry_of_attempt_id"],
        "supersedes": supersedes,
        "adapter": {
            "adapter_id": ADAPTER_ID,
            "adapter_version": ADAPTER_VERSION,
            "protocol_version": PROTOCOL_VERSION,
        },
        "status": status,
        "observed_at": observed_at,
        "external_id": envelope["attempt_id"],
        "output_artifact_refs": outputs,
        "log_artifact_refs": logs,
        "message": message,
    }


def _append_receipt(
    researchctl: Path,
    root: Path,
    args: argparse.Namespace,
    receipt: dict[str, Any],
) -> None:
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="reference-stack-receipt-",
            suffix=".json",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(receipt, temporary, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.write("\n")
        _run_researchctl(
            researchctl,
            root,
            [
                "adapter",
                "receipt-append",
                "--stage",
                args.stage,
                "--path",
                args.exchange_path,
                "--artifact-id",
                args.exchange_artifact_id,
                "--receipt",
                temporary_name,
                "--json",
            ],
        )
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass


def _materialize(
    root: Path,
    sandbox: Path,
    materials: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    retained: list[dict[str, Any]] = []
    for index, material in enumerate(materials):
        reference = material["artifact_ref"]
        snapshot = _resolve_snapshot(root, reference, f"materials[{index}].artifact_ref")
        destination = _sandbox_path(
            sandbox, material["destination"], f"materials[{index}].destination"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(snapshot, destination)
        copied_hash, copied_size = _hash_file(destination)
        if copied_hash != reference["content_hash"] or copied_size != reference["size_bytes"]:
            raise ReferenceStackError("materialized input differs from its snapshot")
        retained.append(
            {
                "artifact_ref": reference,
                "destination": material["destination"],
                "materialized_hash": copied_hash,
                "size_bytes": copied_size,
            }
        )
    return retained


def _command_environment(config: dict[str, Any]) -> dict[str, str]:
    declaration = config["environment"]
    environment = {
        name: os.environ[name]
        for name in declaration["inherit"]
        if name in os.environ
    }
    environment.update(declaration["set"])
    return environment


@dataclass
class _OwnedLog:
    """Bounded parent-owned command log; children receive only a pipe."""

    stream: Any
    size_bytes: int = 0

    def write(self, content: str | bytes) -> None:
        data = (
            content.encode("utf-8", errors="backslashreplace")
            if isinstance(content, str)
            else content
        )
        if self.size_bytes + len(data) > MAX_LOG_BYTES:
            raise ReferenceStackError(
                f"Reference Stack log exceeds {MAX_LOG_BYTES} bytes"
            )
        self.stream.write(data)
        self.stream.flush()
        self.size_bytes += len(data)


def _freeze_owned_log(log: _OwnedLog, destination: Path) -> tuple[str, int]:
    """Copy the unnamed parent-owned spool to one stable regular evidence file."""

    digest = hashlib.sha256()
    copied = 0
    output_fd: int | None = None
    try:
        log.stream.flush()
        log.stream.seek(0)
        output_fd = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        created = os.fstat(output_fd)
        with os.fdopen(output_fd, "wb") as output:
            output_fd = None
            for block in iter(lambda: log.stream.read(1024 * 1024), b""):
                copied += len(block)
                if copied > MAX_LOG_BYTES:
                    raise ReferenceStackError(
                        f"Reference Stack log exceeds {MAX_LOG_BYTES} bytes"
                    )
                output.write(block)
                digest.update(block)
            output.flush()
            os.fsync(output.fileno())
            completed = os.fstat(output.fileno())
        visible = destination.lstat()
    except FileExistsError as exc:
        raise ReferenceStackError(
            f"Reference Stack evidence path already exists: {destination}"
        ) from exc
    except ReferenceStackError:
        raise
    except OSError as exc:
        raise ReferenceStackError(
            f"cannot freeze Reference Stack log {destination}: {exc}"
        ) from exc
    finally:
        if output_fd is not None:
            os.close(output_fd)
    if (
        (created.st_dev, created.st_ino)
        != (completed.st_dev, completed.st_ino)
        or (created.st_dev, created.st_ino) != (visible.st_dev, visible.st_ino)
        or copied != completed.st_size
        or copied != log.size_bytes
    ):
        raise ReferenceStackError(
            "Reference Stack frozen log identity or size changed before publication"
        )
    return f"sha256:{digest.hexdigest()}", copied


def _terminate_process(process: subprocess.Popen[Any]) -> int:
    """Kill the declared process group and reap its leader before continuing."""

    current = process.poll()
    if os.name != "nt":
        try:
            # start_new_session makes the leader PID the process-group ID. Kill
            # the group even after the leader has exited so background descendants
            # cannot outlive a successful step and mutate the sandbox later.
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            if current is None:
                process.kill()
    elif current is None:  # pragma: no cover - platform-specific process termination
        process.kill()
    if current is not None:
        return current
    try:
        return process.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive fallback
        process.kill()
        return process.wait()


def _run_command(
    argv: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: int,
    log_stream: _OwnedLog,
) -> dict[str, Any]:
    started_at = _utc_now()
    log_stream.write(f"\n$ {json.dumps(argv, ensure_ascii=False)}\n")
    timed_out = False
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        return {
            "argv": argv,
            "started_at": started_at,
            "finished_at": _utc_now(),
            "returncode": None,
            "timed_out": False,
            "launch_error": str(exc),
        }
    if process.stdout is None:  # pragma: no cover - subprocess contract guard
        _terminate_process(process)
        raise ReferenceStackError("Reference Stack command pipe was not created")
    reader_errors: list[BaseException] = []

    def drain_stdout() -> None:
        try:
            for block in iter(lambda: process.stdout.read(64 * 1024), b""):
                log_stream.write(block)
        except BaseException as exc:
            reader_errors.append(exc)
        finally:
            process.stdout.close()

    reader = threading.Thread(
        target=drain_stdout,
        name="reference-stack-log-drain",
        daemon=True,
    )
    reader.start()
    try:
        deadline = time.monotonic() + timeout_seconds
        while True:
            if reader_errors:
                raise reader_errors[0]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                returncode = _terminate_process(process)
                break
            try:
                returncode = process.wait(timeout=min(0.1, remaining))
                _terminate_process(process)
                break
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        _terminate_process(process)
        reader.join(timeout=5)
        raise
    reader.join(timeout=5)
    if reader.is_alive():
        _terminate_process(process)
        raise ReferenceStackError("Reference Stack command log pipe did not close")
    if reader_errors:
        raise reader_errors[0]
    return {
        "argv": argv,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "returncode": returncode,
        "timed_out": timed_out,
        "launch_error": None,
    }


def _collect_outputs(
    sandbox: Path,
    outputs: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[tuple[dict[str, Any], Path, str, int]],
]:
    observations: list[dict[str, Any]] = []
    publications: list[tuple[dict[str, Any], Path, str, int]] = []
    for index, spec in enumerate(outputs):
        source = _sandbox_path(
            sandbox, spec["source_path"], f"expected_outputs[{index}].source_path"
        )
        if not source.is_file() or source.is_symlink():
            observations.append(
                {
                    "source_path": spec["source_path"],
                    "publish_path": spec["publish_path"],
                    "present": False,
                    "content_hash": None,
                    "size_bytes": None,
                }
            )
            continue
        content_hash, size = _hash_file(source)
        publications.append((spec, source, content_hash, size))
        observations.append(
            {
                "source_path": spec["source_path"],
                "publish_path": spec["publish_path"],
                "present": True,
                "content_hash": content_hash,
                "size_bytes": size,
            }
        )
    return observations, publications


def _write_sandbox_json(path: Path, value: dict[str, Any]) -> None:
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, ValueError) as exc:
        raise ReferenceStackError(f"cannot retain sandbox JSON {path}: {exc}") from exc


def _state_artifact_refs(
    researchctl: Path,
    root: Path,
    stage: str,
    expectations: list[tuple[dict[str, Any], str, int]],
) -> list[dict[str, Any]]:
    result = _run_researchctl(researchctl, root, ["status", "--json"])
    try:
        state = strict_json_loads(result.stdout)
        artifacts = state["artifacts"][stage]
    except (
        json.JSONDecodeError,
        DuplicateJsonKeyError,
        NonStandardJsonConstantError,
        KeyError,
        TypeError,
        RecursionError,
    ) as exc:
        raise ReferenceStackError(
            "cannot recover committed publication references from researchctl state"
        ) from exc
    references: list[dict[str, Any]] = []
    for spec, expected_hash, expected_size in expectations:
        try:
            entry = artifacts[spec["role"]][spec["artifact_id"]]
            matches = [
                item
                for item in entry["revisions"]
                if isinstance(item, dict)
                and item.get("source_path") == spec["publish_path"]
                and item.get("content_hash") == expected_hash
                and item.get("size_bytes") == expected_size
            ]
            if len(matches) != 1:
                raise ValueError("publication revision match is not unique")
            revision = matches[0]
        except (KeyError, TypeError, ValueError) as exc:
            raise ReferenceStackError(
                "exact committed publication is missing or ambiguous in researchctl state"
            ) from exc
        reference = {
            "label": f"artifacts.{stage}.{spec['role']}.{spec['artifact_id']}",
            "artifact_id": spec["artifact_id"],
            **revision,
        }
        references.append(_artifact_ref(reference, "committed artifact_ref"))
    return references


def _publish_batch(
    researchctl: Path,
    root: Path,
    stage: str,
    attempt_id: str,
    sandbox: Path,
    publications: list[tuple[dict[str, Any], Path, str, int]],
) -> list[dict[str, Any]]:
    expectations = [
        (spec, expected_hash, expected_size)
        for spec, _source, expected_hash, expected_size in publications
    ]
    for _spec, source, expected_hash, expected_size in publications:
        if _hash_file(source) != (expected_hash, expected_size):
            raise ReferenceStackError(
                f"Reference Stack source changed after observation: {source}"
            )
    manifest_path = sandbox / "reference-stack-publications.json"
    _write_sandbox_json(
        manifest_path,
        {
            "schema_version": SCHEMA_VERSION,
            "publications": [
                {
                    "source_path": str(source),
                    "publish_path": spec["publish_path"],
                    "role": spec["role"],
                    "artifact_id": spec["artifact_id"],
                    "expected_content_hash": expected_hash,
                    "expected_size_bytes": expected_size,
                }
                for spec, source, expected_hash, expected_size in publications
            ],
        },
    )
    result = _run_researchctl(
        researchctl,
        root,
        [
            "artifact",
            "publish-batch",
            "--stage",
            stage,
            "--attempt-id",
            attempt_id,
            "--manifest",
            str(manifest_path),
            "--json",
        ],
        allow_failure=True,
    )
    parse_error: BaseException | None = None
    try:
        if result.returncode != 0:
            raise ReferenceStackError("publish-batch returned a non-zero status")
        payload = strict_json_loads(result.stdout)
        raw_references = payload["artifact_refs"]
        if not isinstance(raw_references, list) or len(raw_references) != len(
            publications
        ):
            raise TypeError("artifact_refs count mismatch")
        references = [
            _artifact_ref(reference, f"artifact_refs[{index}]")
            for index, reference in enumerate(raw_references)
        ]
        expected_labels = [
            f"artifacts.{stage}.{spec['role']}.{spec['artifact_id']}"
            for spec, _source, _expected_hash, _expected_size in publications
        ]
        if [reference["label"] for reference in references] != expected_labels:
            raise TypeError("artifact_refs identities mismatch")
        for reference, (spec, expected_hash, expected_size) in zip(
            references, expectations, strict=True
        ):
            if (
                reference.get("source_path") != spec["publish_path"]
                or reference.get("content_hash") != expected_hash
                or reference.get("size_bytes") != expected_size
            ):
                raise TypeError("artifact_refs revision content mismatch")
        return references
    except (
        json.JSONDecodeError,
        DuplicateJsonKeyError,
        NonStandardJsonConstantError,
        KeyError,
        TypeError,
        ReferenceStackError,
        RecursionError,
    ) as exc:
        parse_error = exc
    try:
        # State is authoritative even when the writer committed before a later
        # output/exit failure. Recover the unique exact revision, not merely the
        # current artifact revision, because a later publication may advance it.
        return _state_artifact_refs(researchctl, root, stage, expectations)
    except ReferenceStackError as recovery_error:
        detail = (result.stderr or result.stdout).strip()
        if result.returncode != 0:
            raise ReferenceStackError(
                "researchctl artifact publish-batch failed without an exact "
                f"committed revision: {detail or result.returncode}"
            ) from recovery_error
        raise ReferenceStackError(
            "researchctl publication output was invalid and exact state recovery "
            f"failed: {parse_error}"
        ) from recovery_error


def run_adapter(args: argparse.Namespace) -> int:
    root = Path(args.project_root).expanduser().resolve()
    researchctl = Path(args.researchctl).expanduser().resolve()
    if not researchctl.is_file():
        raise ReferenceStackError(f"researchctl not found: {researchctl}")
    _validate_identifier(args.request_id, "--request-id")
    _validate_identifier(args.attempt_id, "--attempt-id")
    _validate_identifier(args.exchange_artifact_id, "--exchange-artifact-id")
    _validate_role(args.stage, "--stage")
    exchange_path = _project_path(root, args.exchange_path, "--exchange-path")
    args.exchange_path = str(exchange_path)

    envelope = _verification_envelope(researchctl, root, args)
    config, payload_snapshot = _payload_config(root, envelope)
    publish_paths = [
        _project_path(root, spec["publish_path"], "expected output publish_path")
        for spec in config["expected_outputs"]
    ]
    publish_paths.extend(
        _project_path(root, config[field]["publish_path"], f"{field}.publish_path")
        for field in ("log_artifact", "result_artifact")
    )
    input_refs = envelope["request"]["input_artifact_refs"]
    protected_paths = {
        exchange_path,
        payload_snapshot,
        *(_resolve_registered_source(root, reference) for reference in input_refs),
        *(
            _resolve_snapshot(root, reference, "request input artifact")
            for reference in input_refs
        ),
    }
    if any(path in protected_paths for path in publish_paths):
        raise ReferenceStackError(
            "Reference Stack outputs cannot overwrite a registered input, its "
            "snapshot, the exchange, or the payload snapshot"
        )
    if any(path.exists() for path in publish_paths):
        raise ReferenceStackError(
            "Reference Stack outputs cannot overwrite an unrelated existing project file"
        )
    attempt_prefix = (
        f".research/artifacts/{args.stage}/reference-stack/{args.attempt_id}/"
    )
    declared_publish_paths = [
        spec["publish_path"] for spec in config["expected_outputs"]
    ] + [config[field]["publish_path"] for field in ("log_artifact", "result_artifact")]
    if any(not path.startswith(attempt_prefix) for path in declared_publish_paths):
        raise ReferenceStackError(
            "every Reference Stack publish path must be fresh and attempt-scoped under "
            f"{attempt_prefix}"
        )

    accepted_id = f"RS-{args.attempt_id}-ACCEPTED"
    accepted = _receipt(
        envelope=envelope,
        receipt_id=accepted_id,
        status="accepted",
        observed_at=_state_updated_at(researchctl, root),
        supersedes=None,
        outputs=[],
        logs=[],
        message="Reference Adapter durably accepted the verified isolated-command attempt.",
    )
    _append_receipt(researchctl, root, args, accepted)

    command_started = False
    output_refs: list[dict[str, Any]] = []
    log_refs: list[dict[str, Any]] = []
    try:
        with (
            tempfile.TemporaryDirectory(
                prefix="scientific-reference-stack-sandbox-"
            ) as temporary,
            tempfile.TemporaryDirectory(
                prefix="scientific-reference-stack-evidence-"
            ) as evidence_temporary,
            tempfile.TemporaryFile(mode="w+b") as log_spool,
        ):
            sandbox = Path(temporary).resolve()
            evidence_root = Path(evidence_temporary).resolve()
            materialized = _materialize(root, sandbox, config["materials"])
            working_directory = _sandbox_path(
                sandbox, config["working_directory"], "working_directory"
            )
            working_directory.mkdir(parents=True, exist_ok=True)
            environment = _command_environment(config)
            raw_log = evidence_root / "reference-stack.log"
            probes: list[dict[str, Any]] = []
            steps: list[dict[str, Any]] = []
            log = _OwnedLog(log_spool)
            log.write(
                "Reference Stack mechanical log\n"
                f"request_id={args.request_id}\n"
                f"attempt_id={args.attempt_id}\n"
                f"network={config['network']} (declaration only; not enforced)\n"
            )
            for probe in config["tool_probes"]:
                command_started = True
                observation = _run_command(
                    probe["argv"],
                    cwd=working_directory,
                    environment=environment,
                    timeout_seconds=probe["timeout_seconds"],
                    log_stream=log,
                )
                observation["tool_id"] = probe["tool_id"]
                probes.append(observation)
                if observation["returncode"] != 0 or observation["timed_out"]:
                    break
            probes_passed = all(
                item["returncode"] == 0 and not item["timed_out"]
                for item in probes
            ) and len(probes) == len(config["tool_probes"])
            if probes_passed:
                for step in config["steps"]:
                    command_started = True
                    observation = _run_command(
                        step["argv"],
                        cwd=working_directory,
                        environment=environment,
                        timeout_seconds=step["timeout_seconds"],
                        log_stream=log,
                    )
                    observation["step_id"] = step["step_id"]
                    steps.append(observation)
                    if observation["returncode"] != 0 or observation["timed_out"]:
                        break
            inputs_unchanged = True
            for material in materialized:
                material_path = _sandbox_path(
                    sandbox,
                    material["destination"],
                    "materialized input destination",
                )
                if not material_path.is_file() or material_path.is_symlink():
                    material["post_execution_hash"] = None
                    material["post_execution_size_bytes"] = None
                    material["unchanged"] = False
                    inputs_unchanged = False
                    continue
                post_hash, post_size = _hash_file(material_path)
                material["post_execution_hash"] = post_hash
                material["post_execution_size_bytes"] = post_size
                material["unchanged"] = (
                    post_hash == material["materialized_hash"]
                    and post_size == material["size_bytes"]
                )
                inputs_unchanged = inputs_unchanged and material["unchanged"]
            log_hash, log_size = _freeze_owned_log(log, raw_log)
            output_observations, output_publications = _collect_outputs(
                sandbox,
                config["expected_outputs"],
            )
            log_spec = config["log_artifact"]

            all_steps_passed = (
                probes_passed
                and len(steps) == len(config["steps"])
                and all(
                    item["returncode"] == 0 and not item["timed_out"]
                    for item in steps
                )
            )
            all_outputs_present = all(item["present"] for item in output_observations)
            status = (
                "succeeded"
                if all_steps_passed and all_outputs_present and inputs_unchanged
                else "failed"
            )
            result_manifest = {
                "schema_version": SCHEMA_VERSION,
                "adapter": {
                    "adapter_id": ADAPTER_ID,
                    "adapter_version": ADAPTER_VERSION,
                    "protocol_version": PROTOCOL_VERSION,
                },
                "request_id": args.request_id,
                "request_hash": envelope["request_hash"],
                "attempt_id": args.attempt_id,
                "operation_kind": config["operation_kind"],
                "status": status,
                "isolated_working_directory": True,
                "network_declaration": config["network"],
                "network_enforcement": "not_provided",
                "environment_inherited_names": config["environment"]["inherit"],
                "environment_set_names": sorted(config["environment"]["set"]),
                "materials": materialized,
                "tool_probes": probes,
                "steps": steps,
                "expected_outputs": output_observations,
                "log": {"content_hash": log_hash, "size_bytes": log_size},
                "mechanical_checks": {
                    "tool_probes_passed": probes_passed,
                    "steps_passed": all_steps_passed,
                    "expected_outputs_present": all_outputs_present,
                    "materialized_inputs_unchanged": inputs_unchanged,
                },
                "semantic_certifications": [],
                "limitations": [
                    "does_not_certify_scientific_correctness",
                    "does_not_certify_researcher_review_or_venue_facts",
                    "does_not_enforce_the_declared_network_boundary",
                    "does_not_approve_a_gate_or_external_submission",
                ],
                "finished_at": _utc_now(),
            }
            result_spec = config["result_artifact"]
            sandbox_result = evidence_root / "reference-stack-result.json"
            _write_sandbox_json(sandbox_result, result_manifest)
            result_hash, result_size = _hash_file(sandbox_result)
            publications = [
                *output_publications,
                (log_spec, raw_log, log_hash, log_size),
                (result_spec, sandbox_result, result_hash, result_size),
            ]
            published_refs = _publish_batch(
                researchctl,
                root,
                args.stage,
                args.attempt_id,
                evidence_root,
                publications,
            )
            for (spec, _source, _expected_hash, _expected_size), reference in zip(
                publications, published_refs, strict=True
            ):
                if spec is result_spec or spec.get("classification") == "output":
                    output_refs.append(reference)
                else:
                    log_refs.append(reference)

        terminal_id = f"RS-{args.attempt_id}-{status.upper()}"
        terminal = _receipt(
            envelope=envelope,
            receipt_id=terminal_id,
            status=status,
            observed_at=_state_updated_at(researchctl, root),
            supersedes=accepted_id,
            outputs=output_refs,
            logs=log_refs,
            message=(
                "Reference Adapter completed every declared mechanical step and output."
                if status == "succeeded"
                else "Reference Adapter observed an unsatisfied mechanical contract."
            ),
        )
        _append_receipt(researchctl, root, args, terminal)
        print(
            json.dumps(
                {
                    "output_schema_version": SCHEMA_VERSION,
                    "status": status,
                    "request_id": args.request_id,
                    "attempt_id": args.attempt_id,
                    "receipt_id": terminal_id,
                    "output_artifact_refs": output_refs,
                    "log_artifact_refs": log_refs,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if status == "succeeded" else 1
    except BaseException as exc:
        status = "unknown" if command_started else "failed"
        terminal_id = f"RS-{args.attempt_id}-{status.upper()}"
        try:
            terminal = _receipt(
                envelope=envelope,
                receipt_id=terminal_id,
                status=status,
                observed_at=_state_updated_at(researchctl, root),
                supersedes=accepted_id,
                outputs=output_refs,
                logs=log_refs,
                message=(
                    "Reference Adapter lost complete provenance after command start."
                    if status == "unknown"
                    else "Reference Adapter failed before starting a declared command."
                ),
            )
            _append_receipt(researchctl, root, args, terminal)
        except BaseException as receipt_error:
            raise ReferenceStackError(
                f"attempt remains accepted and requires reconciliation; "
                f"execution error: {exc}; terminal receipt error: {receipt_error}"
            ) from receipt_error
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise ReferenceStackError(f"Reference Adapter ended {status}: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reference_stack",
        description=(
            "Execute an exact registered command payload in a clean temporary "
            "directory through the public Adapter Exchange protocol."
        ),
    )
    parser.add_argument("command", choices=("run",))
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--retry-of-attempt-id")
    parser.add_argument("--stage", required=True)
    parser.add_argument("--exchange-path", required=True)
    parser.add_argument("--exchange-artifact-id", required=True)
    parser.add_argument("--project-root", default=str(Path.cwd()))
    parser.add_argument(
        "--researchctl",
        default=str(Path(__file__).resolve().with_name("researchctl.py")),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return run_adapter(args)
    except ReferenceStackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
