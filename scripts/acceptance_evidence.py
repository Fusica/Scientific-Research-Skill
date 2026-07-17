"""Retained evidence-pack resolver for capability acceptance.

The report is only a claim envelope.  Current and Benchmark-verified claims are
qualified against this independently hashed manifest and its materialized files.
Every reference is artifact-ID based so moving the pack does not change identity.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

if __package__:
    from .researchctl_core.constants import SHA256_RE
    from .researchctl_core.jsonutil import (
        DuplicateJsonKeyError,
        NonStandardJsonConstantError,
        strict_json_loads,
    )
else:
    from researchctl_core.constants import SHA256_RE
    from researchctl_core.jsonutil import (
        DuplicateJsonKeyError,
        NonStandardJsonConstantError,
        strict_json_loads,
    )


PACK_SCHEMA_VERSION = "1.0"
PACK_DECLARATION_FIELDS = {"manifest_ref", "content_hash", "size_bytes"}
PACK_MANIFEST_FIELDS = {"schema_version", "artifacts"}
PACK_ARTIFACT_FIELDS = {
    "artifact_id",
    "path",
    "media_type",
    "content_hash",
    "size_bytes",
}
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_ARTIFACTS = 4096
ARTIFACT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class EvidencePackError(ValueError):
    """The retained evidence pack is missing, unsafe, or inconsistent."""


@dataclass(frozen=True)
class EvidenceArtifact:
    artifact_id: str
    path: Path
    media_type: str
    content_hash: str
    size_bytes: int


@dataclass(frozen=True)
class ResolvedEvidence:
    artifact: EvidenceArtifact
    pointer: str
    value: Any | None
    verified_bytes: bytes


def _hash_file(path: Path, *, maximum: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise EvidencePackError(
                    f"evidence path must remain a regular file: {path}"
                )
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(block)
                if size > maximum:
                    raise EvidencePackError(
                        f"evidence file exceeds the {maximum}-byte limit: {path}"
                    )
                digest.update(block)
            after = os.fstat(stream.fileno())
    except EvidencePackError:
        raise
    except OSError as exc:
        raise EvidencePackError(f"cannot read evidence file {path}: {exc}") from exc
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
        raise EvidencePackError(f"evidence file changed while hashing: {path}")
    return f"sha256:{digest.hexdigest()}", size


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise EvidencePackError(f"{label} must be a non-empty POSIX relative path")
    if "#" in value or "?" in value or re.match(r"^[A-Za-z]:", value):
        raise EvidencePackError(f"{label} contains reserved path syntax")
    if unicodedata.normalize("NFC", value) != value:
        raise EvidencePackError(f"{label} must use NFC Unicode normalization")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise EvidencePackError(f"{label} must be a normalized POSIX relative path")
    return path


def _safe_file(root: Path, value: Any, label: str) -> Path:
    relative = _safe_relative(value, label)
    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*relative.parts)
    cursor = resolved_root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise EvidencePackError(f"{label} cannot traverse a symlink")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise EvidencePackError(f"{label} is unavailable or escapes the pack: {value}") from exc
    if candidate.is_symlink() or not resolved.is_file():
        raise EvidencePackError(f"{label} must be a regular non-symlink file")
    return resolved


def _read_verified_bytes(
    path: Path,
    label: str,
    *,
    maximum: int,
    expected_hash: str | None = None,
    expected_size: int | None = None,
) -> bytes:
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    size = 0
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise EvidencePackError(f"{label} must remain a regular file")
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(block)
                if size > maximum:
                    raise EvidencePackError(
                        f"{label} exceeds the {maximum}-byte limit"
                    )
                digest.update(block)
                chunks.append(block)
            after = os.fstat(stream.fileno())
    except EvidencePackError:
        raise
    except OSError as exc:
        raise EvidencePackError(f"cannot read {label}: {exc}") from exc
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
        raise EvidencePackError(f"{label} changed while being read")
    actual_hash = f"sha256:{digest.hexdigest()}"
    if expected_hash is not None and (actual_hash, size) != (
        expected_hash,
        expected_size,
    ):
        raise EvidencePackError(f"{label} no longer matches its retained hash or size")
    return b"".join(chunks)


def _strict_json_bytes(content: bytes, label: str) -> Any:
    try:
        return strict_json_loads(content.decode("utf-8"))
    except (
        UnicodeError,
        json.JSONDecodeError,
        DuplicateJsonKeyError,
        NonStandardJsonConstantError,
        RecursionError,
    ) as exc:
        raise EvidencePackError(f"{label} must be strict JSON: {exc}") from exc


def _json_pointer(value: Any, pointer: str, label: str) -> Any:
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        raise EvidencePackError(f"{label} JSON pointer must be empty or start with /")
    current = value
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if "~" in raw_token.replace("~0", "").replace("~1", ""):
            raise EvidencePackError(f"{label} contains an invalid JSON pointer escape")
        if isinstance(current, dict):
            if token not in current:
                raise EvidencePackError(f"{label} JSON pointer does not resolve")
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or (token.startswith("0") and token != "0"):
                raise EvidencePackError(f"{label} JSON pointer index is invalid")
            index = int(token)
            if index >= len(current):
                raise EvidencePackError(f"{label} JSON pointer index is out of range")
            current = current[index]
        else:
            raise EvidencePackError(f"{label} JSON pointer traverses a scalar")
    return current


class EvidencePack:
    """An eagerly verified, relocatable map of retained acceptance evidence."""

    def __init__(
        self,
        *,
        root: Path,
        manifest_path: Path,
        manifest_hash: str,
        artifacts: dict[str, EvidenceArtifact],
    ) -> None:
        self.root = root
        self.manifest_path = manifest_path
        self.manifest_hash = manifest_hash
        self.artifacts = artifacts

    @classmethod
    def load(cls, report_path: Path, declaration: Any) -> "EvidencePack":
        """Load relative to the report's current physical parent."""

        return cls.load_from_root(report_path.resolve().parent, declaration)

    @classmethod
    def load_from_root(cls, root: Path, declaration: Any) -> "EvidencePack":
        """Load relative to a report root fixed by the caller's stable read."""

        if not isinstance(declaration, dict) or set(declaration) != PACK_DECLARATION_FIELDS:
            raise EvidencePackError(
                "evidence_pack must contain exactly manifest_ref, content_hash, size_bytes"
            )
        expected_hash = declaration.get("content_hash")
        expected_size = declaration.get("size_bytes")
        if not isinstance(expected_hash, str) or SHA256_RE.fullmatch(expected_hash) is None:
            raise EvidencePackError("evidence_pack.content_hash must be sha256:<64 lowercase hex>")
        if type(expected_size) is not int or expected_size < 0:
            raise EvidencePackError("evidence_pack.size_bytes must be a non-negative integer")
        root = root.resolve(strict=True)
        manifest_path = _safe_file(
            root, declaration.get("manifest_ref"), "evidence_pack.manifest_ref"
        )
        manifest_bytes = _read_verified_bytes(
            manifest_path,
            "evidence-pack manifest",
            maximum=MAX_MANIFEST_BYTES,
            expected_hash=expected_hash,
            expected_size=expected_size,
        )
        actual_hash = f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"
        actual_size = len(manifest_bytes)
        if (actual_hash, actual_size) != (expected_hash, expected_size):
            raise EvidencePackError("evidence-pack manifest hash or size does not match")
        manifest = _strict_json_bytes(manifest_bytes, "evidence-pack manifest")
        if not isinstance(manifest, dict) or set(manifest) != PACK_MANIFEST_FIELDS:
            raise EvidencePackError(
                "evidence-pack manifest must contain exactly schema_version and artifacts"
            )
        if manifest.get("schema_version") != PACK_SCHEMA_VERSION:
            raise EvidencePackError(
                f"evidence-pack schema_version must be {PACK_SCHEMA_VERSION!r}"
            )
        raw_artifacts = manifest.get("artifacts")
        if not isinstance(raw_artifacts, list) or not 1 <= len(raw_artifacts) <= MAX_ARTIFACTS:
            raise EvidencePackError(
                f"evidence-pack artifacts must contain 1 to {MAX_ARTIFACTS} entries"
            )
        artifacts: dict[str, EvidenceArtifact] = {}
        paths: set[Path] = {manifest_path}
        collision_keys: set[str] = {
            unicodedata.normalize(
                "NFC", manifest_path.relative_to(root).as_posix()
            ).casefold()
        }
        hashes: dict[str, str] = {}
        total_size = 0
        for index, raw in enumerate(raw_artifacts):
            label = f"evidence-pack artifacts[{index}]"
            if not isinstance(raw, dict) or set(raw) != PACK_ARTIFACT_FIELDS:
                raise EvidencePackError(f"{label} has invalid fields")
            artifact_id = raw.get("artifact_id")
            media_type = raw.get("media_type")
            content_hash = raw.get("content_hash")
            size_bytes = raw.get("size_bytes")
            if not isinstance(artifact_id, str) or ARTIFACT_ID_RE.fullmatch(artifact_id) is None:
                raise EvidencePackError(f"{label}.artifact_id is invalid")
            if artifact_id in artifacts:
                raise EvidencePackError(f"duplicate evidence artifact_id {artifact_id!r}")
            if not isinstance(media_type, str) or not media_type.strip():
                raise EvidencePackError(f"{label}.media_type must be non-empty")
            if not isinstance(content_hash, str) or SHA256_RE.fullmatch(content_hash) is None:
                raise EvidencePackError(f"{label}.content_hash is invalid")
            if type(size_bytes) is not int or not 0 <= size_bytes <= MAX_ARTIFACT_BYTES:
                raise EvidencePackError(f"{label}.size_bytes is invalid")
            relative_path = _safe_relative(raw.get("path"), f"{label}.path")
            collision_key = unicodedata.normalize(
                "NFC", relative_path.as_posix()
            ).casefold()
            if collision_key in collision_keys:
                raise EvidencePackError(
                    f"evidence-pack path has a case or Unicode collision: {relative_path}"
                )
            collision_keys.add(collision_key)
            path = _safe_file(root, relative_path.as_posix(), f"{label}.path")
            if path in paths:
                raise EvidencePackError(f"evidence-pack path is duplicated: {path}")
            paths.add(path)
            actual = _hash_file(path, maximum=MAX_ARTIFACT_BYTES)
            if actual != (content_hash, size_bytes):
                raise EvidencePackError(f"{label} content hash or size does not match")
            total_size += size_bytes
            if total_size > MAX_TOTAL_BYTES:
                raise EvidencePackError(
                    f"evidence-pack exceeds the {MAX_TOTAL_BYTES}-byte total limit"
                )
            owner = hashes.setdefault(content_hash, artifact_id)
            if owner != artifact_id:
                raise EvidencePackError(
                    f"evidence artifacts {owner!r} and {artifact_id!r} reuse identical bytes"
                )
            artifacts[artifact_id] = EvidenceArtifact(
                artifact_id=artifact_id,
                path=path,
                media_type=media_type,
                content_hash=content_hash,
                size_bytes=size_bytes,
            )
        return cls(
            root=root,
            manifest_path=manifest_path,
            manifest_hash=actual_hash,
            artifacts=artifacts,
        )

    def resolve(self, reference: Any, label: str, *, json_value: bool = False) -> ResolvedEvidence:
        if not isinstance(reference, str) or not reference or reference.count("#") > 1:
            raise EvidencePackError(f"{label} must be artifact-id[#/json/pointer]")
        artifact_id, separator, pointer = reference.partition("#")
        if ARTIFACT_ID_RE.fullmatch(artifact_id) is None:
            raise EvidencePackError(f"{label} artifact ID is invalid")
        artifact = self.artifacts.get(artifact_id)
        if artifact is None:
            raise EvidencePackError(f"{label} references unknown artifact {artifact_id!r}")
        if separator and pointer and not pointer.startswith("/"):
            raise EvidencePackError(f"{label} JSON pointer must start with /")
        needs_json = json_value or bool(separator)
        value: Any | None = None
        content = _read_verified_bytes(
            artifact.path,
            f"evidence artifact {artifact_id!r}",
            maximum=MAX_ARTIFACT_BYTES,
            expected_hash=artifact.content_hash,
            expected_size=artifact.size_bytes,
        )
        if needs_json:
            if artifact.media_type not in {
                "application/json",
                "application/vnd.scientific-research.acceptance+json",
            }:
                raise EvidencePackError(f"{label} requires a JSON evidence artifact")
            document = _strict_json_bytes(
                content, f"evidence artifact {artifact_id!r}"
            )
            value = _json_pointer(document, pointer, label)
        return ResolvedEvidence(
            artifact=artifact,
            pointer=pointer,
            value=value,
            verified_bytes=content,
        )


__all__ = [
    "EvidenceArtifact",
    "EvidencePack",
    "EvidencePackError",
    "PACK_DECLARATION_FIELDS",
    "PACK_SCHEMA_VERSION",
    "ResolvedEvidence",
]
