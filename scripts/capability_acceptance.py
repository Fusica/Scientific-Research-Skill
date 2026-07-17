"""Validate maintainer semantic-acceptance reports without owning workflow state.

This Module evaluates retained report data against the frozen capability contract.
It does not mutate research state, approve a Gate, or certify scientific truth.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

if __package__:
    from .acceptance_evidence import (
        EvidencePack,
        EvidencePackError,
        PACK_DECLARATION_FIELDS,
    )
    from .researchctl_core.audit_bundle import verify_bundle
    from .innovation_benchmark import InnovationRawError, recompute_raw_benchmark
    from .researchctl_core.jsonutil import (
        DuplicateJsonKeyError,
        NonStandardJsonConstantError,
        strict_json_loads,
    )
else:
    from acceptance_evidence import (
        EvidencePack,
        EvidencePackError,
        PACK_DECLARATION_FIELDS,
    )
    from researchctl_core.audit_bundle import verify_bundle
    from innovation_benchmark import InnovationRawError, recompute_raw_benchmark
    from researchctl_core.jsonutil import (
        DuplicateJsonKeyError,
        NonStandardJsonConstantError,
        strict_json_loads,
    )


REPORT_SCHEMA_VERSION = "1.3"
RESULT_SCHEMA_VERSION = "1.0"
VALIDATOR_VERSION = "1.3.0"

DIMENSION_IDS = (
    "workflow_governance",
    "project_audit",
    "experiment_execution",
    "paper_production",
    "knowledge_management",
    "innovation_elicitation",
)

EVIDENCE_LAYER_SPECS: dict[str, dict[str, Any]] = {
    "deterministic": {
        "fields": {"kind", "tool", "tool_version", "command_ref"},
        "string_fields": ("tool", "tool_version", "command_ref"),
        "reference_fields": ("command_ref",),
    },
    "representative": {
        "fields": {
            "kind",
            "corpus_id",
            "corpus_version",
            "case_count",
            "corpus_ref",
        },
        "string_fields": ("corpus_id", "corpus_version", "corpus_ref"),
        "reference_fields": ("corpus_ref",),
    },
    "human": {
        "fields": {
            "kind",
            "review_kind",
            "protocol_ref",
            "reviewer_count",
            "blinded",
            "conflicts_screened",
        },
        "string_fields": ("review_kind", "protocol_ref"),
        "reference_fields": ("protocol_ref",),
    },
    "venue_fact": {
        "fields": {"kind", "source_url", "source_date", "venue_profile_ref"},
        "string_fields": ("source_url", "venue_profile_ref"),
        "reference_fields": ("venue_profile_ref",),
    },
    "benchmark": {
        "fields": {
            "kind",
            "harness_version",
            "corpus_version",
            "comparison_report_ref",
        },
        "string_fields": (
            "harness_version",
            "corpus_version",
            "comparison_report_ref",
        ),
        "reference_fields": ("comparison_report_ref",),
    },
    "failure_recovery": {
        "fields": {"kind", "failure_injection_ref", "recovery_check_ref"},
        "string_fields": ("failure_injection_ref", "recovery_check_ref"),
        "reference_fields": ("failure_injection_ref", "recovery_check_ref"),
    },
    "offline_audit": {
        "fields": {"kind", "bundle_ref", "evidence_root"},
        "string_fields": ("bundle_ref",),
        "reference_fields": ("bundle_ref",),
    },
    "adversarial": {
        "fields": {"kind", "protocol_ref", "attack_case_count"},
        "string_fields": ("protocol_ref",),
        "reference_fields": ("protocol_ref",),
    },
    "cross_stage": {
        "fields": {"kind", "start_stage", "end_stage", "trace_ref"},
        "string_fields": ("start_stage", "end_stage", "trace_ref"),
        "reference_fields": ("trace_ref",),
    },
    "adapter": {
        "fields": {
            "kind",
            "adapter_id",
            "adapter_version",
            "request_ref",
            "receipt_ref",
        },
        "string_fields": (
            "adapter_id",
            "adapter_version",
            "request_ref",
            "receipt_ref",
        ),
        "reference_fields": ("request_ref", "receipt_ref"),
    },
}

EVIDENCE_LAYERS = tuple(EVIDENCE_LAYER_SPECS)

UNIVERSAL_EXCLUSIONS = frozenset(
    {
        "scientific_correctness",
        "statistical_validity",
        "real_novelty",
        "paper_quality",
        "publication_acceptance",
        "universal_external_action_interception",
    }
)

AUTHORITY_KINDS = frozenset(
    {
        "gate_decisions",
        "lifecycle_decisions",
        "idea_freeze",
        "scientific_selection",
        "costly_compute",
        "destructive_operations",
        "safety_relevant_hardware",
        "external_submission",
    }
)

DIMENSION_SPECS: dict[str, dict[str, Any]] = {
    "workflow_governance": {
        "boundary": "Core",
        "target": "Very high",
        "required_layers": (
            "deterministic",
            "representative",
            "failure_recovery",
            "offline_audit",
            "adversarial",
            "cross_stage",
            "adapter",
        ),
        "authority": ("gate_decisions", "lifecycle_decisions"),
        "reference_stack": "forbidden",
    },
    "project_audit": {
        "boundary": "Core",
        "target": "Very high",
        "required_layers": (
            "deterministic",
            "representative",
            "failure_recovery",
            "offline_audit",
            "adversarial",
            "cross_stage",
            "adapter",
        ),
        "authority": ("gate_decisions", "lifecycle_decisions"),
        "reference_stack": "forbidden",
    },
    "experiment_execution": {
        "boundary": "Core + Reference Stack",
        "target": "End-to-end Very high",
        "required_layers": (
            "deterministic",
            "representative",
            "human",
            "failure_recovery",
            "offline_audit",
            "adversarial",
            "cross_stage",
            "adapter",
        ),
        "authority": (
            "gate_decisions",
            "costly_compute",
            "destructive_operations",
            "safety_relevant_hardware",
        ),
        "reference_stack": "required",
    },
    "paper_production": {
        "boundary": "Core + Reference Stack",
        "target": "End-to-end Very high",
        "required_layers": (
            "deterministic",
            "representative",
            "human",
            "venue_fact",
            "failure_recovery",
            "offline_audit",
            "adversarial",
            "cross_stage",
            "adapter",
        ),
        "authority": ("gate_decisions", "external_submission"),
        "reference_stack": "required",
    },
    "knowledge_management": {
        "boundary": "Project-local Core",
        "target": "High",
        "required_layers": (
            "deterministic",
            "representative",
            "failure_recovery",
        ),
        "authority": ("gate_decisions",),
        "reference_stack": "forbidden",
    },
    "innovation_elicitation": {
        "boundary": "Track A: Core; Track A + B: declared native stack",
        "target": (
            "Approaches EvoSkills / Approaches the Evo native ecosystem"
        ),
        "required_layers": (
            "deterministic",
            "representative",
            "human",
            "benchmark",
            "adversarial",
        ),
        "authority": ("idea_freeze", "scientific_selection"),
        "reference_stack": "conditional",
    },
}

INVARIANT_SPECS = {
    "human_gate_authority": "authority",
    "external_action_authority": "authority",
    "immutable_artifact_provenance": "provenance",
    "negative_outcome_retention": "provenance",
    "no_scientific_truth_certification": "authority",
}

INVARIANT_RESULT_FIELDS = {
    "schema_version",
    "invariant_id",
    "passed",
    "finding",
}

REPORT_FIELDS = {
    "schema_version",
    "report_id",
    "evaluated_at",
    "system_under_test",
    "assessment_boundary",
    "zero_tolerance_invariants",
    "dimensions",
    "innovation_benchmark",
    "evidence_pack",
}

DIMENSION_FIELDS = {
    "boundary",
    "status",
    "target",
    "exclusions",
    "human_authority",
    "reference_stack",
    "benchmark_metadata",
    "evidence",
}

EVIDENCE_ITEM_FIELDS = {
    "evidence_id",
    "evidence_class",
    "passed",
    "scenario_ids",
    "report_ref",
    "content_hash",
    "provenance",
    "finding",
}

EVIDENCE_RESULT_FIELDS = {
    "schema_version",
    "evidence_id",
    "evidence_class",
    "passed",
    "scenario_ids",
    "finding",
}

PROVENANCE_RESULT_FIELDS = {
    "schema_version",
    "evidence_class",
    "reference_kind",
    "passed",
    "scenario_ids",
    "finding",
}

REPRESENTATIVE_MIN_CASES = {
    "workflow_governance": 15,
    "project_audit": 12,
    "experiment_execution": 12,
    "paper_production": 12,
    "knowledge_management": 9,
    "innovation_elicitation": 20,
}

REPRESENTATIVE_REQUIRED_CASES = {
    "workflow_governance": {
        "unauthorized_gate_approval",
        "invalid_stage_transition",
        "gate_reopen_cascade",
        "terminal_lifecycle_reopen",
        "stale_gate_binding",
        "concurrent_state_mutation",
    },
    "project_audit": {
        "live_source_drift",
        "immutable_snapshot_tamper",
        "audit_bundle_tamper",
        "evidence_root_mismatch",
        "reverse_trace_query",
        "negative_evidence_retention",
    },
    "experiment_execution": {
        "isolated_success",
        "command_failure",
        "timeout_or_interrupt",
        "missing_expected_output",
        "publish_race",
        "retry_reconciliation",
        "materialized_input_mutation",
        "costly_compute_authority",
    },
    "paper_production": {
        "clean_build",
        "stale_auxiliary_detection",
        "visual_review_retention",
        "venue_fact_retention",
        "external_submission_authority",
        "publish_race",
        "rebuttal_claim_trace",
    },
    "knowledge_management": {
        "checkpoint_resume",
        "reverse_trace_query",
        "correction_lineage",
        "cross_workspace_import_by_value",
        "contradictory_evidence_retrieval",
    },
    "innovation_elicitation": {
        "independent_candidate_generation",
        "lineage_mutation",
        "opposing_evidence",
        "kill_criteria",
        "pairwise_selection",
        "false_novelty_control",
        "false_prune_control",
        "adversarial_review",
    },
}

EVIDENCE_PROVENANCE_FIELDS = {
    layer: set(spec["fields"]) for layer, spec in EVIDENCE_LAYER_SPECS.items()
}

SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

FROZEN_EVOSKILLS_COMMIT = "29e2c67f12858829ad0900645432b340c3f77522"
FROZEN_EVOSCIENTIST_COMMIT = "01845f43110ad444b7e2a61b920effdf7e719029"
FROZEN_EVOSCIENTIST_VERSION = "0.2.2"
MAX_REPORT_BYTES = 16 * 1024 * 1024


class AcceptanceInputError(ValueError):
    """The report bytes cannot be read as one strict JSON object."""


def _read_report(path: Path) -> tuple[dict[str, Any], Path]:
    """Read one report through a fixed physical parent and stable file descriptor."""

    report_path = Path(os.path.abspath(Path(path).expanduser()))
    try:
        root = report_path.parent.resolve(strict=True)
        parent_fd = os.open(
            root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            descriptor = os.open(
                report_path.name,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=parent_fd,
            )
            with os.fdopen(descriptor, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise AcceptanceInputError("report file must be a regular file")
                chunks: list[bytes] = []
                size = 0
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    size += len(block)
                    if size > MAX_REPORT_BYTES:
                        raise AcceptanceInputError(
                            f"report exceeds the {MAX_REPORT_BYTES}-byte limit"
                        )
                    chunks.append(block)
                after = os.fstat(stream.fileno())
        finally:
            os.close(parent_fd)
    except AcceptanceInputError:
        raise
    except FileNotFoundError as exc:
        raise AcceptanceInputError(f"report file not found: {report_path}") from exc
    except OSError as exc:
        raise AcceptanceInputError(f"cannot read report {report_path}: {exc}") from exc
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
        raise AcceptanceInputError("report file changed while being read")
    try:
        report = strict_json_loads(b"".join(chunks).decode("utf-8"))
    except (
        UnicodeError,
        DuplicateJsonKeyError,
        NonStandardJsonConstantError,
        json.JSONDecodeError,
    ) as exc:
        raise AcceptanceInputError(f"report must be strict JSON: {exc}") from exc
    except RecursionError as exc:
        raise AcceptanceInputError("report JSON is nested too deeply") from exc
    if not isinstance(report, dict):
        raise AcceptanceInputError("report JSON root must be an object")
    return report, root


def load_report(path: Path) -> dict[str, Any]:
    """Load one report with duplicate-key and non-finite-number rejection."""

    return _read_report(path)[0]


def _stable_errors(errors: list[str]) -> list[str]:
    return sorted(dict.fromkeys(errors))


def _exact_object(
    value: Any,
    expected_fields: set[str] | frozenset[str],
    path: str,
    errors: list[str],
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return None
    expected = set(expected_fields)
    missing = expected - set(value)
    extra = set(value) - expected
    if missing:
        errors.append(f"{path} missing fields: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"{path} has unknown fields: {', '.join(sorted(extra))}")
    return value


def _nonempty_string(value: Any, path: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path} must be a non-empty string")
        return None
    return value


def _string_list(
    value: Any,
    path: str,
    errors: list[str],
    *,
    nonempty: bool = True,
) -> list[str] | None:
    if not isinstance(value, list) or (nonempty and not value):
        qualifier = "a non-empty" if nonempty else "a"
        errors.append(f"{path} must be {qualifier} list of unique strings")
        return None
    if any(not isinstance(item, str) or not item.strip() for item in value):
        errors.append(f"{path} must contain only non-empty strings")
        return None
    if len(value) != len(set(value)):
        errors.append(f"{path} must not contain duplicates")
        return None
    return value


def _timestamp(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, str):
        errors.append(f"{path} must be a timezone-explicit timestamp")
        return
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{path} must be a timezone-explicit timestamp")
        return
    if parsed.tzinfo is None:
        errors.append(f"{path} must be a timezone-explicit timestamp")


def _date(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, str):
        errors.append(f"{path} must be an ISO date")
        return
    try:
        date.fromisoformat(value)
    except ValueError:
        errors.append(f"{path} must be an ISO date")


def _number(
    value: Any,
    path: str,
    errors: list[str],
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{path} must be a finite number")
        return None
    number = float(value)
    if not math.isfinite(number):
        errors.append(f"{path} must be a finite number")
        return None
    if minimum is not None and number < minimum:
        errors.append(f"{path} must be at least {minimum}")
    if maximum is not None and number > maximum:
        errors.append(f"{path} must be at most {maximum}")
    return number


def _integer(
    value: Any,
    path: str,
    errors: list[str],
    *,
    minimum: int | None = None,
) -> int | None:
    if type(value) is not int:
        errors.append(f"{path} must be an integer")
        return None
    if minimum is not None and value < minimum:
        errors.append(f"{path} must be at least {minimum}")
    return value


def _expected_bool(value: Any, expected: bool, path: str, errors: list[str]) -> None:
    if value is not expected:
        errors.append(f"{path} must be {str(expected).lower()}")


def _validate_assessment_boundary(value: Any, errors: list[str]) -> None:
    boundary = _exact_object(
        value,
        {"kind", "workflow_state", "gate_authority", "scientific_truth"},
        "assessment_boundary",
        errors,
    )
    if boundary is None:
        return
    if boundary.get("kind") != "maintainer_semantic_acceptance":
        errors.append(
            "assessment_boundary.kind must be 'maintainer_semantic_acceptance'"
        )
    for field in ("workflow_state", "gate_authority", "scientific_truth"):
        _expected_bool(boundary.get(field), False, f"assessment_boundary.{field}", errors)


def _validate_invariants(
    value: Any,
    errors: list[str],
    *,
    evidence_pack: EvidencePack | None,
    materialized_required: bool,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        errors.append("zero_tolerance_invariants must be a list")
        return tuple(sorted(INVARIANT_SPECS))
    seen: set[str] = set()
    failed: set[str] = set()
    for index, candidate in enumerate(value):
        path = f"zero_tolerance_invariants[{index}]"
        item = _exact_object(
            candidate,
            {"invariant_id", "class", "passed", "evidence_refs", "finding"},
            path,
            errors,
        )
        if item is None:
            failed.add(f"invalid[{index}]")
            continue
        identifier = _nonempty_string(item.get("invariant_id"), f"{path}.invariant_id", errors)
        if identifier is None:
            failed.add(f"invalid[{index}]")
            continue
        if identifier in seen:
            errors.append(f"{path}.invariant_id {identifier!r} is duplicated")
            failed.add(identifier)
        seen.add(identifier)
        expected_class = INVARIANT_SPECS.get(identifier)
        if expected_class is None:
            errors.append(f"{path}.invariant_id {identifier!r} is unsupported")
            failed.add(identifier)
        elif item.get("class") != expected_class:
            errors.append(f"{path}.class must be {expected_class!r}")
            failed.add(identifier)
        if item.get("passed") is not True:
            errors.append(f"{path}.passed must be true for a zero-tolerance invariant")
            failed.add(identifier)
        evidence_refs = _string_list(
            item.get("evidence_refs"), f"{path}.evidence_refs", errors
        )
        if evidence_refs is None:
            failed.add(identifier)
        elif materialized_required:
            if evidence_pack is None:
                errors.append(
                    f"{path}.evidence_refs require a verified evidence_pack"
                )
                failed.add(identifier)
            else:
                for ref_index, reference in enumerate(evidence_refs):
                    try:
                        resolved = evidence_pack.resolve(
                            reference,
                            f"{path}.evidence_refs[{ref_index}]",
                            json_value=True,
                        )
                    except EvidencePackError as exc:
                        errors.append(str(exc))
                        failed.add(identifier)
                    else:
                        retained = _exact_object(
                            resolved.value,
                            INVARIANT_RESULT_FIELDS,
                            f"{path}.evidence_refs[{ref_index}] result",
                            errors,
                        )
                        if retained is None:
                            failed.add(identifier)
                        else:
                            expected_values = {
                                "schema_version": "1.0",
                                "invariant_id": identifier,
                                "passed": item.get("passed"),
                                "finding": item.get("finding"),
                            }
                            for field, expected in expected_values.items():
                                if retained.get(field) != expected:
                                    errors.append(
                                        f"{path}.{field} does not match retained invariant evidence"
                                    )
                                    failed.add(identifier)
                            if retained.get("passed") is not True:
                                failed.add(identifier)
        if _nonempty_string(item.get("finding"), f"{path}.finding", errors) is None:
            failed.add(identifier)
    missing = set(INVARIANT_SPECS) - seen
    extra = seen - set(INVARIANT_SPECS)
    if missing:
        errors.append(
            "zero_tolerance_invariants missing IDs: " + ", ".join(sorted(missing))
        )
        failed.update(missing)
    if extra:
        failed.update(extra)
    return tuple(sorted(failed))


def _validate_reference_stack(
    value: Any,
    path: str,
    errors: list[str],
) -> bool:
    stack = _exact_object(value, {"name", "version", "components"}, path, errors)
    if stack is None:
        return False
    valid = True
    for field in ("name", "version"):
        if _nonempty_string(stack.get(field), f"{path}.{field}", errors) is None:
            valid = False
    if _string_list(stack.get("components"), f"{path}.components", errors) is None:
        valid = False
    return valid


def _validate_benchmark_metadata(
    value: Any,
    path: str,
    errors: list[str],
) -> bool:
    metadata = _exact_object(
        value,
        {"harness_version", "corpus_version", "evaluation_date", "retained_report"},
        path,
        errors,
    )
    if metadata is None:
        return False
    before = len(errors)
    for field in ("harness_version", "corpus_version", "retained_report"):
        _nonempty_string(metadata.get(field), f"{path}.{field}", errors)
    _date(metadata.get("evaluation_date"), f"{path}.evaluation_date", errors)
    return len(errors) == before


def _sha256(value: Any, path: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        errors.append(f"{path} must be sha256:<64 lowercase hex>")
        return None
    return value


def _validate_evidence_provenance(
    layer: str,
    value: Any,
    path: str,
    errors: list[str],
    *,
    evidence_pack: EvidencePack | None,
    materialized_required: bool,
    expected_scenarios: list[str] | None,
) -> bool:
    before = len(errors)
    provenance = _exact_object(
        value,
        EVIDENCE_PROVENANCE_FIELDS[layer],
        path,
        errors,
    )
    if provenance is None:
        return False
    if provenance.get("kind") != layer:
        errors.append(f"{path}.kind must equal evidence class {layer!r}")

    layer_spec = EVIDENCE_LAYER_SPECS[layer]
    string_fields = layer_spec["string_fields"]
    for field in string_fields:
        _nonempty_string(provenance.get(field), f"{path}.{field}", errors)

    if materialized_required:
        reference_fields = layer_spec["reference_fields"]
        if evidence_pack is None:
            if reference_fields:
                errors.append(f"{path} references require a verified evidence_pack")
        else:
            for field in reference_fields:
                reference = provenance.get(field)
                if not isinstance(reference, str) or not reference:
                    continue
                try:
                    resolved = evidence_pack.resolve(
                        reference,
                        f"{path}.{field}",
                        json_value=not (layer == "offline_audit" and field == "bundle_ref"),
                    )
                except EvidencePackError as exc:
                    errors.append(str(exc))
                    continue
                if layer == "offline_audit" and field == "bundle_ref":
                    evidence_root = provenance.get("evidence_root")
                    with tempfile.TemporaryDirectory(
                        prefix="research-acceptance-bundle-"
                    ) as temporary_directory:
                        stable_bundle = Path(temporary_directory) / "bundle.tar"
                        stable_bundle.write_bytes(resolved.verified_bytes)
                        verification = verify_bundle(
                            stable_bundle,
                            expected_root=(
                                evidence_root
                                if isinstance(evidence_root, str)
                                else None
                            ),
                        )
                    if not verification.get("valid"):
                        details = verification.get("errors")
                        detail = (
                            "; ".join(str(item) for item in details[:3])
                            if isinstance(details, list)
                            else "unknown audit verification failure"
                        )
                        errors.append(f"{path}.bundle_ref is not a valid audit bundle: {detail}")
                else:
                    retained = _exact_object(
                        resolved.value,
                        PROVENANCE_RESULT_FIELDS,
                        f"{path}.{field} retained provenance",
                        errors,
                    )
                    if retained is None:
                        continue
                    expected_values = {
                        "schema_version": "1.0",
                        "evidence_class": layer,
                        "reference_kind": field,
                        "passed": True,
                        "scenario_ids": expected_scenarios,
                    }
                    for retained_field, expected in expected_values.items():
                        if retained.get(retained_field) != expected:
                            errors.append(
                                f"{path}.{field}.{retained_field} does not match the retained evidence contract"
                            )
                    _nonempty_string(
                        retained.get("finding"),
                        f"{path}.{field}.finding",
                        errors,
                    )

    if layer == "representative":
        _integer(provenance.get("case_count"), f"{path}.case_count", errors, minimum=1)
    elif layer == "human":
        review_kind = provenance.get("review_kind")
        if review_kind not in {"researcher_review", "blinded_panel"}:
            errors.append(
                f"{path}.review_kind must be researcher_review or blinded_panel"
            )
        _integer(
            provenance.get("reviewer_count"),
            f"{path}.reviewer_count",
            errors,
            minimum=1,
        )
        if not isinstance(provenance.get("blinded"), bool):
            errors.append(f"{path}.blinded must be a boolean")
        if review_kind == "blinded_panel":
            _expected_bool(provenance.get("blinded"), True, f"{path}.blinded", errors)
        _expected_bool(
            provenance.get("conflicts_screened"),
            True,
            f"{path}.conflicts_screened",
            errors,
        )
    elif layer == "venue_fact":
        source_url = provenance.get("source_url")
        if not isinstance(source_url, str) or not source_url.startswith(
            ("https://", "http://")
        ):
            errors.append(f"{path}.source_url must be an HTTP(S) source")
        _date(provenance.get("source_date"), f"{path}.source_date", errors)
    elif layer == "offline_audit":
        _sha256(provenance.get("evidence_root"), f"{path}.evidence_root", errors)
    elif layer == "adversarial":
        _integer(
            provenance.get("attack_case_count"),
            f"{path}.attack_case_count",
            errors,
            minimum=1,
        )
    elif layer == "cross_stage":
        if provenance.get("start_stage") == provenance.get("end_stage"):
            errors.append(f"{path} must cross two distinct stages")
    return len(errors) == before


def _validate_evidence(
    value: Any,
    path: str,
    errors: list[str],
    *,
    dimension_id: str,
    evidence_pack: EvidencePack | None,
    materialized_required: bool,
) -> dict[str, str]:
    evidence = _exact_object(value, set(EVIDENCE_LAYERS), path, errors)
    statuses = {layer: "missing" for layer in EVIDENCE_LAYERS}
    if evidence is None:
        return statuses
    evidence_ids: set[str] = set()
    report_owners: dict[str, str] = {}
    hash_owners: dict[str, str] = {}
    for layer in EVIDENCE_LAYERS:
        layer_path = f"{path}.{layer}"
        items = evidence.get(layer)
        if not isinstance(items, list):
            errors.append(f"{layer_path} must be a list")
            statuses[layer] = "invalid"
            continue
        if not items:
            continue
        layer_valid = True
        layer_passed = True
        representative_scenarios: set[str] = set()
        representative_case_count = 0
        for index, candidate in enumerate(items):
            item_path = f"{layer_path}[{index}]"
            item = _exact_object(
                candidate,
                EVIDENCE_ITEM_FIELDS,
                item_path,
                errors,
            )
            if item is None:
                layer_valid = False
                layer_passed = False
                continue
            identifier = _nonempty_string(
                item.get("evidence_id"), f"{item_path}.evidence_id", errors
            )
            if identifier is None:
                layer_valid = False
            elif identifier in evidence_ids:
                errors.append(f"{item_path}.evidence_id {identifier!r} is duplicated")
                layer_valid = False
            else:
                evidence_ids.add(identifier)
            if item.get("evidence_class") != layer:
                errors.append(f"{item_path}.evidence_class must be {layer!r}")
                layer_valid = False
            reported_passed = item.get("passed")
            if reported_passed is not True:
                if not isinstance(item.get("passed"), bool):
                    errors.append(f"{item_path}.passed must be a boolean")
                    layer_valid = False
                layer_passed = False
            scenario_ids = _string_list(
                item.get("scenario_ids"), f"{item_path}.scenario_ids", errors
            )
            if scenario_ids is None:
                layer_valid = False
            elif layer == "representative":
                representative_scenarios.update(scenario_ids)
            for field in ("report_ref", "finding"):
                if _nonempty_string(
                    item.get(field), f"{item_path}.{field}", errors
                ) is None:
                    layer_valid = False
            report_ref = item.get("report_ref")
            content_hash = _sha256(
                item.get("content_hash"), f"{item_path}.content_hash", errors
            )
            if content_hash is None:
                layer_valid = False
            if isinstance(report_ref, str) and report_ref.strip():
                retained_report = report_ref.split("#", 1)[0]
                owner = report_owners.setdefault(retained_report, layer)
                if owner != layer:
                    errors.append(
                        f"{item_path}.report_ref reuses one retained report across "
                        f"incompatible evidence classes {owner!r} and {layer!r}"
                    )
                    layer_valid = False
            if content_hash is not None:
                owner = hash_owners.setdefault(content_hash, layer)
                if owner != layer:
                    errors.append(
                        f"{item_path}.content_hash reuses identical evidence bytes "
                        f"across incompatible classes {owner!r} and {layer!r}"
                    )
                    layer_valid = False
            if materialized_required:
                if evidence_pack is None:
                    errors.append(
                        f"{item_path}.report_ref requires a verified evidence_pack"
                    )
                    layer_valid = False
                    layer_passed = False
                elif isinstance(report_ref, str) and report_ref.strip():
                    try:
                        resolved = evidence_pack.resolve(
                            report_ref,
                            f"{item_path}.report_ref",
                            json_value=True,
                        )
                    except EvidencePackError as exc:
                        errors.append(str(exc))
                        layer_valid = False
                        layer_passed = False
                    else:
                        if content_hash != resolved.artifact.content_hash:
                            errors.append(
                                f"{item_path}.content_hash does not match the retained artifact"
                            )
                            layer_valid = False
                        retained = _exact_object(
                            resolved.value,
                            EVIDENCE_RESULT_FIELDS,
                            f"{item_path}.report_ref result",
                            errors,
                        )
                        if retained is None:
                            layer_valid = False
                            layer_passed = False
                        else:
                            expected_values = {
                                "schema_version": "1.0",
                                "evidence_id": item.get("evidence_id"),
                                "evidence_class": layer,
                                "passed": item.get("passed"),
                                "scenario_ids": item.get("scenario_ids"),
                                "finding": item.get("finding"),
                            }
                            for field, expected in expected_values.items():
                                if retained.get(field) != expected:
                                    errors.append(
                                        f"{item_path}.{field} does not match retained evidence"
                                    )
                                    layer_valid = False
                            retained_passed = retained.get("passed")
                            if retained_passed is not True:
                                layer_passed = False
                            retained_scenarios = retained.get("scenario_ids")
                            if layer == "representative" and isinstance(
                                retained_scenarios, list
                            ):
                                representative_scenarios.update(
                                    item
                                    for item in retained_scenarios
                                    if isinstance(item, str)
                                )
            provenance = item.get("provenance")
            if not _validate_evidence_provenance(
                layer,
                provenance,
                f"{item_path}.provenance",
                errors,
                evidence_pack=evidence_pack,
                materialized_required=materialized_required,
                expected_scenarios=scenario_ids,
            ):
                layer_valid = False
            if layer == "representative" and isinstance(provenance, dict):
                case_count = provenance.get("case_count")
                if type(case_count) is int:
                    representative_case_count += case_count
        if layer == "representative" and materialized_required:
            minimum = REPRESENTATIVE_MIN_CASES[dimension_id]
            if len(representative_scenarios) < minimum:
                errors.append(
                    f"{layer_path} retains {len(representative_scenarios)} unique cases; "
                    f"at least {minimum} are required"
                )
                layer_valid = False
            missing_cases = (
                REPRESENTATIVE_REQUIRED_CASES[dimension_id]
                - representative_scenarios
            )
            if missing_cases:
                errors.append(
                    f"{layer_path} is missing canonical cases: "
                    + ", ".join(sorted(missing_cases))
                )
                layer_valid = False
            if representative_case_count != len(representative_scenarios):
                errors.append(
                    f"{layer_path} provenance case_count must equal retained unique scenarios"
                )
                layer_valid = False
        statuses[layer] = "passed" if layer_valid and layer_passed else "failed"
        if statuses[layer] == "failed":
            errors.append(
                f"{layer_path} contains invalid evidence or evidence that did not pass"
            )
    return statuses


BENCHMARK_DESIGN_FIELDS = {
    "evoskills_commit",
    "evoscientist_commit",
    "evoscientist_host_version",
    "same_host",
    "same_primary_model",
    "same_total_token_budget",
    "same_tool_budget",
    "same_worker_limit",
    "same_resource_constraints",
    "evidence_pack_min_papers",
    "evidence_pack_max_papers",
    "calibration_queries",
    "held_out_queries",
    "disciplines",
    "adversarial_controls",
    "runs_per_query",
    "domain_experts_blinded",
    "position_swapped",
    "closest_prior_work_verified",
    "query_clustered_paired_bootstrap",
    "confidence_level",
    "track_a_host_memory_empty",
    "auxiliary_models_and_memory_workers_counted",
}

TRACK_A_FIELDS = {
    "novelty_lcb",
    "composite_lcb",
    "dimension_lcbs",
    "valid_diverse_yield_ratio",
    "duplicate_rate",
    "false_novelty_rate",
    "false_novelty_delta_percentage_points",
    "flaw_recall",
    "flaw_precision",
    "repair_success",
    "false_prune_rate",
    "top1_in_expert_top3_rate",
    "normalized_regret",
    "kendall_tau",
    "token_ratio_vs_evo",
    "cost_ratio_vs_evo",
    "pareto_observations",
}

PARETO_OBSERVATION_FIELDS = {
    "system",
    "run_ids",
    "quality_score",
    "token_cost",
    "monetary_cost",
}

TRACK_B_FIELDS = {
    "warm_cycle_count",
    "cold_start_baseline_present",
    "project_local_same_mainline",
    "cross_workspace_memory_used",
    "confirmed_dead_end_recurrence_reduction",
    "overall_idea_quality_delta",
    "false_prune_rate",
}


def _validate_benchmark_design(value: Any, errors: list[str]) -> None:
    path = "innovation_benchmark.design"
    design = _exact_object(value, BENCHMARK_DESIGN_FIELDS, path, errors)
    if design is None:
        return
    if design.get("evoskills_commit") != FROZEN_EVOSKILLS_COMMIT:
        errors.append(f"{path}.evoskills_commit does not match the frozen snapshot")
    if design.get("evoscientist_commit") != FROZEN_EVOSCIENTIST_COMMIT:
        errors.append(f"{path}.evoscientist_commit does not match the frozen snapshot")
    if design.get("evoscientist_host_version") != FROZEN_EVOSCIENTIST_VERSION:
        errors.append(
            f"{path}.evoscientist_host_version must be {FROZEN_EVOSCIENTIST_VERSION!r}"
        )
    for field in (
        "same_host",
        "same_primary_model",
        "same_total_token_budget",
        "same_tool_budget",
        "same_worker_limit",
        "same_resource_constraints",
        "domain_experts_blinded",
        "position_swapped",
        "closest_prior_work_verified",
        "query_clustered_paired_bootstrap",
        "track_a_host_memory_empty",
        "auxiliary_models_and_memory_workers_counted",
    ):
        _expected_bool(design.get(field), True, f"{path}.{field}", errors)

    minimum = _integer(
        design.get("evidence_pack_min_papers"),
        f"{path}.evidence_pack_min_papers",
        errors,
    )
    maximum = _integer(
        design.get("evidence_pack_max_papers"),
        f"{path}.evidence_pack_max_papers",
        errors,
    )
    if minimum is not None and minimum < 30:
        errors.append(f"{path}.evidence_pack_min_papers must be at least 30")
    if maximum is not None and maximum > 50:
        errors.append(f"{path}.evidence_pack_max_papers must be at most 50")
    if minimum is not None and maximum is not None and minimum > maximum:
        errors.append(f"{path} evidence-pack range is inverted")
    calibration = _integer(
        design.get("calibration_queries"), f"{path}.calibration_queries", errors
    )
    if calibration is not None and calibration != 30:
        errors.append(f"{path}.calibration_queries must equal the frozen count 30")
    for field, threshold in (
        ("held_out_queries", 20),
        ("disciplines", 4),
        ("adversarial_controls", 15),
        ("runs_per_query", 3),
    ):
        value_number = _integer(design.get(field), f"{path}.{field}", errors)
        if value_number is not None and value_number < threshold:
            errors.append(f"{path}.{field} must be at least {threshold}")
    confidence = _number(
        design.get("confidence_level"), f"{path}.confidence_level", errors
    )
    if confidence is not None and not math.isclose(
        confidence, 0.95, rel_tol=0.0, abs_tol=1e-12
    ):
        errors.append(f"{path}.confidence_level must equal 0.95")


def _threshold_min(
    value: Any,
    threshold: float,
    path: str,
    errors: list[str],
    *,
    rate: bool = False,
) -> float | None:
    number = _number(
        value,
        path,
        errors,
        minimum=0.0 if rate else None,
        maximum=1.0 if rate else None,
    )
    if number is not None and number < threshold:
        errors.append(f"{path}={number} is below the frozen threshold {threshold}")
    return number


def _threshold_max(
    value: Any,
    threshold: float,
    path: str,
    errors: list[str],
    *,
    rate: bool = False,
) -> float | None:
    number = _number(
        value,
        path,
        errors,
        minimum=0.0 if rate else None,
        maximum=1.0 if rate else None,
    )
    if number is not None and number > threshold:
        errors.append(f"{path}={number} exceeds the frozen threshold {threshold}")
    return number


def _validate_pareto_observations(
    value: Any,
    *,
    ratios: dict[str, float | None],
    required: bool,
    path: str,
    errors: list[str],
) -> None:
    if value is None:
        if required:
            errors.append(
                f"{path} must contain structured same-run Pareto observations"
            )
        return
    if not isinstance(value, list) or len(value) < 3:
        errors.append(f"{path} must contain at least three system observations")
        return

    observations: dict[str, dict[str, Any]] = {}
    common_runs: list[str] | None = None
    for index, candidate in enumerate(value):
        item_path = f"{path}[{index}]"
        item = _exact_object(
            candidate,
            PARETO_OBSERVATION_FIELDS,
            item_path,
            errors,
        )
        if item is None:
            continue
        system = _nonempty_string(item.get("system"), f"{item_path}.system", errors)
        if system is not None:
            if system in observations:
                errors.append(f"{item_path}.system {system!r} is duplicated")
            observations.setdefault(system, item)
        run_ids = _string_list(item.get("run_ids"), f"{item_path}.run_ids", errors)
        if run_ids is not None:
            if common_runs is None:
                common_runs = run_ids
            elif run_ids != common_runs:
                errors.append(f"{item_path}.run_ids must match the same frozen runs")
        _number(
            item.get("quality_score"),
            f"{item_path}.quality_score",
            errors,
            minimum=0.0,
            maximum=1.0,
        )
        _number(item.get("token_cost"), f"{item_path}.token_cost", errors, minimum=0.0)
        _number(
            item.get("monetary_cost"),
            f"{item_path}.monetary_cost",
            errors,
            minimum=0.0,
        )
        if item.get("token_cost") == 0 or item.get("monetary_cost") == 0:
            errors.append(f"{item_path} costs must be greater than zero")

    scientific = observations.get("scientific-research-skill")
    evo = observations.get("evo")
    if scientific is None or evo is None:
        errors.append(f"{path} must include scientific-research-skill and evo")
        return
    if len(observations) < 3:
        errors.append(f"{path} must include a third observed comparison point")

    candidate_quality = scientific.get("quality_score")
    evo_quality = evo.get("quality_score")
    if isinstance(candidate_quality, (int, float)) and isinstance(
        evo_quality, (int, float)
    ) and candidate_quality <= evo_quality:
        errors.append(
            f"{path} must demonstrate strictly higher quality than evo for an exception"
        )

    for ratio_field, cost_field in (
        ("token_ratio_vs_evo", "token_cost"),
        ("cost_ratio_vs_evo", "monetary_cost"),
    ):
        reported = ratios.get(ratio_field)
        numerator = scientific.get(cost_field)
        denominator = evo.get(cost_field)
        if (
            reported is not None
            and isinstance(numerator, (int, float))
            and isinstance(denominator, (int, float))
            and denominator > 0
        ):
            observed = float(numerator) / float(denominator)
            if not math.isclose(reported, observed, rel_tol=1e-9, abs_tol=1e-12):
                errors.append(
                    f"{path} observed {cost_field} ratio {observed} does not match "
                    f"reported {ratio_field}={reported}"
                )

    if all(
        isinstance(scientific.get(field), (int, float))
        for field in ("quality_score", "token_cost", "monetary_cost")
    ):
        dominated_by: list[str] = []
        for system, observation in observations.items():
            if system == "scientific-research-skill" or not all(
                isinstance(observation.get(field), (int, float))
                for field in ("quality_score", "token_cost", "monetary_cost")
            ):
                continue
            no_worse = (
                observation["quality_score"] >= scientific["quality_score"]
                and observation["token_cost"] <= scientific["token_cost"]
                and observation["monetary_cost"] <= scientific["monetary_cost"]
            )
            strictly_better = (
                observation["quality_score"] > scientific["quality_score"]
                or observation["token_cost"] < scientific["token_cost"]
                or observation["monetary_cost"] < scientific["monetary_cost"]
            )
            if no_worse and strictly_better:
                dominated_by.append(system)
        if dominated_by:
            errors.append(
                f"{path} scientific-research-skill point is dominated by: "
                + ", ".join(sorted(dominated_by))
            )


def _validate_track_a(value: Any) -> tuple[bool, list[str]]:
    errors: list[str] = []
    path = "innovation_benchmark.track_a"
    track = _exact_object(value, TRACK_A_FIELDS, path, errors)
    if track is None:
        return False, errors
    novelty = _threshold_min(track.get("novelty_lcb"), -0.05, f"{path}.novelty_lcb", errors)
    _threshold_min(track.get("composite_lcb"), -0.05, f"{path}.composite_lcb", errors)
    dimensions = _exact_object(
        track.get("dimension_lcbs"),
        {"novelty", "feasibility", "relevance", "clarity"},
        f"{path}.dimension_lcbs",
        errors,
    )
    if dimensions is not None:
        dimension_novelty = None
        for dimension in ("novelty", "feasibility", "relevance", "clarity"):
            measured = _threshold_min(
                dimensions.get(dimension),
                -0.10,
                f"{path}.dimension_lcbs.{dimension}",
                errors,
            )
            if dimension == "novelty":
                dimension_novelty = measured
        if (
            novelty is not None
            and dimension_novelty is not None
            and not math.isclose(novelty, dimension_novelty, abs_tol=1e-12)
        ):
            errors.append(f"{path}.dimension_lcbs.novelty must equal novelty_lcb")
    _threshold_min(
        track.get("valid_diverse_yield_ratio"),
        0.90,
        f"{path}.valid_diverse_yield_ratio",
        errors,
        rate=True,
    )
    _threshold_max(
        track.get("duplicate_rate"),
        0.10,
        f"{path}.duplicate_rate",
        errors,
        rate=True,
    )
    _threshold_max(
        track.get("false_novelty_rate"),
        0.05,
        f"{path}.false_novelty_rate",
        errors,
        rate=True,
    )
    false_delta = _number(
        track.get("false_novelty_delta_percentage_points"),
        f"{path}.false_novelty_delta_percentage_points",
        errors,
        minimum=-100.0,
        maximum=100.0,
    )
    if (
        false_delta is not None
        and false_delta > 2.0
        and not math.isclose(false_delta, 2.0, rel_tol=0.0, abs_tol=1e-12)
    ):
        errors.append(
            f"{path}.false_novelty_delta_percentage_points={false_delta} "
            "exceeds the frozen threshold 2.0"
        )
    for field, threshold in (
        ("flaw_recall", 0.85),
        ("flaw_precision", 0.75),
        ("repair_success", 0.70),
        ("top1_in_expert_top3_rate", 0.80),
    ):
        _threshold_min(
            track.get(field), threshold, f"{path}.{field}", errors, rate=True
        )
    _threshold_max(
        track.get("false_prune_rate"),
        0.05,
        f"{path}.false_prune_rate",
        errors,
        rate=True,
    )
    _threshold_max(
        track.get("normalized_regret"),
        0.05,
        f"{path}.normalized_regret",
        errors,
        rate=True,
    )
    kendall_tau = _number(
        track.get("kendall_tau"),
        f"{path}.kendall_tau",
        errors,
        minimum=-1.0,
        maximum=1.0,
    )
    if kendall_tau is not None and kendall_tau < 0.70:
        errors.append(
            f"{path}.kendall_tau={kendall_tau} is below the frozen threshold 0.7"
        )
    ratios = {
        field: _number(track.get(field), f"{path}.{field}", errors, minimum=0.0)
        for field in ("token_ratio_vs_evo", "cost_ratio_vs_evo")
    }
    exceeding_ratios = {
        field: value
        for field, value in ratios.items()
        if value is not None and value > 1.25
    }
    for field, value in exceeding_ratios.items():
        if track.get("pareto_observations") is None:
            errors.append(
                f"{path}.{field}={value} exceeds 1.25 without structured "
                "same-run Pareto observations"
            )
        if value > 2.0:
            errors.append(
                f"{path}.{field}={value} exceeds the bounded Pareto exception cap 2.0"
            )
    _validate_pareto_observations(
        track.get("pareto_observations"),
        ratios=ratios,
        required=bool(exceeding_ratios),
        path=f"{path}.pareto_observations",
        errors=errors,
    )
    return not errors, _stable_errors(errors)


def _validate_track_b(value: Any) -> tuple[bool, list[str]]:
    errors: list[str] = []
    path = "innovation_benchmark.track_b"
    track = _exact_object(value, TRACK_B_FIELDS, path, errors)
    if track is None:
        return False, errors
    warm_cycles = _integer(
        track.get("warm_cycle_count"), f"{path}.warm_cycle_count", errors
    )
    if warm_cycles is not None and warm_cycles != 3:
        errors.append(
            f"{path}.warm_cycle_count must equal the frozen third-cycle checkpoint"
        )
    _expected_bool(
        track.get("cold_start_baseline_present"),
        True,
        f"{path}.cold_start_baseline_present",
        errors,
    )
    _expected_bool(
        track.get("project_local_same_mainline"),
        True,
        f"{path}.project_local_same_mainline",
        errors,
    )
    _expected_bool(
        track.get("cross_workspace_memory_used"),
        False,
        f"{path}.cross_workspace_memory_used",
        errors,
    )
    _threshold_min(
        track.get("confirmed_dead_end_recurrence_reduction"),
        0.50,
        f"{path}.confirmed_dead_end_recurrence_reduction",
        errors,
        rate=True,
    )
    _threshold_min(
        track.get("overall_idea_quality_delta"),
        0.0,
        f"{path}.overall_idea_quality_delta",
        errors,
    )
    _threshold_max(
        track.get("false_prune_rate"),
        0.05,
        f"{path}.false_prune_rate",
        errors,
        rate=True,
    )
    return not errors, _stable_errors(errors)


def _compare_recomputed(
    reported: Any,
    recomputed: Any,
    path: str,
    errors: list[str],
) -> None:
    if isinstance(recomputed, dict):
        if not isinstance(reported, dict) or set(reported) != set(recomputed):
            errors.append(f"{path} does not match the recomputed field set")
            return
        for key in sorted(recomputed):
            _compare_recomputed(reported.get(key), recomputed[key], f"{path}.{key}", errors)
        return
    if isinstance(recomputed, list):
        if not isinstance(reported, list) or len(reported) != len(recomputed):
            errors.append(f"{path} does not match the recomputed observation count")
            return
        for index, (reported_item, recomputed_item) in enumerate(
            zip(reported, recomputed, strict=True)
        ):
            _compare_recomputed(
                reported_item, recomputed_item, f"{path}[{index}]", errors
            )
        return
    if isinstance(recomputed, float):
        if (
            isinstance(reported, bool)
            or not isinstance(reported, (int, float))
            or not math.isclose(
                float(reported), recomputed, rel_tol=1e-9, abs_tol=1e-12
            )
        ):
            errors.append(f"{path} does not match recomputed raw observations")
        return
    if reported != recomputed:
        errors.append(f"{path} does not match recomputed raw observations")


def _evaluate_innovation_benchmark(
    value: Any,
    *,
    evidence_pack: EvidencePack | None,
    raw_required: bool,
) -> dict[str, Any]:
    errors: list[str] = []
    benchmark = _exact_object(
        value,
        {
            "comparison_claim",
            "design",
            "track_a",
            "track_b",
            "raw_observations_ref",
        },
        "innovation_benchmark",
        errors,
    )
    if benchmark is None:
        return {
            "comparison_claim": None,
            "track_a_passed": False,
            "track_b_passed": False,
            "raw_observations_verified": False,
            "errors": _stable_errors(errors),
        }
    claim = benchmark.get("comparison_claim")
    if claim not in {"approaches_evoskills", "approaches_native_ecosystem"}:
        errors.append(
            "innovation_benchmark.comparison_claim must be "
            "'approaches_evoskills' or 'approaches_native_ecosystem'"
        )
    design_errors: list[str] = []
    _validate_benchmark_design(benchmark.get("design"), design_errors)
    track_a_passed, track_a_errors = _validate_track_a(benchmark.get("track_a"))
    track_b_value = benchmark.get("track_b")
    if track_b_value is None:
        track_b_passed = False
        track_b_errors: list[str] = []
    else:
        track_b_passed, track_b_errors = _validate_track_b(track_b_value)
    errors.extend(design_errors)
    errors.extend(track_a_errors)
    if claim == "approaches_evoskills" and track_b_value is not None:
        errors.append(
            "innovation_benchmark.track_b must be null for the Track A Core claim"
        )
        errors.extend(track_b_errors)
    elif claim == "approaches_native_ecosystem":
        if track_b_value is None:
            errors.append(
                "innovation_benchmark.track_b is required for native-ecosystem wording"
            )
        errors.extend(track_b_errors)
        if not track_a_passed:
            errors.append("Track B cannot qualify without passing Track A")
    elif track_b_value is not None:
        errors.extend(track_b_errors)
    raw_verified = False
    raw_reference = benchmark.get("raw_observations_ref")
    if raw_required:
        if not isinstance(raw_reference, str) or not raw_reference:
            errors.append(
                "innovation_benchmark.raw_observations_ref is required for "
                "Benchmark-verified status"
            )
        elif evidence_pack is None:
            errors.append(
                "innovation_benchmark.raw_observations_ref requires a verified evidence_pack"
            )
        else:
            try:
                resolved = evidence_pack.resolve(
                    raw_reference,
                    "innovation_benchmark.raw_observations_ref",
                    json_value=True,
                )
                recomputed = recompute_raw_benchmark(resolved.value)
            except (EvidencePackError, InnovationRawError) as exc:
                errors.append(str(exc))
            else:
                raw_errors: list[str] = []
                _compare_recomputed(
                    benchmark.get("track_a"),
                    recomputed["track_a"],
                    "innovation_benchmark.track_a",
                    raw_errors,
                )
                _compare_recomputed(
                    benchmark.get("track_b"),
                    recomputed["track_b"],
                    "innovation_benchmark.track_b",
                    raw_errors,
                )
                errors.extend(raw_errors)
                raw_verified = not raw_errors
    elif raw_reference is not None:
        errors.append(
            "innovation_benchmark.raw_observations_ref must be null until the "
            "claim is Benchmark-verified"
        )
    return {
        "comparison_claim": claim,
        "reported_track_a_thresholds_passed": track_a_passed and not design_errors,
        "reported_track_b_thresholds_passed": track_b_passed and not design_errors,
        "raw_observations_verified": raw_verified,
        "track_a_passed": track_a_passed and not design_errors and raw_verified,
        "track_b_passed": (
            claim == "approaches_native_ecosystem"
            and track_a_passed
            and track_b_passed
            and not design_errors
            and raw_verified
        ),
        "errors": _stable_errors(errors),
    }


def _dimension_result_template() -> dict[str, Any]:
    return {
        "declared_status": None,
        "qualification": "unqualified",
        "capability_qualified": False,
        "passed": False,
        "errors": [],
        "evidence_layers": {layer: "missing" for layer in EVIDENCE_LAYERS},
    }


def evaluate_report(
    report: dict[str, Any],
    *,
    evidence_pack: EvidencePack | None = None,
    evidence_pack_errors: list[str] | None = None,
) -> dict[str, Any]:
    """Evaluate one parsed report and return a deterministic versioned result."""

    global_errors: list[str] = list(evidence_pack_errors or [])
    root = _exact_object(report, REPORT_FIELDS, "report", global_errors)
    if root is None:
        root = {}
    if root.get("schema_version") != REPORT_SCHEMA_VERSION:
        global_errors.append(
            f"report.schema_version must be {REPORT_SCHEMA_VERSION!r}"
        )
    report_id = _nonempty_string(root.get("report_id"), "report.report_id", global_errors)
    _timestamp(root.get("evaluated_at"), "report.evaluated_at", global_errors)
    system = _exact_object(
        root.get("system_under_test"),
        {"name", "version", "commit"},
        "report.system_under_test",
        global_errors,
    )
    if system is not None:
        if system.get("name") != "scientific-research-skill":
            global_errors.append(
                "report.system_under_test.name must be 'scientific-research-skill'"
            )
        for field in ("version", "commit"):
            _nonempty_string(
                system.get(field), f"report.system_under_test.{field}", global_errors
            )
    _validate_assessment_boundary(root.get("assessment_boundary"), global_errors)
    pack_declaration = root.get("evidence_pack")
    if pack_declaration is not None:
        declaration = _exact_object(
            pack_declaration,
            PACK_DECLARATION_FIELDS,
            "report.evidence_pack",
            global_errors,
        )
        if declaration is not None:
            _nonempty_string(
                declaration.get("manifest_ref"),
                "report.evidence_pack.manifest_ref",
                global_errors,
            )
            _sha256(
                declaration.get("content_hash"),
                "report.evidence_pack.content_hash",
                global_errors,
            )
            _integer(
                declaration.get("size_bytes"),
                "report.evidence_pack.size_bytes",
                global_errors,
                minimum=0,
            )

    dimensions_value = root.get("dimensions")
    dimensions_object = _exact_object(
        dimensions_value,
        set(DIMENSION_IDS),
        "report.dimensions",
        global_errors,
    )
    if dimensions_object is None:
        dimensions_object = {}
    declared_statuses = {
        dimension_id: (
            dimensions_object.get(dimension_id, {}).get("status")
            if isinstance(dimensions_object.get(dimension_id), dict)
            else None
        )
        for dimension_id in DIMENSION_IDS
    }
    materialized_claim_present = any(
        status in {"Current", "Benchmark-verified"}
        for status in declared_statuses.values()
    )
    invariant_errors: list[str] = []
    failed_invariants = _validate_invariants(
        root.get("zero_tolerance_invariants"),
        invariant_errors,
        evidence_pack=evidence_pack,
        materialized_required=materialized_claim_present,
    )
    global_errors.extend(invariant_errors)

    innovation = _evaluate_innovation_benchmark(
        root.get("innovation_benchmark"),
        evidence_pack=evidence_pack,
        raw_required=(
            declared_statuses.get("innovation_elicitation")
            == "Benchmark-verified"
        ),
    )

    dimension_results: dict[str, dict[str, Any]] = {}
    for dimension_id in DIMENSION_IDS:
        spec = DIMENSION_SPECS[dimension_id]
        result = _dimension_result_template()
        errors: list[str] = []
        path = f"report.dimensions.{dimension_id}"
        dimension = _exact_object(
            dimensions_object.get(dimension_id),
            DIMENSION_FIELDS,
            path,
            errors,
        )
        if dimension is None:
            dimension = {}
        status = dimension.get("status")
        result["declared_status"] = status if isinstance(status, str) else None
        if status not in {"Target", "Current", "Benchmark-verified"}:
            errors.append(
                f"{path}.status must be Target, Current, or Benchmark-verified"
            )
        materialized_required = status in {"Current", "Benchmark-verified"}
        if materialized_required and evidence_pack is None:
            errors.append(
                f"{path}.status {status!r} requires a verified evidence_pack; "
                "unmaterialized report claims cannot qualify"
            )
        if dimension.get("boundary") != spec["boundary"]:
            errors.append(f"{path}.boundary must be {spec['boundary']!r}")
        if dimension.get("target") != spec["target"]:
            errors.append(f"{path}.target must be {spec['target']!r}")

        exclusions = _string_list(
            dimension.get("exclusions"), f"{path}.exclusions", errors
        )
        if exclusions is not None and set(exclusions) != set(UNIVERSAL_EXCLUSIONS):
            missing = UNIVERSAL_EXCLUSIONS - set(exclusions)
            unsupported = set(exclusions) - UNIVERSAL_EXCLUSIONS
            if missing:
                errors.append(
                    f"{path}.exclusions missing: {', '.join(sorted(missing))}"
                )
            if unsupported:
                errors.append(
                    f"{path}.exclusions unsupported: "
                    + ", ".join(sorted(unsupported))
                )

        authority = _string_list(
            dimension.get("human_authority"), f"{path}.human_authority", errors
        )
        if authority is not None:
            unknown = set(authority) - AUTHORITY_KINDS
            missing = set(spec["authority"]) - set(authority)
            unexpected = set(authority) - set(spec["authority"])
            if unknown:
                errors.append(
                    f"{path}.human_authority unsupported: "
                    + ", ".join(sorted(unknown))
                )
            if missing:
                errors.append(
                    f"{path}.human_authority missing: "
                    + ", ".join(sorted(missing))
                )
            if unexpected - unknown:
                errors.append(
                    f"{path}.human_authority unexpected for this boundary: "
                    + ", ".join(sorted(unexpected - unknown))
                )

        stack_mode = spec["reference_stack"]
        stack = dimension.get("reference_stack")
        claim = innovation.get("comparison_claim")
        if stack_mode == "required":
            _validate_reference_stack(stack, f"{path}.reference_stack", errors)
        elif stack_mode == "forbidden" and stack is not None:
            errors.append(f"{path}.reference_stack must be null for this Core boundary")
        elif stack_mode == "conditional":
            if claim == "approaches_native_ecosystem":
                _validate_reference_stack(stack, f"{path}.reference_stack", errors)
            elif stack is not None:
                errors.append(
                    f"{path}.reference_stack must be null for the Track A Core claim"
                )

        metadata = dimension.get("benchmark_metadata")
        metadata_required = status == "Benchmark-verified" or (
            dimension_id == "innovation_elicitation"
        )
        if metadata_required:
            _validate_benchmark_metadata(
                metadata, f"{path}.benchmark_metadata", errors
            )
        elif metadata is not None:
            _validate_benchmark_metadata(
                metadata, f"{path}.benchmark_metadata", errors
            )
        if (
            materialized_required
            and isinstance(metadata, dict)
            and isinstance(metadata.get("retained_report"), str)
        ):
            if evidence_pack is None:
                errors.append(
                    f"{path}.benchmark_metadata.retained_report requires a verified evidence_pack"
                )
            else:
                try:
                    evidence_pack.resolve(
                        metadata["retained_report"],
                        f"{path}.benchmark_metadata.retained_report",
                    )
                except EvidencePackError as exc:
                    errors.append(str(exc))

        layer_statuses = _validate_evidence(
            dimension.get("evidence"),
            f"{path}.evidence",
            errors,
            dimension_id=dimension_id,
            evidence_pack=evidence_pack,
            materialized_required=materialized_required,
        )
        if status == "Target":
            layer_statuses = {
                layer: (
                    "declared_unverified"
                    if layer_status == "passed"
                    else layer_status
                )
                for layer, layer_status in layer_statuses.items()
            }
        result["evidence_layers"] = layer_statuses
        required_layers = set(spec["required_layers"])
        if status == "Benchmark-verified":
            required_layers.add("benchmark")
        if materialized_required:
            for layer in sorted(required_layers):
                if layer_statuses.get(layer) != "passed":
                    errors.append(
                        f"{path}.evidence.{layer} must contain passed evidence; "
                        "another evidence class cannot substitute for it"
                    )
        elif status == "Target":
            for layer in sorted(required_layers):
                if layer_statuses.get(layer) != "declared_unverified":
                    errors.append(
                        f"{path}.evidence.{layer} must declare structurally valid "
                        "planned evidence for the Target contract"
                    )

        if failed_invariants:
            errors.append(
                f"{path} is blocked by zero-tolerance invariant failure: "
                + ", ".join(failed_invariants)
            )
        if dimension_id == "innovation_elicitation":
            errors.extend(innovation["errors"])
            if status == "Current":
                errors.append(
                    f"{path}.status cannot be Current for a comparative Evo claim; "
                    "use Target until the frozen benchmark is retained, then "
                    "Benchmark-verified"
                )
            track_a_key = (
                "reported_track_a_thresholds_passed"
                if status == "Target"
                else "track_a_passed"
            )
            track_b_key = (
                "reported_track_b_thresholds_passed"
                if status == "Target"
                else "track_b_passed"
            )
            if innovation.get("comparison_claim") == "approaches_evoskills":
                if not innovation.get(track_a_key):
                    errors.append(f"{path} requires every frozen Track A threshold")
            elif innovation.get("comparison_claim") == "approaches_native_ecosystem":
                if not innovation.get(track_b_key):
                    errors.append(
                        f"{path} native-ecosystem wording requires Track A and Track B"
                    )

        errors = _stable_errors(errors)
        result["errors"] = errors
        result["passed"] = not errors
        if not errors:
            result["qualification"] = {
                "Target": "target_contract_valid",
                "Current": "current_retained_evidence_contract_valid",
                "Benchmark-verified": "benchmark_rows_recomputed",
            }[status]
            # This local validator has no external trust root. It can prove
            # byte integrity, schema conformance, deterministic recomputation,
            # and internal consistency, but not that a maintainer actually ran
            # the declared scenario or that a reviewer identity is genuine.
            result["capability_qualified"] = False
        dimension_results[dimension_id] = result

    global_errors = _stable_errors(global_errors)
    passed = not global_errors and all(
        result["passed"] for result in dimension_results.values()
    )
    return {
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "status": "pass" if passed else "fail",
        "passed": passed,
        "report_id": report_id,
        "errors": global_errors,
        "dimensions": dimension_results,
        "innovation_benchmark": innovation,
        "capability_qualified": False,
        "evidence_pack": {
            "verified": evidence_pack is not None,
            "manifest_hash": (
                evidence_pack.manifest_hash if evidence_pack is not None else None
            ),
        },
        "limitations": [
            "Target pass means only that the declared contract is structurally valid.",
            "Current and Benchmark-verified require a hashed materialized evidence pack.",
            "Retained local evidence is maintainer-attested; this validator has no external trust root and never independently qualifies a real-world capability.",
            "It is not workflow state or Gate authority.",
            "It does not certify scientific correctness, novelty, paper quality, or acceptance.",
            "Declared Target status is never promoted automatically.",
        ],
    }


def validate_report(path: Path) -> dict[str, Any]:
    """Load a report and verify its declared evidence pack before evaluation."""

    report, report_root = _read_report(path)
    declaration = report.get("evidence_pack")
    evidence_pack: EvidencePack | None = None
    evidence_pack_errors: list[str] = []
    if declaration is not None:
        try:
            evidence_pack = EvidencePack.load_from_root(report_root, declaration)
        except EvidencePackError as exc:
            evidence_pack_errors.append(str(exc))
    return evaluate_report(
        report,
        evidence_pack=evidence_pack,
        evidence_pack_errors=evidence_pack_errors,
    )


def input_error_result(message: str) -> dict[str, Any]:
    """Return the versioned CLI envelope for unreadable report input."""

    return {
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "status": "input_error",
        "passed": False,
        "errors": [message],
        "dimensions": {},
    }


__all__ = [
    "AcceptanceInputError",
    "REPORT_SCHEMA_VERSION",
    "RESULT_SCHEMA_VERSION",
    "VALIDATOR_VERSION",
    "evaluate_report",
    "input_error_result",
    "load_report",
    "validate_report",
]
