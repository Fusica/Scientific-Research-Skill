"""Validate registered scientific-record manifests without owning research state."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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
    """Stable diagnostics and projection returned through the record seam."""

    errors: tuple[str, ...]
    record_count: int
    warnings: tuple[str, ...] = ()
    nodes: tuple[dict[str, Any], ...] = ()
    edges: tuple[dict[str, str], ...] = ()
    diagnostics: dict[str, tuple[Any, ...]] = field(
        default_factory=lambda: {
            "dangling": (),
            "duplicates": (),
            "invalid_supersedes": (),
            "cycles": (),
            "orphans": (),
        }
    )


@dataclass
class _DiagnosticCollector:
    dangling: list[dict[str, str]] = field(default_factory=list)
    duplicates: list[dict[str, str]] = field(default_factory=list)
    invalid_supersedes: list[dict[str, Any]] = field(default_factory=list)
    cycles: list[dict[str, Any]] = field(default_factory=list)


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


_RecordOwner = tuple[str, str, dict[str, Any], _ParsedManifest]


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
    diagnostics: _DiagnosticCollector,
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
                diagnostics.duplicates.append(
                    {
                        "kind": "relation",
                        "identifier": f"{relation_kind}:{target_id}",
                        "location": relation_label,
                        "owner": label,
                    }
                )
            seen.add(key)


def _parse_manifest(
    source: _ManifestRevision,
    *,
    state: dict[str, Any],
    policy: Policy,
    errors: list[str],
    diagnostics: _DiagnosticCollector,
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
            diagnostics.duplicates.append(
                {
                    "kind": "record",
                    "identifier": record_id,
                    "location": record_label,
                    "owner": source.label,
                }
            )
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
                diagnostics.invalid_supersedes.append(
                    {
                        "record_id": record_id,
                        "supersedes": supersedes,
                        "reason": "invalid_id",
                        "location": record_label,
                    }
                )
            elif supersedes not in prior_kinds:
                errors.append(
                    f"{record_label}.supersedes must reference an earlier record "
                    "in this manifest"
                )
                diagnostics.invalid_supersedes.append(
                    {
                        "record_id": record_id,
                        "supersedes": supersedes,
                        "reason": "not_earlier_in_manifest",
                        "location": record_label,
                    }
                )
            else:
                if prior_kinds[supersedes] != record_kind:
                    errors.append(
                        f"{record_label}.supersedes must reference the same record_kind"
                    )
                    diagnostics.invalid_supersedes.append(
                        {
                            "record_id": record_id,
                            "supersedes": supersedes,
                            "reason": "kind_mismatch",
                            "location": record_label,
                        }
                    )
                if supersedes in superseded_by:
                    errors.append(
                        f"{record_label}.supersedes branches correction lineage already "
                        f"continued by {superseded_by[supersedes]}"
                    )
                    diagnostics.invalid_supersedes.append(
                        {
                            "record_id": record_id,
                            "supersedes": supersedes,
                            "reason": "branch",
                            "location": record_label,
                        }
                    )
                elif isinstance(record_id, str):
                    superseded_by[supersedes] = record_id
        _validate_relations(
            record.get("relations"),
            label=f"{record_label}.relations",
            policy=policy,
            errors=errors,
            diagnostics=diagnostics,
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
    owners: dict[str, _RecordOwner],
    policy: Policy,
    errors: list[str],
    diagnostics: _DiagnosticCollector,
) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for manifest in manifests:
        for record_index, record in enumerate(manifest.records):
            source_id = record.get("record_id")
            source_kind = record.get("record_kind")
            if (
                not isinstance(source_id, str)
                or source_kind not in policy.runtime.scientific_record_kinds
            ):
                continue
            source_label = f"{manifest.source.label}.records[{record_index}]"
            owner = owners.get(source_id)
            if owner is None or owner[1] != source_label:
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
                    diagnostics.dangling.append(
                        {
                            "source_id": source_id,
                            "relation": relation_kind,
                            "target_id": target_id,
                            "location": label,
                        }
                    )
                    continue
                target_kind, _target_label, _target_record, _target_manifest = target
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
                    continue
                edge_key = (source_id, relation_kind, target_id)
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append(
                        {
                            "source_id": source_id,
                            "relation": relation_kind,
                            "target_id": target_id,
                        }
                    )
    return edges


def _stable_dicts(values: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    unique: dict[str, dict[str, Any]] = {}
    for value in values:
        key = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        unique.setdefault(key, value)
    return tuple(unique[key] for key in sorted(unique))


def _projection_nodes(
    owners: dict[str, _RecordOwner],
) -> tuple[dict[str, Any], ...]:
    nodes: list[dict[str, Any]] = []
    for record_id in sorted(owners):
        record_kind, _label, record, manifest = owners[record_id]
        source = record.get("source")
        source_projection = (
            {key: source[key] for key in sorted(source)}
            if isinstance(source, dict)
            else {}
        )
        nodes.append(
            {
                "record_id": record_id,
                "record_kind": record_kind,
                "stage": manifest.source.stage,
                "manifest_artifact_id": manifest.source.artifact_id,
                "manifest_revision": manifest.source.revision,
                "pending": manifest.source.pending,
                "source": source_projection,
                "supersedes": record.get("supersedes"),
            }
        )
    return tuple(nodes)


def _supersedes_edges(
    owners: dict[str, _RecordOwner],
) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    for record_id in sorted(owners):
        record_kind, _label, record, _manifest = owners[record_id]
        supersedes = record.get("supersedes")
        target = owners.get(supersedes) if isinstance(supersedes, str) else None
        if target is not None and target[0] == record_kind:
            edges.append(
                {
                    "source_id": record_id,
                    "relation": "supersedes",
                    "target_id": supersedes,
                }
            )
    return edges


def _cycle_components(
    node_ids: tuple[str, ...],
    edges: list[dict[str, str]],
) -> list[tuple[str, ...]]:
    """Return deterministic strongly connected components that contain cycles."""

    adjacency: dict[str, set[str]] = {record_id: set() for record_id in node_ids}
    reverse: dict[str, set[str]] = {record_id: set() for record_id in node_ids}
    self_loops: set[str] = set()
    for edge in edges:
        source = edge["source_id"]
        target = edge["target_id"]
        if source not in adjacency or target not in adjacency:
            continue
        adjacency[source].add(target)
        reverse[target].add(source)
        if source == target:
            self_loops.add(source)

    visited: set[str] = set()
    finish_order: list[str] = []
    for start in sorted(node_ids):
        if start in visited:
            continue
        visited.add(start)
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            current, expanded = stack.pop()
            if expanded:
                finish_order.append(current)
                continue
            stack.append((current, True))
            for neighbor in sorted(adjacency[current], reverse=True):
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append((neighbor, False))

    assigned: set[str] = set()
    cycles: list[tuple[str, ...]] = []
    for start in reversed(finish_order):
        if start in assigned:
            continue
        assigned.add(start)
        component: list[str] = []
        component_stack = [start]
        while component_stack:
            current = component_stack.pop()
            component.append(current)
            for neighbor in sorted(reverse[current], reverse=True):
                if neighbor not in assigned:
                    assigned.add(neighbor)
                    component_stack.append(neighbor)
        normalized = tuple(sorted(component))
        if len(normalized) > 1 or normalized[0] in self_loops:
            cycles.append(normalized)
    return sorted(cycles)


def _diagnose_cycles(
    node_ids: tuple[str, ...],
    relation_edges: list[dict[str, str]],
    *,
    errors: list[str],
    warnings: list[str],
    diagnostics: _DiagnosticCollector,
) -> None:
    derived_edges = [
        edge for edge in relation_edges if edge["relation"] == "derived_from"
    ]
    for component in _cycle_components(node_ids, derived_edges):
        records = ", ".join(component)
        errors.append(f"derived_from relation cycle detected among records: {records}")
        diagnostics.cycles.append(
            {
                "record_ids": list(component),
                "relations": ["derived_from"],
                "severity": "error",
            }
        )

    for component in _cycle_components(node_ids, relation_edges):
        member_set = set(component)
        relations = sorted(
            {
                edge["relation"]
                for edge in relation_edges
                if edge["source_id"] in member_set
                and edge["target_id"] in member_set
            }
        )
        if relations == ["derived_from"]:
            continue
        records = ", ".join(component)
        warnings.append(
            "record relation cycle detected among records "
            f"{records}; preserved as a diagnostic because only derived_from "
            "cycles are mechanically invalid"
        )
        diagnostics.cycles.append(
            {
                "record_ids": list(component),
                "relations": relations,
                "severity": "warning",
            }
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
    warnings: list[str] = []
    diagnostics = _DiagnosticCollector()
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
            diagnostics=diagnostics,
        )
        if candidate is not None:
            parsed.append(candidate)

    by_artifact: dict[tuple[str, str], list[_ParsedManifest]] = {}
    for manifest in parsed:
        by_artifact.setdefault(
            (manifest.source.stage, manifest.source.artifact_id), []
        ).append(manifest)
    current: list[_ParsedManifest] = []
    for key in sorted(by_artifact):
        history = by_artifact[key]
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

    owners: dict[str, _RecordOwner] = {}
    for manifest in sorted(
        current,
        key=lambda item: (
            item.source.stage,
            item.source.artifact_id,
            item.source.revision,
        ),
    ):
        for index, record in enumerate(manifest.records):
            record_id = record.get("record_id")
            record_kind = record.get("record_kind")
            if not isinstance(record_id, str) or not ARTIFACT_ID_RE.fullmatch(record_id):
                continue
            label = f"{manifest.source.label}.records[{index}]"
            if record_kind not in policy.runtime.scientific_record_kinds:
                continue
            prior = owners.setdefault(
                record_id,
                (record_kind, label, record, manifest),
            )
            if prior[1] != label:
                errors.append(
                    f"{label}.record_id {record_id!r} duplicates project record "
                    f"owned by {prior[1]}"
                )
                diagnostics.duplicates.append(
                    {
                        "kind": "record",
                        "identifier": record_id,
                        "location": label,
                        "owner": prior[1],
                    }
                )

    relation_edges = _validate_relation_endpoints(
        current,
        owners=owners,
        policy=policy,
        errors=errors,
        diagnostics=diagnostics,
    )
    node_ids = tuple(sorted(owners))
    _diagnose_cycles(
        node_ids,
        relation_edges,
        errors=errors,
        warnings=warnings,
        diagnostics=diagnostics,
    )

    all_edges = relation_edges + _supersedes_edges(owners)
    connected = {
        endpoint
        for edge in all_edges
        for endpoint in (edge["source_id"], edge["target_id"])
    }
    orphans = tuple(record_id for record_id in node_ids if record_id not in connected)
    if orphans:
        preview = ", ".join(orphans[:10])
        suffix = f", and {len(orphans) - 10} more" if len(orphans) > 10 else ""
        warnings.append(
            f"{len(orphans)} structurally orphaned record(s) have no valid relation "
            f"or correction-lineage edge: {preview}{suffix}"
        )
    edges = tuple(
        sorted(
            all_edges,
            key=lambda edge: (
                edge["source_id"],
                edge["relation"],
                edge["target_id"],
            ),
        )
    )

    return RecordInspection(
        errors=tuple(errors),
        record_count=len(owners),
        warnings=tuple(warnings),
        nodes=_projection_nodes(owners),
        edges=edges,
        diagnostics={
            "dangling": _stable_dicts(diagnostics.dangling),
            "duplicates": _stable_dicts(diagnostics.duplicates),
            "invalid_supersedes": _stable_dicts(
                diagnostics.invalid_supersedes
            ),
            "cycles": _stable_dicts(diagnostics.cycles),
            "orphans": orphans,
        },
    )
