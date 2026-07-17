"""Create and verify deterministic, snapshot-only offline audit bundles."""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import stat
import tarfile
import tempfile
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable

from .constants import (
    MEMORY_RELATIVE_PATH,
    PLUGIN_ROOT,
    STATE_RELATIVE_PATH,
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
from .policy import load_policy, policy_path
from .runtime_contract import runtime_contract_path


BUNDLE_SCHEMA_VERSION = "1.0"
REPORT_SCHEMA_VERSION = "1.0"
MANIFEST_PATH = "audit-manifest.json"
STATE_ENTRY_PATH = "workspace/.research/state.json"
MEMORY_ENTRY_PATH = "workspace/.research/memory.md"
POLICY_ENTRY_PATH = "contracts/policy.yaml"
RUNTIME_ENTRY_PATH = "contracts/runtime-contract.json"
PLUGIN_ENTRY_PATH = "contracts/plugin.json"
TRACE_ENTRY_PATH = "projections/trace.json"
FIXED_MODE = 0o644
FIXED_MTIME = 0
MAX_ARCHIVE_ENTRIES = 100_000
MAX_MANIFEST_BYTES = 8 * 1024 * 1024

BASE_LIMITATIONS = (
    "does_not_certify_scientific_correctness_or_statistical_validity",
    "does_not_authenticate_human_identity_or_authorization",
    "does_not_certify_provider_reported_truth",
    "does_not_certify_paper_quality_or_acceptance",
    "does_not_prove_origin_without_an_externally_pinned_evidence_root",
    "memory_is_non_authoritative_navigation_only",
    "verification_is_snapshot_only_and_does_not_verify_live_sources",
)


@dataclass(frozen=True)
class _Payload:
    path: str
    kind: str
    authoritative: bool
    content_hash: str
    size_bytes: int
    content: bytes | None = None
    source: Path | None = None

    def manifest_entry(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "authoritative": self.authoritative,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
        }


def _canonical_json(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ResearchCtlError(f"audit value is not canonical JSON: {exc}") from exc


def _hash_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
    except OSError as exc:
        raise ResearchCtlError(f"cannot read audit input {path}: {exc}") from exc
    return f"sha256:{digest.hexdigest()}", size


def _safe_archive_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        return None
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        return None
    if any(part in {"", ".", ".."} for part in posix.parts):
        return None
    normalized = posix.as_posix()
    return normalized if normalized == value else None


def _collision_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _ensure_unique_paths(paths: Iterable[str], label: str) -> None:
    exact: set[str] = set()
    folded: dict[str, str] = {}
    for path in paths:
        if _safe_archive_path(path) is None:
            raise ResearchCtlError(f"{label} has unsafe archive path: {path!r}")
        if path in exact:
            raise ResearchCtlError(f"{label} has duplicate archive path: {path!r}")
        exact.add(path)
        key = _collision_key(path)
        prior = folded.setdefault(key, path)
        if prior != path:
            raise ResearchCtlError(
                f"{label} has case-insensitive archive path collision: "
                f"{prior!r} and {path!r}"
            )


def _read_regular_file(path: Path, *, root: Path | None = None) -> bytes:
    try:
        if root is not None:
            root = root.resolve()
            resolved = path.resolve()
            if resolved != root and root not in resolved.parents:
                raise ResearchCtlError(f"audit input escapes its root: {path}")
            relative = resolved.relative_to(root)
            cursor = root
            for part in relative.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise ResearchCtlError(f"audit input cannot use a symlink: {path}")
        elif path.is_symlink():
            raise ResearchCtlError(f"audit input cannot use a symlink: {path}")
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode):
            raise ResearchCtlError(f"audit input must be a regular file: {path}")
        return path.read_bytes()
    except ResearchCtlError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise ResearchCtlError(f"cannot read audit input {path}: {exc}") from exc


def _payload_from_bytes(
    path: str,
    kind: str,
    authoritative: bool,
    content: bytes,
) -> _Payload:
    return _Payload(
        path=path,
        kind=kind,
        authoritative=authoritative,
        content_hash=_hash_bytes(content),
        size_bytes=len(content),
        content=content,
    )


def _payload_from_file(
    archive_path: str,
    kind: str,
    authoritative: bool,
    source: Path,
    *,
    root: Path,
) -> _Payload:
    # Read once through the symlink-safe path check, then stream the same regular
    # file into the tar. The completed tar is rehashed before publication, closing
    # the mutation window without retaining every historical snapshot in memory.
    _read_regular_file(source, root=root)
    content_hash, size_bytes = _hash_file(source)
    return _Payload(
        path=archive_path,
        kind=kind,
        authoritative=authoritative,
        content_hash=content_hash,
        size_bytes=size_bytes,
        source=source,
    )


def _strict_object(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = strict_json_loads(content.decode("utf-8"))
    except UnicodeError as exc:
        raise ResearchCtlError(f"{label} must be UTF-8 JSON: {exc}") from exc
    except (
        DuplicateJsonKeyError,
        NonStandardJsonConstantError,
        json.JSONDecodeError,
    ) as exc:
        raise ResearchCtlError(f"{label} is invalid JSON: {exc}") from exc
    except RecursionError as exc:
        raise ResearchCtlError(f"{label} JSON is nested too deeply") from exc
    if not isinstance(value, dict):
        raise ResearchCtlError(f"{label} root must be an object")
    return value


def _snapshot_relative_paths(state: dict[str, Any]) -> list[str]:
    values: list[str] = []
    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        return values
    for stage_bucket in artifacts.values():
        if not isinstance(stage_bucket, dict):
            continue
        for role_bucket in stage_bucket.values():
            if not isinstance(role_bucket, dict):
                continue
            for entry in role_bucket.values():
                revisions = entry.get("revisions") if isinstance(entry, dict) else None
                if not isinstance(revisions, list):
                    continue
                for revision in revisions:
                    snapshot = (
                        revision.get("snapshot_path")
                        if isinstance(revision, dict)
                        else None
                    )
                    if isinstance(snapshot, str):
                        values.append(snapshot)
    return values


def _trace_projection(
    root: Path,
    state: dict[str, Any],
    policy: Policy,
) -> tuple[bytes, bool]:
    module_name = f"{__package__}.trace"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise ResearchCtlError(f"cannot load trace projection: {exc}") from exc
        projection = {
            "schema_version": "1.0",
            "status": "unavailable",
            "nodes": [],
            "edges": [],
            "limitations": [
                "trace module was unavailable when the bundle was exported"
            ],
        }
        return _canonical_json(projection), False
    builder = getattr(module, "build_trace_projection", None)
    if callable(builder):
        projection = builder(root, state, policy)
    else:
        # The trace module intentionally exposes queries over the records module's
        # read-only projection rather than owning a second graph builder. Adapt to
        # that public seam while keeping a future one-shot builder compatible.
        try:
            records = importlib.import_module(f"{__package__}.records")
        except ModuleNotFoundError as exc:
            if exc.name != f"{__package__}.records":
                raise ResearchCtlError(
                    f"cannot load trace record projection: {exc}"
                ) from exc
            projection = {
                "schema_version": "1.0",
                "status": "unavailable",
                "nodes": [],
                "edges": [],
                "limitations": [
                    "record projection module was unavailable when exported"
                ],
            }
            return _canonical_json(projection), False
        inspector = getattr(records, "inspect_record_manifests", None)
        summarizer = getattr(module, "build_trace_summary", None)
        if not callable(inspector) or not callable(summarizer):
            projection = {
                "schema_version": "1.0",
                "status": "unavailable",
                "nodes": [],
                "edges": [],
                "limitations": [
                    "trace modules did not expose the required read-only "
                    "projection seam"
                ],
            }
            return _canonical_json(projection), False
        inspection = inspector(root, state, policy)
        projection = {
            "schema_version": "1.0",
            "status": "available",
            "nodes": list(inspection.nodes),
            "edges": list(inspection.edges),
            "summary": summarizer(inspection),
        }
    if not isinstance(projection, dict):
        raise ResearchCtlError("trace build_trace_projection must return an object")
    return _canonical_json(projection), True


def _versions(
    state: dict[str, Any], policy: Policy, plugin: dict[str, Any]
) -> dict[str, Any]:
    return {
        "state_schema": state.get("schema_version"),
        "workflow": state.get("workflow_version"),
        "runtime_contract": policy.runtime.contract_version,
        "scientific_record_manifest": (
            policy.runtime.scientific_record_manifest_schema_version
        ),
        "adapter_exchange_manifest": (
            policy.runtime.adapter_exchange_manifest_schema_version
        ),
        "adapter_exchange_protocol": policy.runtime.adapter_exchange_protocol_version,
        "plugin": plugin.get("version"),
    }


def _evidence_root(
    *,
    versions: dict[str, Any],
    entries: list[dict[str, Any]],
    limitations: list[str],
) -> str:
    material = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "versions": versions,
        "entries": entries,
        "limitations": limitations,
    }
    return _hash_bytes(_canonical_json(material))


def _tar_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.REGTYPE
    info.mode = FIXED_MODE
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = FIXED_MTIME
    info.size = size
    return info


def _write_deterministic_tar(
    destination: Path,
    manifest_bytes: bytes,
    payloads: list[_Payload],
) -> None:
    temporary_name: str | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
        with tarfile.open(
            temporary_name, mode="w", format=tarfile.GNU_FORMAT
        ) as bundle:
            bundle.addfile(
                _tar_info(MANIFEST_PATH, len(manifest_bytes)),
                io.BytesIO(manifest_bytes),
            )
            for payload in sorted(payloads, key=lambda item: item.path):
                info = _tar_info(payload.path, payload.size_bytes)
                if payload.content is not None:
                    bundle.addfile(info, io.BytesIO(payload.content))
                elif payload.source is not None:
                    with payload.source.open("rb") as stream:
                        bundle.addfile(info, stream)
                else:  # pragma: no cover - construction invariant
                    raise ResearchCtlError(
                        f"audit entry {payload.path!r} has no content source"
                    )
        # Verify the bytes that will actually be published, not only the source
        # files observed before tar creation.
        with tarfile.open(temporary_name, mode="r:") as bundle:
            expected = {payload.path: payload for payload in payloads}
            for member in bundle.getmembers()[1:]:
                stream = bundle.extractfile(member)
                if stream is None:
                    raise ResearchCtlError(
                        f"cannot reread generated audit entry {member.name!r}"
                    )
                content = stream.read()
                payload = expected[member.name]
                if len(content) != payload.size_bytes or _hash_bytes(content) != (
                    payload.content_hash
                ):
                    raise ResearchCtlError(
                        f"audit input changed while exporting {member.name!r}"
                    )
        # Publish without clobbering a path created after the preflight check.
        # The temporary file lives in the same directory, so a hard-link create
        # is an atomic no-replace operation and leaves a complete bundle if the
        # process stops before the temporary name is removed.
        os.link(temporary_name, destination)
        os.unlink(temporary_name)
        temporary_name = None
    except ResearchCtlError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise ResearchCtlError(
            f"cannot create audit bundle {destination}: {exc}"
        ) from exc
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass


def export_bundle(
    root: Path,
    state: dict[str, Any],
    policy: Policy,
    destination: Path,
) -> dict[str, Any]:
    """Export one deterministic archive of canonical state and immutable evidence.

    The returned descriptor is JSON-ready. Export never edits state, memory, source
    artifacts, snapshots, Gate records, or the derived trace projection.
    """

    root = Path(root).resolve()
    requested_destination = Path(destination).expanduser()
    if requested_destination.is_symlink():
        raise ResearchCtlError("audit destination cannot be a symlink")
    destination = requested_destination.resolve()
    try:
        destination.relative_to(root)
    except ValueError:
        pass
    else:
        raise ResearchCtlError(
            "audit destination must be outside the research project"
        )
    if destination.exists():
        raise ResearchCtlError(
            "audit destination must not already exist; choose a new external path"
        )
    state_path = root / STATE_RELATIVE_PATH
    memory_path = root / MEMORY_RELATIVE_PATH
    bundled_policy_path = policy_path()
    bundled_runtime_path = runtime_contract_path()
    plugin_path = PLUGIN_ROOT / ".codex-plugin/plugin.json"
    state_bytes = _read_regular_file(state_path, root=root)
    bundled_state = _strict_object(state_bytes, "project state")
    if bundled_state != state:
        raise ResearchCtlError(
            "provided state does not exactly match the canonical project state file"
        )
    errors, _warnings = validate_state(
        root, state, policy, verify_artifact_integrity=True
    )
    if errors:
        raise ResearchCtlError(
            "cannot export an invalid research workspace: " + "; ".join(errors[:5])
        )

    policy_bytes = _read_regular_file(bundled_policy_path)
    runtime_bytes = _read_regular_file(bundled_runtime_path)
    plugin_bytes = _read_regular_file(plugin_path)
    memory_bytes = _read_regular_file(memory_path, root=root)
    if _strict_object(policy_bytes, "workflow policy") != policy.raw:
        raise ResearchCtlError(
            "provided policy does not match the canonical policy file"
        )
    if _strict_object(runtime_bytes, "runtime contract") != policy.runtime.raw:
        raise ResearchCtlError(
            "provided policy runtime does not match the canonical runtime contract"
        )
    plugin = _strict_object(plugin_bytes, "plugin manifest")
    if not isinstance(plugin.get("version"), str) or not plugin["version"].strip():
        raise ResearchCtlError("plugin manifest version must be a non-empty string")

    trace_bytes, trace_available = _trace_projection(root, state, policy)
    payloads = [
        _payload_from_bytes(STATE_ENTRY_PATH, "state", True, state_bytes),
        _payload_from_bytes(MEMORY_ENTRY_PATH, "memory", False, memory_bytes),
        _payload_from_bytes(POLICY_ENTRY_PATH, "policy", True, policy_bytes),
        _payload_from_bytes(
            RUNTIME_ENTRY_PATH, "runtime_contract", True, runtime_bytes
        ),
        _payload_from_bytes(
            PLUGIN_ENTRY_PATH, "plugin_manifest", True, plugin_bytes
        ),
        _payload_from_bytes(
            TRACE_ENTRY_PATH, "trace_projection", False, trace_bytes
        ),
    ]

    snapshot_paths = _snapshot_relative_paths(state)
    _ensure_unique_paths(snapshot_paths, "state snapshot registry")
    for snapshot_path in snapshot_paths:
        if not snapshot_path.startswith(".research/snapshots/"):
            raise ResearchCtlError(
                "state snapshot path must remain below .research/snapshots/: "
                f"{snapshot_path!r}"
            )
        source = root.joinpath(*PurePosixPath(snapshot_path).parts)
        payloads.append(
            _payload_from_file(
                f"workspace/{snapshot_path}",
                "snapshot",
                True,
                source,
                root=root,
            )
        )

    _ensure_unique_paths(
        [MANIFEST_PATH, *(payload.path for payload in payloads)],
        "audit bundle",
    )
    payloads.sort(key=lambda item: item.path)
    entries = [payload.manifest_entry() for payload in payloads]
    limitations = list(BASE_LIMITATIONS)
    if not trace_available:
        limitations.append("trace_projection_unavailable")
    versions = _versions(state, policy, plugin)
    evidence_root = _evidence_root(
        versions=versions,
        entries=entries,
        limitations=limitations,
    )
    manifest = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "versions": versions,
        "entries": entries,
        "evidence_root": evidence_root,
        "limitations": limitations,
    }
    manifest_bytes = _canonical_json(manifest)
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise ResearchCtlError("audit bundle manifest exceeds the supported size")
    _write_deterministic_tar(destination, manifest_bytes, payloads)
    bundle_hash, bundle_size = _hash_file(destination)
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "path": str(destination),
        "content_hash": bundle_hash,
        "size_bytes": bundle_size,
        "evidence_root": evidence_root,
        "entry_count": len(payloads),
        "versions": versions,
    }


def _base_report(path: Path, expected_root: str | None) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "valid": False,
        "path": str(path),
        "content_hash": None,
        "size_bytes": None,
        "evidence_root": None,
        "expected_root": expected_root,
        "entry_count": 0,
        "versions": {},
        "errors": [],
        "warnings": [],
        "limitations": list(BASE_LIMITATIONS),
    }


def _member_metadata_errors(member: tarfile.TarInfo) -> list[str]:
    errors: list[str] = []
    if not member.isfile():
        errors.append(f"non-regular archive entry is forbidden: {member.name!r}")
        return errors
    if member.mode != FIXED_MODE:
        errors.append(f"archive entry has non-canonical permissions: {member.name!r}")
    if member.mtime != FIXED_MTIME:
        errors.append(f"archive entry has non-canonical timestamp: {member.name!r}")
    if member.uid != 0 or member.gid != 0 or member.uname or member.gname:
        errors.append(f"archive entry has non-canonical ownership: {member.name!r}")
    return errors


def _manifest_errors(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_fields = {
        "bundle_schema_version",
        "versions",
        "entries",
        "evidence_root",
        "limitations",
    }
    if set(manifest) != expected_fields:
        errors.append("audit manifest fields do not match the bundle contract")
    if manifest.get("bundle_schema_version") != BUNDLE_SCHEMA_VERSION:
        errors.append(
            f"unsupported audit bundle schema {manifest.get('bundle_schema_version')!r}"
        )
    if not isinstance(manifest.get("versions"), dict):
        errors.append("audit manifest versions must be an object")
    evidence_root = manifest.get("evidence_root")
    if not isinstance(evidence_root, str) or not SHA256_RE.fullmatch(evidence_root):
        errors.append("audit manifest evidence_root must be sha256:<64 lowercase hex>")
    limitations = manifest.get("limitations")
    if (
        not isinstance(limitations, list)
        or not all(isinstance(item, str) and item for item in limitations)
        or len(limitations) != len(set(limitations))
    ):
        errors.append("audit manifest limitations must be a unique string list")
    elif not set(BASE_LIMITATIONS) <= set(limitations):
        errors.append("audit manifest omits required non-certification limitations")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        errors.append("audit manifest entries must be a list")
        return errors
    expected_entry_fields = {
        "path",
        "kind",
        "authoritative",
        "content_hash",
        "size_bytes",
    }
    for index, entry in enumerate(entries):
        label = f"audit manifest entries[{index}]"
        if not isinstance(entry, dict) or set(entry) != expected_entry_fields:
            errors.append(f"{label} fields do not match the entry contract")
            continue
        if _safe_archive_path(entry.get("path")) is None:
            errors.append(f"{label} has unsafe archive path: {entry.get('path')!r}")
        if not isinstance(entry.get("kind"), str) or not entry["kind"]:
            errors.append(f"{label}.kind must be a non-empty string")
        if not isinstance(entry.get("authoritative"), bool):
            errors.append(f"{label}.authoritative must be a boolean")
        content_hash = entry.get("content_hash")
        if not isinstance(content_hash, str) or not SHA256_RE.fullmatch(content_hash):
            errors.append(f"{label}.content_hash is invalid")
        if type(entry.get("size_bytes")) is not int or entry["size_bytes"] < 0:
            errors.append(f"{label}.size_bytes must be a non-negative integer")
    paths = [
        entry.get("path") for entry in entries if isinstance(entry, dict)
    ]
    if all(isinstance(path, str) for path in paths):
        try:
            _ensure_unique_paths(paths, "audit manifest")
        except ResearchCtlError as exc:
            errors.append(str(exc))
        if paths != sorted(paths):
            errors.append("audit manifest entries must use canonical path order")
    return errors


def _entry_contract_errors(entries: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    fixed = {
        "state": (STATE_ENTRY_PATH, True),
        "memory": (MEMORY_ENTRY_PATH, False),
        "policy": (POLICY_ENTRY_PATH, True),
        "runtime_contract": (RUNTIME_ENTRY_PATH, True),
        "plugin_manifest": (PLUGIN_ENTRY_PATH, True),
        "trace_projection": (TRACE_ENTRY_PATH, False),
    }
    seen_fixed: set[str] = set()
    for entry in entries:
        kind = entry.get("kind")
        path = entry.get("path")
        authoritative = entry.get("authoritative")
        if kind == "snapshot":
            if (
                not isinstance(path, str)
                or not path.startswith("workspace/.research/snapshots/")
                or authoritative is not True
            ):
                errors.append(
                    f"snapshot entry has an invalid path or authority: {path!r}"
                )
            continue
        contract = fixed.get(kind)
        if contract is None:
            errors.append(f"audit manifest uses unknown entry kind {kind!r}")
            continue
        if kind in seen_fixed:
            errors.append(f"audit manifest repeats fixed entry kind {kind!r}")
        seen_fixed.add(kind)
        if (path, authoritative) != contract:
            errors.append(f"audit entry kind {kind!r} has an invalid path or authority")
    missing = set(fixed) - seen_fixed
    if missing:
        errors.append(
            "audit manifest is missing fixed entry kinds: "
            + ", ".join(sorted(missing))
        )
    return errors


@contextmanager
def _bundled_contract_environment(
    policy_file: Path,
    runtime_file: Path,
) -> Iterable[None]:
    names = ("RESEARCHCTL_POLICY", "RESEARCHCTL_RUNTIME_CONTRACT")
    previous = {name: os.environ.get(name) for name in names}
    os.environ["RESEARCHCTL_POLICY"] = str(policy_file)
    os.environ["RESEARCHCTL_RUNTIME_CONTRACT"] = str(runtime_file)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _write_verified_workspace(
    temporary_root: Path,
    contents: dict[str, bytes],
) -> tuple[Path, Path, Path]:
    workspace = temporary_root / "workspace"
    for archive_path, content in contents.items():
        if archive_path == MANIFEST_PATH:
            continue
        target = temporary_root.joinpath(*PurePosixPath(archive_path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return (
        workspace,
        temporary_root / POLICY_ENTRY_PATH,
        temporary_root / RUNTIME_ENTRY_PATH,
    )


def _state_snapshot_errors(
    workspace: Path,
    state: dict[str, Any],
    entries: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    manifest_snapshots = {
        entry["path"]
        for entry in entries
        if entry.get("kind") == "snapshot" and isinstance(entry.get("path"), str)
    }
    state_paths = _snapshot_relative_paths(state)
    expected_archive_paths = {f"workspace/{path}" for path in state_paths}
    if manifest_snapshots != expected_archive_paths:
        missing = expected_archive_paths - manifest_snapshots
        extra = manifest_snapshots - expected_archive_paths
        if missing:
            errors.append(
                "bundle omits state-registered historical snapshots: "
                + ", ".join(sorted(missing))
            )
        if extra:
            errors.append(
                "bundle contains snapshots absent from state: "
                + ", ".join(sorted(extra))
            )

    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        return errors
    for stage, stage_bucket in artifacts.items():
        if not isinstance(stage_bucket, dict):
            continue
        for role, role_bucket in stage_bucket.items():
            if not isinstance(role_bucket, dict):
                continue
            for artifact_id, artifact in role_bucket.items():
                revisions = (
                    artifact.get("revisions")
                    if isinstance(artifact, dict)
                    else None
                )
                if not isinstance(revisions, list):
                    continue
                for index, revision in enumerate(revisions):
                    if not isinstance(revision, dict):
                        continue
                    label = f"artifacts.{stage}.{role}.{artifact_id}.revisions[{index}]"
                    snapshot_path = revision.get("snapshot_path")
                    if _safe_archive_path(snapshot_path) is None:
                        errors.append(f"{label}.snapshot_path is unsafe")
                        continue
                    path = workspace.joinpath(*PurePosixPath(snapshot_path).parts)
                    if not path.is_file() or path.is_symlink():
                        errors.append(f"{label} snapshot is missing or not regular")
                        continue
                    actual_hash, actual_size = _hash_file(path)
                    if actual_hash != revision.get("content_hash") or actual_size != (
                        revision.get("size_bytes")
                    ):
                        errors.append(
                            f"{label} snapshot differs from registered hash or size"
                        )
    return errors


def _verify_semantics(
    contents: dict[str, bytes],
    entries: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    versions: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="research-audit-verify-") as directory:
        temporary_root = Path(directory)
        workspace, bundled_policy_path, bundled_runtime_path = (
            _write_verified_workspace(temporary_root, contents)
        )
        try:
            state = _strict_object(contents[STATE_ENTRY_PATH], "bundled state")
            plugin = _strict_object(
                contents[PLUGIN_ENTRY_PATH], "bundled plugin manifest"
            )
            trace = _strict_object(
                contents[TRACE_ENTRY_PATH], "bundled trace projection"
            )
            if not isinstance(trace.get("schema_version"), str):
                errors.append("bundled trace projection has no schema_version")
            with _bundled_contract_environment(
                bundled_policy_path, bundled_runtime_path
            ):
                bundled_policy = load_policy()
        except ResearchCtlError as exc:
            return versions, [str(exc)], warnings

        versions = _versions(state, bundled_policy, plugin)
        trace_status = trace.get("status")
        if trace_status == "available":
            rebuilt_trace, trace_available = _trace_projection(
                workspace, state, bundled_policy
            )
            if not trace_available or rebuilt_trace != contents[TRACE_ENTRY_PATH]:
                errors.append(
                    "bundled trace projection does not match bundled evidence"
                )
        elif trace_status == "unavailable":
            warnings.append(
                "bundled trace projection was unavailable at export time"
            )
        else:
            errors.append(
                "bundled trace projection status must be available or unavailable"
            )
        state_snapshot_errors = _state_snapshot_errors(workspace, state, entries)
        errors.extend(state_snapshot_errors)
        try:
            (workspace / bundled_policy.artifact_root).mkdir(
                parents=True, exist_ok=True
            )
            (workspace / bundled_policy.snapshot_root).mkdir(
                parents=True, exist_ok=True
            )
        except OSError as exc:
            errors.append(f"cannot prepare offline verification roots: {exc}")
            return versions, errors, warnings
        state_errors, state_warnings = validate_state(
            workspace,
            state,
            bundled_policy,
            verify_artifact_integrity=False,
        )
        errors.extend(state_errors)
        warnings.extend(
            warning
            for warning in state_warnings
            if not warning.startswith("Git worktree not detected")
            and ".research/ is not present" not in warning
        )
    return versions, errors, warnings


def verify_bundle(
    path: Path,
    expected_root: str | None = None,
) -> dict[str, Any]:
    """Verify one bundle without consulting its source workspace.

    ``expected_root`` is an externally retained ``sha256:...`` evidence root. A
    self-consistent archive proves mechanical integrity only when that root is
    pinned outside the archive; it never authenticates scientific or human claims.
    """

    bundle_path = Path(path).expanduser().resolve()
    report = _base_report(bundle_path, expected_root)
    errors: list[str] = report["errors"]
    try:
        bundle_hash, bundle_size = _hash_file(bundle_path)
        report["content_hash"] = bundle_hash
        report["size_bytes"] = bundle_size
        with tarfile.open(bundle_path, mode="r:") as bundle:
            members = bundle.getmembers()
            if len(members) > MAX_ARCHIVE_ENTRIES:
                errors.append("audit bundle has too many archive entries")
                return report
            names: list[str] = []
            seen: set[str] = set()
            folded: dict[str, str] = {}
            for member in members:
                name = member.name
                if _safe_archive_path(name) is None:
                    errors.append(f"unsafe archive path: {name!r}")
                if name in seen:
                    errors.append(f"duplicate archive path: {name!r}")
                seen.add(name)
                key = _collision_key(name)
                prior = folded.setdefault(key, name)
                if prior != name:
                    errors.append(
                        "case-insensitive archive path collision: "
                        f"{prior!r} and {name!r}"
                    )
                errors.extend(_member_metadata_errors(member))
                names.append(name)
            if names.count(MANIFEST_PATH) != 1:
                errors.append("audit bundle must contain exactly one manifest")
                return report
            manifest_member = next(
                member for member in members if member.name == MANIFEST_PATH
            )
            if manifest_member.size > MAX_MANIFEST_BYTES:
                errors.append("audit manifest exceeds the supported size")
                return report
            manifest_stream = bundle.extractfile(manifest_member)
            if manifest_stream is None:
                errors.append("audit manifest cannot be read")
                return report
            manifest = _strict_object(manifest_stream.read(), "audit manifest")
            errors.extend(_manifest_errors(manifest))
            entries = manifest.get("entries")
            if not isinstance(entries, list) or any(
                not isinstance(entry, dict) for entry in entries
            ):
                return report
            errors.extend(_entry_contract_errors(entries))
            registered_names = [entry.get("path") for entry in entries]
            if not all(isinstance(name, str) for name in registered_names):
                return report
            expected_names = [MANIFEST_PATH, *registered_names]
            missing = set(expected_names) - set(names)
            extra = set(names) - set(expected_names)
            if missing:
                errors.append(
                    "missing registered entries: " + ", ".join(sorted(missing))
                )
            if extra:
                errors.append("unregistered entries: " + ", ".join(sorted(extra)))
            if names != expected_names:
                errors.append("archive entries do not use canonical fixed order")

            contents: dict[str, bytes] = {}
            members_by_name = {
                member.name: member
                for member in members
                if member.name not in contents
            }
            for entry in entries:
                entry_path = entry["path"]
                member = members_by_name.get(entry_path)
                if member is None or not member.isfile():
                    continue
                stream = bundle.extractfile(member)
                if stream is None:
                    errors.append(f"cannot read registered entry {entry_path!r}")
                    continue
                content = stream.read()
                contents[entry_path] = content
                if len(content) != entry.get("size_bytes"):
                    errors.append(f"content size mismatch for {entry_path!r}")
                if _hash_bytes(content) != entry.get("content_hash"):
                    errors.append(f"content hash mismatch for {entry_path!r}")

            versions = manifest.get("versions")
            limitations = manifest.get("limitations")
            if isinstance(versions, dict) and isinstance(limitations, list):
                computed_root = _evidence_root(
                    versions=versions,
                    entries=entries,
                    limitations=limitations,
                )
                report["evidence_root"] = manifest.get("evidence_root")
                if computed_root != manifest.get("evidence_root"):
                    errors.append(
                        "audit manifest evidence root does not match its content"
                    )
                if (
                    expected_root is not None
                    and manifest.get("evidence_root") != expected_root
                ):
                    errors.append(
                        "expected evidence root does not match the bundled "
                        "evidence root"
                    )
                report["limitations"] = limitations
            report["entry_count"] = len(entries)

            required_payloads = {
                STATE_ENTRY_PATH,
                POLICY_ENTRY_PATH,
                RUNTIME_ENTRY_PATH,
                PLUGIN_ENTRY_PATH,
                TRACE_ENTRY_PATH,
                MEMORY_ENTRY_PATH,
            }
            if not errors and required_payloads <= set(contents):
                semantic_versions, semantic_errors, semantic_warnings = (
                    _verify_semantics(contents, entries)
                )
                report["versions"] = semantic_versions
                errors.extend(semantic_errors)
                report["warnings"].extend(semantic_warnings)
                if isinstance(versions, dict) and semantic_versions != versions:
                    errors.append(
                        "bundled version declarations do not match bundled contracts"
                    )
            elif isinstance(versions, dict):
                report["versions"] = versions
    except ResearchCtlError as exc:
        errors.append(str(exc))
    except (OSError, tarfile.TarError, EOFError) as exc:
        errors.append(f"cannot read audit bundle: {exc}")
    except (KeyError, TypeError, ValueError, RecursionError) as exc:
        errors.append(f"malformed audit bundle: {exc}")
    report["valid"] = not errors
    return report
