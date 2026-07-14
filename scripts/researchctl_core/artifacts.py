"""Artifact path handling, immutable identities, and registry validation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .constants import (
    ARTIFACT_ID_RE,
    ARTIFACT_METADATA_FIELDS,
    ARTIFACT_POINTER_FIELDS,
    ARTIFACT_ROLE_RE,
    LEGACY_RELATIVE_PATH,
    LOCK_RELATIVE_PATH,
    MEMORY_RELATIVE_PATH,
    Policy,
    RESERVED_ARTIFACT_IDS,
    ResearchCtlError,
    SHA256_RE,
    STATE_RELATIVE_PATH,
)


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

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        before = path.stat()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        after = path.stat()
    except OSError as exc:
        raise ResearchCtlError(f"cannot hash artifact file {path}: {exc}") from exc
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
            f"artifact file changed while it was being hashed: {path}; retry with a stable file"
        )
    return f"sha256:{digest.hexdigest()}"

def is_research_control_file(root: Path, path: Path) -> bool:
    """Return whether ``path`` is workflow control metadata, not evidence."""

    control_files = (
        root / STATE_RELATIVE_PATH,
        root / LOCK_RELATIVE_PATH,
        root / MEMORY_RELATIVE_PATH,
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
    try:
        unresolved_path = Path(path_value).expanduser()
        if not unresolved_path.is_absolute():
            unresolved_path = root / unresolved_path
        resolved_for_control_check = unresolved_path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        return [f"{label}.path cannot be resolved: {exc}"]
    if is_research_control_file(root, resolved_for_control_check):
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

def is_direct_artifact_pointer(value: Any) -> bool:
    """Recognize the pre-registry pointer shape without inspecting mapping labels."""

    return (
        isinstance(value, dict)
        and isinstance(value.get("path"), str)
        and any(field in value for field in ARTIFACT_METADATA_FIELDS)
    )

def iter_current_artifact_pointers(
    state: dict[str, Any], policy: Policy
) -> Iterable[tuple[str, dict[str, Any]]]:
    """Yield only canonical artifacts[stage][role][artifact_id] pointers."""

    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        return
    for stage in policy.stage_order:
        stage_bucket = artifacts.get(stage)
        if not isinstance(stage_bucket, dict):
            continue
        for role, role_bucket in stage_bucket.items():
            if (
                not isinstance(role, str)
                or not ARTIFACT_ROLE_RE.fullmatch(role)
                or not isinstance(role_bucket, dict)
                or is_direct_artifact_pointer(role_bucket)
            ):
                continue
            for artifact_id, pointer in role_bucket.items():
                if isinstance(artifact_id, str) and isinstance(pointer, dict):
                    yield f"artifacts.{stage}.{role}.{artifact_id}", pointer

def artifact_identity_payload(pointer: dict[str, Any]) -> dict[str, Any]:
    """Return the immutable fields bound by an artifact ID and version."""

    return {field: pointer.get(field) for field in ARTIFACT_POINTER_FIELDS}

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
    elif isinstance(raw_stage, dict) and not is_direct_artifact_pointer(raw_stage):
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
    elif is_direct_artifact_pointer(raw_role):
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

def validate_legacy_artifact_tree(
    root: Path,
    value: Any,
    label: str,
    warnings: list[str],
    *,
    verify_integrity: bool,
) -> None:
    """Inspect legacy containers iteratively; they remain readable but non-canonical."""

    stack: list[tuple[Any, str]] = [(value, label)]
    while stack:
        current, current_label = stack.pop()
        if current is None:
            continue
        if isinstance(current, str):
            if not current.strip():
                warnings.append(
                    f"legacy artifact pointer: {current_label} is an empty artifact path"
                )
                continue
            try:
                candidate = Path(current).expanduser()
                if not candidate.is_absolute():
                    candidate = root / candidate
                exists = candidate.exists()
            except (OSError, RuntimeError, ValueError) as exc:
                warnings.append(
                    f"artifact pointer cannot be resolved: {current_label} -> {exc}"
                )
                continue
            if not exists:
                warnings.append(
                    f"artifact pointer does not exist: {current_label} -> {current}"
                )
            continue
        if isinstance(current, list):
            stack.extend(
                (child, f"{current_label}[{index}]")
                for index, child in reversed(list(enumerate(current)))
            )
            continue
        if not isinstance(current, dict):
            warnings.append(
                f"legacy artifact pointer: {current_label} is not a valid artifact path"
            )
            continue
        if is_direct_artifact_pointer(current):
            pointer_errors = artifact_pointer_errors(
                root,
                current,
                current_label,
                verify_integrity=verify_integrity,
            )
            warnings.extend(
                f"legacy artifact pointer: {error}" for error in pointer_errors
            )
            continue
        if any(field in current for field in ARTIFACT_METADATA_FIELDS):
            warnings.append(
                f"legacy artifact pointer: {current_label} is not a valid artifact path"
            )
            continue
        stack.extend(
            (child, f"{current_label}.{key}")
            for key, child in reversed(list(current.items()))
        )

def validate_artifact_registry(
    root: Path,
    artifacts: dict[str, Any] | list[Any],
    policy: Policy,
    errors: list[str],
    warnings: list[str],
    *,
    verify_integrity: bool,
) -> None:
    """Validate the fixed artifacts[stage][role][artifact_id] registry shape."""

    if isinstance(artifacts, list):
        validate_legacy_artifact_tree(
            root,
            artifacts,
            "artifacts",
            warnings,
            verify_integrity=verify_integrity,
        )
        return
    for stage, stage_bucket in artifacts.items():
        stage_label = f"artifacts.{stage}"
        if stage not in policy.stage_order:
            validate_legacy_artifact_tree(
                root,
                stage_bucket,
                stage_label,
                warnings,
                verify_integrity=verify_integrity,
            )
            continue
        if not isinstance(stage_bucket, dict) or is_direct_artifact_pointer(stage_bucket):
            errors.append(f"{stage_label} must be a role mapping")
            continue
        for role, role_bucket in stage_bucket.items():
            role_label = f"{stage_label}.{role}"
            if not ARTIFACT_ROLE_RE.fullmatch(role):
                errors.append(f"{role_label} role must use lower_snake_case")
                continue
            if not isinstance(role_bucket, dict):
                errors.append(f"{role_label} must be an artifact-ID mapping")
                continue
            if is_direct_artifact_pointer(role_bucket):
                pointer_errors = artifact_pointer_errors(
                    root,
                    role_bucket,
                    role_label,
                    verify_integrity=verify_integrity,
                )
                errors.extend(pointer_errors)
                warnings.append(
                    f"legacy artifact pointer: {role_label} should be re-registered "
                    "under an artifact-ID mapping"
                )
                continue
            for artifact_id, pointer in role_bucket.items():
                pointer_label = f"{role_label}.{artifact_id}"
                if (
                    not ARTIFACT_ID_RE.fullmatch(artifact_id)
                    or artifact_id in RESERVED_ARTIFACT_IDS
                ):
                    errors.append(
                        f"{pointer_label} has an invalid or reserved artifact-ID mapping key"
                    )
                pointer_errors = artifact_pointer_errors(
                    root,
                    pointer,
                    pointer_label,
                    verify_integrity=verify_integrity,
                )
                if isinstance(pointer, dict):
                    extra = set(pointer) - set(ARTIFACT_POINTER_FIELDS)
                    if extra:
                        pointer_errors.append(
                            f"{pointer_label} has unknown fields: {', '.join(sorted(extra))}"
                        )
                    if pointer.get("artifact_id") != artifact_id:
                        pointer_errors.append(
                            f"{pointer_label}.artifact_id must match its artifact-ID mapping key"
                        )
                errors.extend(pointer_errors)

def validate_artifact_identities(
    state: dict[str, Any],
    policy: Policy,
    errors: list[str],
    warnings: list[str],
    *,
    allow_binding_drift_for: frozenset[str],
) -> None:
    """Reject immutable identity conflicts while preserving a Gate recovery path."""

    current: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = {}
    historical: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = {}

    def add(
        destination: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]],
        label: str,
        pointer: dict[str, Any],
    ) -> None:
        artifact_id = pointer.get("artifact_id")
        version = pointer.get("version")
        if (
            not isinstance(artifact_id, str)
            or not ARTIFACT_ID_RE.fullmatch(artifact_id)
            or isinstance(version, bool)
            or not isinstance(version, (str, int))
            or not str(version).strip()
        ):
            return
        identity = (artifact_id, str(version))
        destination.setdefault(identity, []).append(
            (label, artifact_identity_payload(pointer))
        )

    for label, pointer in iter_current_artifact_pointers(state, policy):
        add(current, label, pointer)
    for label, pointer in iter_gate_artifact_refs(state):
        add(historical, label, pointer)

    def unique_payloads(
        bindings: list[tuple[str, dict[str, Any]]],
    ) -> dict[str, tuple[str, dict[str, Any]]]:
        return {
            json.dumps(payload, ensure_ascii=False, sort_keys=True): (label, payload)
            for label, payload in bindings
        }

    for source_name, collection in (("current registry", current), ("Gate history", historical)):
        for (artifact_id, version), bindings in collection.items():
            payloads = unique_payloads(bindings)
            if len(payloads) > 1:
                labels = [label for label, _payload in payloads.values()]
                errors.append(
                    f"artifact identity {artifact_id}@{version} has different metadata "
                    f"within {source_name}: {', '.join(labels)}"
                )

    active_gates_by_identity: dict[tuple[str, str], set[str]] = {}
    gates = state.get("gates")
    if isinstance(gates, dict):
        for gate, record in gates.items():
            history = record.get("history") if isinstance(record, dict) else None
            if (
                not isinstance(record, dict)
                or record.get("status") != "approved"
                or not isinstance(history, list)
            ):
                continue
            approval = next(
                (
                    decision
                    for decision in reversed(history)
                    if isinstance(decision, dict) and decision.get("action") == "approve"
                ),
                None,
            )
            refs = approval.get("artifact_refs") if isinstance(approval, dict) else None
            if not isinstance(refs, list):
                continue
            for reference in refs:
                if not isinstance(reference, dict):
                    continue
                artifact_id = reference.get("artifact_id")
                version = reference.get("version")
                if isinstance(artifact_id, str) and isinstance(version, (str, int)):
                    active_gates_by_identity.setdefault(
                        (artifact_id, str(version)), set()
                    ).add(str(gate))

    for identity in current.keys() & historical.keys():
        current_payloads = unique_payloads(current[identity])
        historical_payloads = unique_payloads(historical[identity])
        if set(current_payloads) == set(historical_payloads):
            continue
        artifact_id, version = identity
        active_gates = active_gates_by_identity.get(identity, set())
        message = (
            f"artifact identity {artifact_id}@{version} differs between the current "
            "registry and Gate history"
        )
        if active_gates and not active_gates.issubset(allow_binding_drift_for):
            errors.append(
                f"{message}; reopen Gate(s) {', '.join(sorted(active_gates))} before recovery"
            )
        else:
            warnings.append(
                f"{message}; register a new version to complete recovery"
            )
