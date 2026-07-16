"""Validate registered scientific-record manifests without owning research state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import resolve_artifact_path
from .constants import ARTIFACT_ID_RE, ARTIFACT_ROLE_RE, Policy, ResearchCtlError
from .jsonutil import (
    DuplicateJsonKeyError,
    NonStandardJsonConstantError,
    strict_json_loads,
)


@dataclass(frozen=True)
class PendingRecordManifest:
    """A source file being checked before its artifact revision is published."""

    stage: str
    artifact_id: str
    path: Path


@dataclass(frozen=True)
class RecordInspection:
    """Stable diagnostics returned through the scientific-record seam."""

    errors: tuple[str, ...]
    record_count: int


@dataclass(frozen=True)
class _ManifestRevision:
    stage: str
    artifact_id: str
    revision: int
    path: Path
    pending: bool = False

    @property
    def label(self) -> str:
        suffix = " pending" if self.pending else ""
        return (
            f"record manifest {self.stage}.{self.artifact_id} "
            f"r{self.revision}{suffix}"
        )


@dataclass(frozen=True)
class _ParsedManifest:
    source: _ManifestRevision
    records: tuple[dict[str, Any], ...]


def _field_errors(
    value: Any,
    expected: tuple[str, ...],
    label: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(value, dict):
        return None, [f"{label} must be an object"]
    errors: list[str] = []
    configured = set(expected)
    missing = configured - set(value)
    extra = set(value) - configured
    if missing:
        errors.append(f"{label} missing fields: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"{label} has unknown fields: {', '.join(sorted(extra))}")
    return value, errors


def _load_json(source: _ManifestRevision, errors: list[str]) -> Any | None:
    try:
        return strict_json_loads(source.path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"{source.label} file is missing: {source.path}")
    except (OSError, UnicodeError) as exc:
        errors.append(f"cannot read {source.label}: {exc}")
    except (DuplicateJsonKeyError, NonStandardJsonConstantError) as exc:
        errors.append(f"{source.label} contains {exc}")
    except json.JSONDecodeError as exc:
        errors.append(
            f"{source.label} must be JSON: line {exc.lineno}, column {exc.colno}: "
            f"{exc.msg}"
        )
    except RecursionError:
        errors.append(f"{source.label} JSON is nested too deeply")
    return None


def _artifact_revision_exists(
    state: dict[str, Any],
    *,
    stage: str,
    role: str,
    artifact_id: str,
    revision: int,
) -> bool:
    artifacts = state.get("artifacts")
    stage_bucket = artifacts.get(stage) if isinstance(artifacts, dict) else None
    role_bucket = stage_bucket.get(role) if isinstance(stage_bucket, dict) else None
    entry = role_bucket.get(artifact_id) if isinstance(role_bucket, dict) else None
    revisions = entry.get("revisions") if isinstance(entry, dict) else None
    if not isinstance(revisions, list):
        return False
    return any(
        isinstance(item, dict) and item.get("revision") == revision
        for item in revisions
    )


def _validate_source(
    value: Any,
    *,
    label: str,
    stage: str,
    state: dict[str, Any],
    policy: Policy,
    errors: list[str],
) -> None:
    source, field_problems = _field_errors(
        value,
        policy.runtime.scientific_record_source_fields,
        label,
    )
    errors.extend(field_problems)
    if source is None or field_problems:
        return
    role = source.get("artifact_role")
    artifact_id = source.get("artifact_id")
    revision = source.get("revision")
    locator = source.get("locator")
    if not isinstance(role, str) or not ARTIFACT_ROLE_RE.fullmatch(role):
        errors.append(f"{label}.artifact_role must use lower_snake_case")
    elif role == policy.runtime.scientific_record_artifact_role:
        errors.append(f"{label}.artifact_role cannot reference the record manifest")
    if not isinstance(artifact_id, str) or not ARTIFACT_ID_RE.fullmatch(artifact_id):
        errors.append(f"{label}.artifact_id has an invalid format")
    if type(revision) is not int or revision <= 0:
        errors.append(f"{label}.revision must be a positive integer")
    if not isinstance(locator, str) or not locator.strip():
        errors.append(f"{label}.locator must be a non-empty string")
    if (
        isinstance(role, str)
        and ARTIFACT_ROLE_RE.fullmatch(role)
        and isinstance(artifact_id, str)
        and ARTIFACT_ID_RE.fullmatch(artifact_id)
        and type(revision) is int
        and revision > 0
        and not _artifact_revision_exists(
            state,
            stage=stage,
            role=role,
            artifact_id=artifact_id,
            revision=revision,
        )
    ):
        errors.append(
            f"{label} references unregistered artifact revision "
            f"{stage}.{role}.{artifact_id} r{revision}"
        )


def _validate_relations(
    value: Any,
    *,
    label: str,
    policy: Policy,
    errors: list[str],
) -> None:
    if not isinstance(value, list):
        errors.append(f"{label} must be a list")
        return
    seen: set[tuple[str, str]] = set()
    for index, candidate in enumerate(value):
        relation_label = f"{label}[{index}]"
        relation, field_problems = _field_errors(
            candidate,
            policy.runtime.scientific_record_relation_fields,
            relation_label,
        )
        errors.extend(field_problems)
        if relation is None or field_problems:
            continue
        relation_kind = relation.get("relation")
        target_id = relation.get("target_id")
        if relation_kind not in policy.runtime.scientific_record_relation_kinds:
            errors.append(
                f"{relation_label}.relation {relation_kind!r} is unsupported"
            )
        if not isinstance(target_id, str) or not ARTIFACT_ID_RE.fullmatch(target_id):
            errors.append(f"{relation_label}.target_id has an invalid format")
        if isinstance(relation_kind, str) and isinstance(target_id, str):
            key = (relation_kind, target_id)
            if key in seen:
                errors.append(f"{relation_label} duplicates relation {key!r}")
            seen.add(key)


def _parse_manifest(
    source: _ManifestRevision,
    *,
    state: dict[str, Any],
    policy: Policy,
    errors: list[str],
) -> _ParsedManifest | None:
    raw = _load_json(source, errors)
    manifest, field_problems = _field_errors(
        raw,
        policy.runtime.scientific_record_manifest_fields,
        source.label,
    )
    errors.extend(field_problems)
    if manifest is None or field_problems:
        return None
    if manifest.get("schema_version") != (
        policy.runtime.scientific_record_manifest_schema_version
    ):
        errors.append(
            f"{source.label}.schema_version must be "
            f"{policy.runtime.scientific_record_manifest_schema_version!r}"
        )
    if manifest.get("stage") != source.stage:
        errors.append(
            f"{source.label}.stage must match registered stage {source.stage!r}"
        )
    records = manifest.get("records")
    if not isinstance(records, list) or not records:
        errors.append(f"{source.label}.records must be a non-empty list")
        return None

    materialized: list[dict[str, Any]] = []
    prior_kinds: dict[str, str] = {}
    superseded_by: dict[str, str] = {}
    for index, candidate in enumerate(records):
        record_label = f"{source.label}.records[{index}]"
        record, record_problems = _field_errors(
            candidate,
            policy.runtime.scientific_record_fields,
            record_label,
        )
        errors.extend(record_problems)
        if record is None or record_problems:
            continue
        record_id = record.get("record_id")
        record_kind = record.get("record_kind")
        valid_id = bool(
            isinstance(record_id, str) and ARTIFACT_ID_RE.fullmatch(record_id)
        )
        if not valid_id:
            errors.append(f"{record_label}.record_id has an invalid format")
        elif record_id in prior_kinds:
            errors.append(f"{record_label}.record_id {record_id!r} is duplicated")
        if record_kind not in policy.runtime.scientific_record_kinds:
            errors.append(
                f"{record_label}.record_kind {record_kind!r} is unsupported"
            )
        _validate_source(
            record.get("source"),
            label=f"{record_label}.source",
            stage=source.stage,
            state=state,
            policy=policy,
            errors=errors,
        )
        supersedes = record.get("supersedes")
        if supersedes is not None:
            if not isinstance(supersedes, str) or not ARTIFACT_ID_RE.fullmatch(
                supersedes
            ):
                errors.append(
                    f"{record_label}.supersedes must be null or a valid record ID"
                )
            elif supersedes not in prior_kinds:
                errors.append(
                    f"{record_label}.supersedes must reference an earlier record "
                    "in this manifest"
                )
            else:
                if prior_kinds[supersedes] != record_kind:
                    errors.append(
                        f"{record_label}.supersedes must reference the same record_kind"
                    )
                if supersedes in superseded_by:
                    errors.append(
                        f"{record_label}.supersedes branches correction lineage already "
                        f"continued by {superseded_by[supersedes]}"
                    )
                elif isinstance(record_id, str):
                    superseded_by[supersedes] = record_id
        _validate_relations(
            record.get("relations"),
            label=f"{record_label}.relations",
            policy=policy,
            errors=errors,
        )
        if valid_id and isinstance(record_id, str) and isinstance(record_kind, str):
            prior_kinds.setdefault(record_id, record_kind)
        materialized.append(record)
    return _ParsedManifest(source=source, records=tuple(materialized))


def _registered_manifest_revisions(
    root: Path,
    state: dict[str, Any],
    policy: Policy,
) -> list[_ManifestRevision]:
    sources: list[_ManifestRevision] = []
    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        return sources
    role = policy.runtime.scientific_record_artifact_role
    for stage in policy.stage_order:
        stage_bucket = artifacts.get(stage)
        role_bucket = stage_bucket.get(role) if isinstance(stage_bucket, dict) else None
        if not isinstance(role_bucket, dict):
            continue
        for artifact_id, entry in role_bucket.items():
            revisions = entry.get("revisions") if isinstance(entry, dict) else None
            if not isinstance(artifact_id, str) or not isinstance(revisions, list):
                continue
            for revision in revisions:
                if not isinstance(revision, dict):
                    continue
                number = revision.get("revision")
                snapshot_path = revision.get("snapshot_path")
                if type(number) is not int or not isinstance(snapshot_path, str):
                    continue
                try:
                    path = resolve_artifact_path(root, snapshot_path)
                except ResearchCtlError:
                    continue
                sources.append(
                    _ManifestRevision(
                        stage=stage,
                        artifact_id=artifact_id,
                        revision=number,
                        path=path,
                    )
                )
    return sources


def _validate_relation_endpoints(
    manifests: list[_ParsedManifest],
    *,
    owners: dict[str, tuple[str, str]],
    policy: Policy,
    errors: list[str],
) -> None:
    for manifest in manifests:
        for record_index, record in enumerate(manifest.records):
            source_kind = record.get("record_kind")
            if source_kind not in policy.runtime.scientific_record_kinds:
                continue
            relations = record.get("relations")
            if not isinstance(relations, list):
                continue
            for relation_index, relation in enumerate(relations):
                if not isinstance(relation, dict):
                    continue
                relation_kind = relation.get("relation")
                target_id = relation.get("target_id")
                if (
                    not isinstance(relation_kind, str)
                    or relation_kind
                    not in policy.runtime.scientific_record_relation_signatures
                    or not isinstance(target_id, str)
                    or not ARTIFACT_ID_RE.fullmatch(target_id)
                ):
                    continue
                label = (
                    f"{manifest.source.label}.records[{record_index}].relations"
                    f"[{relation_index}]"
                )
                target = owners.get(target_id)
                if target is None:
                    errors.append(
                        f"{label}.target_id references unknown record {target_id!r}"
                    )
                    continue
                target_kind, _target_label = target
                source_kinds, target_kinds = (
                    policy.runtime.scientific_record_relation_signatures[
                        relation_kind
                    ]
                )
                if source_kind not in source_kinds or target_kind not in target_kinds:
                    errors.append(
                        f"{label} relation {relation_kind!r} does not allow "
                        f"{source_kind} -> {target_kind}"
                    )


def inspect_record_manifests(
    root: Path,
    state: dict[str, Any],
    policy: Policy,
    *,
    pending: PendingRecordManifest | None = None,
) -> RecordInspection:
    """Inspect every record-manifest revision through one read-only interface.

    The implementation validates machine shape, exact source revisions, stable IDs,
    correction lineage, relation endpoints, and append-only history. It deliberately
    does not judge the scientific truth of a relation or store records in
    ``state.json``.
    """

    errors: list[str] = []
    sources = _registered_manifest_revisions(root, state, policy)
    if pending is not None:
        existing_numbers = [
            item.revision
            for item in sources
            if item.stage == pending.stage and item.artifact_id == pending.artifact_id
        ]
        sources.append(
            _ManifestRevision(
                stage=pending.stage,
                artifact_id=pending.artifact_id,
                revision=(max(existing_numbers, default=0) + 1),
                path=pending.path,
                pending=True,
            )
        )

    parsed: list[_ParsedManifest] = []
    for source in sorted(
        sources, key=lambda item: (item.stage, item.artifact_id, item.revision)
    ):
        candidate = _parse_manifest(
            source,
            state=state,
            policy=policy,
            errors=errors,
        )
        if candidate is not None:
            parsed.append(candidate)

    by_artifact: dict[tuple[str, str], list[_ParsedManifest]] = {}
    for manifest in parsed:
        by_artifact.setdefault(
            (manifest.source.stage, manifest.source.artifact_id), []
        ).append(manifest)
    current: list[_ParsedManifest] = []
    for history in by_artifact.values():
        history.sort(key=lambda item: item.source.revision)
        for previous, candidate in zip(history, history[1:]):
            previous_records = previous.records
            if (
                len(candidate.records) < len(previous_records)
                or candidate.records[: len(previous_records)] != previous_records
            ):
                errors.append(
                    f"{candidate.source.label} must preserve prior records as an "
                    "append-only prefix"
                )
        current.append(history[-1])

    owners: dict[str, tuple[str, str]] = {}
    for manifest in current:
        for index, record in enumerate(manifest.records):
            record_id = record.get("record_id")
            record_kind = record.get("record_kind")
            if not isinstance(record_id, str) or not ARTIFACT_ID_RE.fullmatch(record_id):
                continue
            label = f"{manifest.source.label}.records[{index}]"
            if record_kind not in policy.runtime.scientific_record_kinds:
                continue
            prior = owners.setdefault(record_id, (record_kind, label))
            if prior[1] != label:
                errors.append(
                    f"{label}.record_id {record_id!r} duplicates project record "
                    f"owned by {prior[1]}"
                )

    _validate_relation_endpoints(
        current,
        owners=owners,
        policy=policy,
        errors=errors,
    )

    return RecordInspection(
        errors=tuple(errors),
        record_count=len(owners),
    )
