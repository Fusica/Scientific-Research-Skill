"""Validate provider-neutral adapter requests and factual receipt observations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import (
    artifact_ref_errors,
    resolve_artifact_path,
    retained_artifact_reference,
)
from .constants import ARTIFACT_ID_RE, Policy, ResearchCtlError, SHA256_RE
from .gate_records import gate_record
from .jsonutil import (
    DuplicateJsonKeyError,
    NonStandardJsonConstantError,
    strict_json_loads,
)
from .timeutils import parse_utc_timestamp, utc_now, valid_timestamp


@dataclass(frozen=True)
class PendingAdapterExchange:
    """A source file checked before its adapter-exchange revision is published."""

    stage: str
    artifact_id: str
    path: Path
    registered_at: str


@dataclass(frozen=True)
class AdapterInspection:
    """Stable diagnostics returned through the adapter authority seam."""

    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    request_count: int
    receipt_count: int


@dataclass(frozen=True)
class _ExchangeRevision:
    stage: str
    artifact_id: str
    revision: int
    path: Path
    registered_at: str | None
    pending: bool = False

    @property
    def label(self) -> str:
        suffix = " pending" if self.pending else ""
        return (
            f"adapter exchange {self.stage}.{self.artifact_id} "
            f"r{self.revision}{suffix}"
        )


@dataclass(frozen=True)
class _ParsedExchange:
    source: _ExchangeRevision
    requests: tuple[dict[str, Any], ...]
    receipts: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class _RequestContext:
    exchange: _ParsedExchange
    request: dict[str, Any]
    receipts: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class _ProjectInspection:
    public: AdapterInspection
    requests: dict[str, _RequestContext]
    attempt_owners: dict[str, str]


def _field_errors(
    value: Any,
    expected: tuple[str, ...],
    label: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(value, dict):
        return None, [f"{label} must be an object"]
    errors: list[str] = []
    expected_set = set(expected)
    missing = expected_set - set(value)
    extra = set(value) - expected_set
    if missing:
        errors.append(f"{label} missing fields: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"{label} has unknown fields: {', '.join(sorted(extra))}")
    return value, errors


def _load_json(source: _ExchangeRevision, errors: list[str]) -> Any | None:
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


def _canonical_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _valid_identifier(value: Any) -> bool:
    return isinstance(value, str) and ARTIFACT_ID_RE.fullmatch(value) is not None


def _adapter_role_from_ref(reference: Any) -> str | None:
    if not isinstance(reference, dict):
        return None
    label = reference.get("label")
    if not isinstance(label, str):
        return None
    parts = label.split(".", 3)
    if len(parts) != 4 or parts[0] != "artifacts":
        return None
    return parts[2]


def _validate_artifact_refs(
    value: Any,
    *,
    label: str,
    root: Path,
    state: dict[str, Any],
    policy: Policy,
    errors: list[str],
    require_nonempty: bool,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or (require_nonempty and not value):
        qualifier = "a non-empty" if require_nonempty else "a"
        errors.append(f"{label} must be {qualifier} list")
        return []
    materialized: list[dict[str, Any]] = []
    fingerprints: set[str] = set()
    for index, candidate in enumerate(value):
        item_label = f"{label}[{index}]"
        problems = artifact_ref_errors(
            root,
            policy,
            candidate,
            item_label,
            verify_source=False,
            verify_snapshot=True,
        )
        errors.extend(problems)
        if not isinstance(candidate, dict) or problems:
            continue
        retained = retained_artifact_reference(state, policy, candidate)
        if retained != candidate:
            errors.append(
                f"{item_label} must exactly match one retained artifact revision"
            )
            continue
        if _adapter_role_from_ref(candidate) == policy.runtime.adapter_exchange_artifact_role:
            errors.append(f"{item_label} cannot reference an adapter exchange")
            continue
        fingerprint = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
        if fingerprint in fingerprints:
            errors.append(f"{item_label} duplicates an earlier artifact reference")
            continue
        fingerprints.add(fingerprint)
        materialized.append(candidate)
    return materialized


def _validate_retry_policy(
    value: Any,
    *,
    label: str,
    policy: Policy,
    errors: list[str],
) -> dict[str, Any] | None:
    retry, problems = _field_errors(
        value, policy.runtime.adapter_exchange_retry_policy_fields, label
    )
    errors.extend(problems)
    if retry is None or problems:
        return None
    mode = retry.get("mode")
    if mode not in policy.runtime.adapter_exchange_retry_modes:
        errors.append(f"{label}.mode {mode!r} is unsupported")
    max_attempts = retry.get("max_attempts")
    if type(max_attempts) is not int or not 1 <= max_attempts <= 100:
        errors.append(f"{label}.max_attempts must be an integer from 1 to 100")
    key = retry.get("idempotency_key")
    if mode == "never":
        if max_attempts != 1:
            errors.append(f"{label}.max_attempts must be 1 when mode is 'never'")
        if key is not None:
            errors.append(f"{label}.idempotency_key must be null when mode is 'never'")
    elif mode in {"idempotent", "reconcile_before_retry"}:
        if not _valid_identifier(key):
            errors.append(
                f"{label}.idempotency_key must be a stable non-empty identifier"
            )
    return retry


def _parse_request(
    value: Any,
    *,
    label: str,
    root: Path,
    state: dict[str, Any],
    policy: Policy,
    errors: list[str],
) -> dict[str, Any] | None:
    request, problems = _field_errors(
        value, policy.runtime.adapter_exchange_request_fields, label
    )
    errors.extend(problems)
    if request is None or problems:
        return None
    if not _valid_identifier(request.get("request_id")):
        errors.append(f"{label}.request_id has an invalid format")
    if request.get("operation_kind") not in policy.runtime.adapter_exchange_operation_kinds:
        errors.append(
            f"{label}.operation_kind {request.get('operation_kind')!r} is unsupported"
        )
    if not valid_timestamp(request.get("created_at")):
        errors.append(f"{label}.created_at must be a UTC timestamp")
    if request.get("effect_class") not in policy.runtime.adapter_exchange_effect_classes:
        errors.append(
            f"{label}.effect_class {request.get('effect_class')!r} is unsupported"
        )

    input_refs = _validate_artifact_refs(
        request.get("input_artifact_refs"),
        label=f"{label}.input_artifact_refs",
        root=root,
        state=state,
        policy=policy,
        errors=errors,
        require_nonempty=True,
    )
    payload, payload_problems = _field_errors(
        request.get("payload"),
        policy.runtime.adapter_exchange_payload_fields,
        f"{label}.payload",
    )
    errors.extend(payload_problems)
    if payload is not None and not payload_problems:
        payload_ref = payload.get("artifact_ref")
        payload_errors: list[str] = []
        validated_payload = _validate_artifact_refs(
            [payload_ref],
            label=f"{label}.payload.artifact_ref_wrapper",
            root=root,
            state=state,
            policy=policy,
            errors=payload_errors,
            require_nonempty=True,
        )
        errors.extend(
            problem.replace(
                f"{label}.payload.artifact_ref_wrapper[0]",
                f"{label}.payload.artifact_ref",
            ).replace(
                f"{label}.payload.artifact_ref_wrapper",
                f"{label}.payload.artifact_ref",
            )
            for problem in payload_errors
        )
        if validated_payload and payload_ref not in input_refs:
            errors.append(
                f"{label}.payload.artifact_ref must also appear in input_artifact_refs"
            )
        locator = payload.get("locator")
        if not isinstance(locator, str) or not locator.strip():
            errors.append(f"{label}.payload.locator must be a non-empty string")

    _validate_retry_policy(
        request.get("retry_policy"),
        label=f"{label}.retry_policy",
        policy=policy,
        errors=errors,
    )
    return request


def _parse_receipt(
    value: Any,
    *,
    label: str,
    root: Path,
    state: dict[str, Any],
    policy: Policy,
    errors: list[str],
) -> dict[str, Any] | None:
    receipt, problems = _field_errors(
        value, policy.runtime.adapter_exchange_receipt_fields, label
    )
    errors.extend(problems)
    if receipt is None or problems:
        return None
    for field in ("receipt_id", "request_id", "attempt_id"):
        if not _valid_identifier(receipt.get(field)):
            errors.append(f"{label}.{field} has an invalid format")
    for field in ("retry_of_attempt_id", "supersedes"):
        candidate = receipt.get(field)
        if candidate is not None and not _valid_identifier(candidate):
            errors.append(f"{label}.{field} must be null or a valid identifier")
    if not isinstance(receipt.get("request_hash"), str) or not SHA256_RE.fullmatch(
        receipt["request_hash"]
    ):
        errors.append(f"{label}.request_hash must be a sha256 digest")
    adapter, adapter_problems = _field_errors(
        receipt.get("adapter"),
        policy.runtime.adapter_exchange_adapter_fields,
        f"{label}.adapter",
    )
    errors.extend(adapter_problems)
    if adapter is not None and not adapter_problems:
        for field in ("adapter_id", "adapter_version"):
            if not isinstance(adapter.get(field), str) or not adapter[field].strip():
                errors.append(f"{label}.adapter.{field} must be non-empty")
        if adapter.get("protocol_version") != (
            policy.runtime.adapter_exchange_protocol_version
        ):
            errors.append(
                f"{label}.adapter.protocol_version must be "
                f"{policy.runtime.adapter_exchange_protocol_version!r}"
            )
    if receipt.get("status") not in policy.runtime.adapter_exchange_receipt_statuses:
        errors.append(f"{label}.status {receipt.get('status')!r} is unsupported")
    if not valid_timestamp(receipt.get("observed_at")):
        errors.append(f"{label}.observed_at must be a UTC timestamp")
    external_id = receipt.get("external_id")
    if external_id is not None and (
        not isinstance(external_id, str) or not external_id.strip()
    ):
        errors.append(f"{label}.external_id must be null or a non-empty string")
    if not isinstance(receipt.get("message"), str) or not receipt["message"].strip():
        errors.append(f"{label}.message must be a non-empty factual observation")
    for field in ("output_artifact_refs", "log_artifact_refs"):
        _validate_artifact_refs(
            receipt.get(field),
            label=f"{label}.{field}",
            root=root,
            state=state,
            policy=policy,
            errors=errors,
            require_nonempty=False,
        )
    return receipt


def _parse_exchange(
    source: _ExchangeRevision,
    *,
    root: Path,
    state: dict[str, Any],
    policy: Policy,
    errors: list[str],
) -> _ParsedExchange | None:
    raw = _load_json(source, errors)
    manifest, problems = _field_errors(
        raw, policy.runtime.adapter_exchange_manifest_fields, source.label
    )
    errors.extend(problems)
    if manifest is None or problems:
        return None
    if manifest.get("schema_version") != (
        policy.runtime.adapter_exchange_manifest_schema_version
    ):
        errors.append(
            f"{source.label}.schema_version must be "
            f"{policy.runtime.adapter_exchange_manifest_schema_version!r}"
        )
    if manifest.get("stage") != source.stage:
        errors.append(
            f"{source.label}.stage must match registered stage {source.stage!r}"
        )
    raw_requests = manifest.get("requests")
    raw_receipts = manifest.get("receipts")
    if not isinstance(raw_requests, list) or not raw_requests:
        errors.append(f"{source.label}.requests must be a non-empty list")
        raw_requests = []
    if not isinstance(raw_receipts, list):
        errors.append(f"{source.label}.receipts must be a list")
        raw_receipts = []
    requests = tuple(
        candidate
        for index, value in enumerate(raw_requests)
        if (
            candidate := _parse_request(
                value,
                label=f"{source.label}.requests[{index}]",
                root=root,
                state=state,
                policy=policy,
                errors=errors,
            )
        )
        is not None
    )
    receipts = tuple(
        candidate
        for index, value in enumerate(raw_receipts)
        if (
            candidate := _parse_receipt(
                value,
                label=f"{source.label}.receipts[{index}]",
                root=root,
                state=state,
                policy=policy,
                errors=errors,
            )
        )
        is not None
    )
    return _ParsedExchange(source=source, requests=requests, receipts=receipts)


def _registered_exchange_revisions(
    root: Path,
    state: dict[str, Any],
    policy: Policy,
) -> list[_ExchangeRevision]:
    sources: list[_ExchangeRevision] = []
    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        return sources
    role = policy.runtime.adapter_exchange_artifact_role
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
                registered_at = revision.get("registered_at")
                if (
                    type(number) is not int
                    or not isinstance(snapshot_path, str)
                    or not isinstance(registered_at, str)
                ):
                    continue
                try:
                    path = resolve_artifact_path(root, snapshot_path)
                except ResearchCtlError:
                    continue
                sources.append(
                    _ExchangeRevision(
                        stage=stage,
                        artifact_id=artifact_id,
                        revision=number,
                        path=path,
                        registered_at=registered_at,
                    )
                )
    return sources


def _operation_spec(policy: Policy, operation_kind: Any) -> dict[str, Any] | None:
    authority = policy.raw.get("adapter_authority")
    operations = authority.get("operation_kinds") if isinstance(authority, dict) else None
    spec = operations.get(operation_kind) if isinstance(operations, dict) else None
    return spec if isinstance(spec, dict) else None


def _stage_at(state: dict[str, Any], policy: Policy, timestamp: str) -> str | None:
    boundary = parse_utc_timestamp(timestamp)
    if boundary is None:
        return None
    stage = policy.stage_order[0]
    transitions = state.get("stage_history")
    if not isinstance(transitions, list):
        return None
    for transition in transitions:
        if not isinstance(transition, dict):
            continue
        occurred = parse_utc_timestamp(transition.get("timestamp"))
        if occurred is not None and occurred <= boundary:
            destination = transition.get("to_stage")
            if destination in policy.stage_order:
                stage = destination
    return stage


def _gate_binding_errors(
    request: dict[str, Any],
    *,
    source: _ExchangeRevision,
    state: dict[str, Any],
    policy: Policy,
    require_current: bool,
) -> list[str]:
    request_id = request.get("request_id")
    label = f"adapter request {request_id!r}"
    spec = _operation_spec(policy, request.get("operation_kind"))
    if spec is None:
        return [f"{label} has no policy authority contract"]
    errors: list[str] = []
    allowed_stages = spec.get("allowed_stages")
    if source.stage not in allowed_stages:
        errors.append(
            f"{label} operation {request.get('operation_kind')!r} is not allowed "
            f"in stage {source.stage!r}"
        )
    effective_stage = (
        state.get("current_stage")
        if require_current
        else _stage_at(state, policy, source.registered_at or "")
    )
    if effective_stage != source.stage:
        qualifier = "current" if require_current else "registration"
        errors.append(
            f"{label} stage {source.stage!r} does not match {qualifier} stage "
            f"{effective_stage!r}"
        )
    effect_class = request.get("effect_class")
    if effect_class not in spec.get("allowed_effect_classes", []):
        errors.append(
            f"{label} effect_class {effect_class!r} is not allowed for "
            f"{request.get('operation_kind')!r}"
        )

    required_refs = spec.get("required_gate_refs")
    binding = request.get("gate_binding")
    if not required_refs:
        if binding is not None:
            errors.append(f"{label}.gate_binding must be null for this operation")
        return errors
    gate_binding, problems = _field_errors(
        binding,
        policy.runtime.adapter_exchange_gate_binding_fields,
        f"{label}.gate_binding",
    )
    errors.extend(problems)
    if gate_binding is None or problems:
        return errors
    gate_ref = gate_binding.get("gate_ref")
    if not isinstance(gate_ref, dict) or set(gate_ref) not in (
        set(policy.runtime.gate_ref_required_fields),
        {
            *policy.runtime.gate_ref_required_fields,
            *policy.runtime.gate_ref_optional_fields,
        },
    ):
        errors.append(f"{label}.gate_binding.gate_ref has invalid fields")
        return errors
    if gate_ref not in required_refs:
        errors.append(
            f"{label} is not bound to a policy-required GateRef for "
            f"{request.get('operation_kind')!r}"
        )
        return errors
    decision_id = gate_binding.get("gate_decision_id")
    if not _valid_identifier(decision_id):
        errors.append(f"{label}.gate_binding.gate_decision_id is invalid")
        return errors
    gate = gate_ref.get("gate")
    target = gate_ref.get("target")
    record = gate_record(state, policy, gate, target)
    if not isinstance(record, dict):
        errors.append(f"{label} references a missing Gate record")
        return errors
    history = record.get("history")
    if not isinstance(history, list):
        errors.append(f"{label} references an invalid Gate history")
        return errors
    decision = next(
        (
            candidate
            for candidate in history
            if isinstance(candidate, dict)
            and candidate.get("decision_id") == decision_id
        ),
        None,
    )
    if not isinstance(decision, dict) or decision.get("action") != "approve":
        errors.append(f"{label} gate_decision_id must name an approval decision")
        return errors
    if gate_binding.get("artifact_refs") != decision.get("artifact_refs"):
        errors.append(
            f"{label}.gate_binding.artifact_refs must exactly match its Gate decision"
        )
    approved_refs = gate_binding.get("artifact_refs")
    input_refs = request.get("input_artifact_refs")
    if isinstance(approved_refs, list) and isinstance(input_refs, list):
        for index, reference in enumerate(approved_refs):
            if reference not in input_refs:
                errors.append(
                    f"{label}.input_artifact_refs must include approved Gate "
                    f"artifact_refs[{index}]"
                )
    if request.get("operation_kind") == "external_release":
        payload = request.get("payload")
        payload_ref = payload.get("artifact_ref") if isinstance(payload, dict) else None
        if isinstance(approved_refs, list) and payload_ref not in approved_refs:
            errors.append(
                f"{label}.payload.artifact_ref must be an exact artifact revision "
                "from the approved release package"
            )
        if isinstance(approved_refs, list) and input_refs != approved_refs:
            errors.append(
                f"{label}.input_artifact_refs must exactly match the ordered "
                "approved release package without extra artifacts"
            )
    if require_current:
        if (
            record.get("status") != "approved"
            or record.get("latest_decision_id") != decision_id
        ):
            errors.append(f"{label} does not match the current approved Gate binding")
    else:
        registered = parse_utc_timestamp(source.registered_at)
        decisions_at_registration = [
            candidate
            for candidate in history
            if isinstance(candidate, dict)
            and (decided := parse_utc_timestamp(candidate.get("decided_at")))
            is not None
            and registered is not None
            and decided <= registered
        ]
        latest = decisions_at_registration[-1] if decisions_at_registration else None
        if not isinstance(latest, dict) or (
            latest.get("decision_id") != decision_id
            or latest.get("action") != "approve"
        ):
            errors.append(
                f"{label} was not bound to the current approved Gate decision "
                "when first registered"
            )
    return errors


def _request_authority_errors(
    request: dict[str, Any],
    *,
    source: _ExchangeRevision,
    state: dict[str, Any],
    policy: Policy,
    require_current: bool,
) -> list[str]:
    errors = _gate_binding_errors(
        request,
        source=source,
        state=state,
        policy=policy,
        require_current=require_current,
    )
    label = f"adapter request {request.get('request_id')!r}"
    authority = policy.raw["adapter_authority"]
    protected = authority["human_authorization_effect_classes"]
    declaration = request.get("human_authorization")
    authorized_at = None
    if request.get("effect_class") in protected:
        authorization, problems = _field_errors(
            declaration,
            policy.runtime.adapter_exchange_human_authorization_fields,
            f"{label}.human_authorization",
        )
        errors.extend(problems)
        if authorization is not None and not problems:
            if not _valid_identifier(authorization.get("authorization_id")):
                errors.append(
                    f"{label}.human_authorization.authorization_id is invalid"
                )
            for field in ("actor", "scope"):
                if not isinstance(authorization.get(field), str) or not authorization[
                    field
                ].strip():
                    errors.append(
                        f"{label}.human_authorization.{field} must be non-empty"
                    )
            if not valid_timestamp(authorization.get("authorized_at")):
                errors.append(
                    f"{label}.human_authorization.authorized_at must be a UTC timestamp"
                )
            else:
                authorized_at = parse_utc_timestamp(authorization.get("authorized_at"))
    elif declaration is not None:
        errors.append(
            f"{label}.human_authorization must be null for a low-risk effect"
        )
    created_at = parse_utc_timestamp(request.get("created_at"))
    registered_at = parse_utc_timestamp(source.registered_at)
    if (
        created_at is not None
        and registered_at is not None
        and created_at > registered_at
    ):
        errors.append(f"{label}.created_at cannot follow its first registration")
    if (
        authorized_at is not None
        and registered_at is not None
        and authorized_at > registered_at
    ):
        errors.append(
            f"{label}.human_authorization.authorized_at cannot follow its first "
            "registration"
        )
    return errors


def _lineage(
    exchange: _ParsedExchange,
    *,
    policy: Policy,
    errors: list[str],
    warnings: list[str],
    global_receipt_ids: set[str],
    global_attempt_owners: dict[str, str],
    dispatch_journal_receipt_ids: set[str],
) -> tuple[dict[str, tuple[dict[str, Any], ...]], dict[str, list[str]]]:
    requests = {
        request.get("request_id"): request
        for request in exchange.requests
        if _valid_identifier(request.get("request_id"))
    }
    receipts_by_request: dict[str, list[dict[str, Any]]] = {
        request_id: [] for request_id in requests
    }
    attempts_by_request: dict[str, list[str]] = {
        request_id: [] for request_id in requests
    }
    latest_by_attempt: dict[str, dict[str, Any]] = {}
    retry_parent: dict[str, str | None] = {}
    for index, receipt in enumerate(exchange.receipts):
        label = f"{exchange.source.label}.receipts[{index}]"
        receipt_id = receipt.get("receipt_id")
        request_id = receipt.get("request_id")
        attempt_id = receipt.get("attempt_id")
        if not _valid_identifier(receipt_id):
            continue
        if receipt_id in global_receipt_ids:
            errors.append(f"{label}.receipt_id {receipt_id!r} is duplicated")
        global_receipt_ids.add(receipt_id)
        request = requests.get(request_id)
        if request is None:
            errors.append(
                f"{label}.request_id must name a request in the same adapter exchange"
            )
            continue
        if receipt.get("request_hash") != _canonical_hash(request):
            errors.append(f"{label}.request_hash does not match its immutable request")
        observed = parse_utc_timestamp(receipt.get("observed_at"))
        created = parse_utc_timestamp(request.get("created_at"))
        if observed is not None and created is not None and observed < created:
            errors.append(f"{label}.observed_at cannot precede the request")
        if not _valid_identifier(attempt_id):
            continue
        owner = global_attempt_owners.setdefault(attempt_id, request_id)
        if owner != request_id:
            errors.append(
                f"{label}.attempt_id {attempt_id!r} is already owned by request {owner!r}"
            )
            continue
        prior = latest_by_attempt.get(attempt_id)
        if prior is None:
            if receipt.get("status") != "accepted":
                warnings.append(
                    f"{label} is a nonconforming fact import: the first receipt for "
                    "an attempt must be a durable accepted journal before any side "
                    "effect"
                )
            if receipt.get("supersedes") is not None:
                errors.append(f"{label}.supersedes must be null for a new attempt")
            prior_attempts = attempts_by_request[request_id]
            prior_attempt_id = prior_attempts[-1] if prior_attempts else None
            retry_of = receipt.get("retry_of_attempt_id")
            if not prior_attempts:
                if retry_of is not None:
                    errors.append(
                        f"{label}.retry_of_attempt_id must be null for the first attempt"
                    )
            elif retry_of != prior_attempts[-1]:
                errors.append(
                    f"{label}.retry_of_attempt_id must name the latest prior attempt"
                )
            attempts_by_request[request_id].append(attempt_id)
            retry_parent[attempt_id] = retry_of
            if prior_attempt_id is not None:
                prior_receipt = latest_by_attempt.get(prior_attempt_id)
                retry = request.get("retry_policy", {})
                mode = retry.get("mode")
                prior_status = (
                    prior_receipt.get("status")
                    if isinstance(prior_receipt, dict)
                    else None
                )
                allowed_statuses = (
                    {"failed", "cancelled", "unknown"}
                    if mode == "idempotent"
                    else {"failed", "cancelled"}
                    if mode == "reconcile_before_retry"
                    else set()
                )
                if prior_status not in allowed_statuses:
                    message = (
                        f"{label} reports a retry that the registered retry policy "
                        "would not authorize for dispatch"
                    )
                    if receipt_id in dispatch_journal_receipt_ids:
                        errors.append(message)
                    else:
                        warnings.append(message)
                if len(prior_attempts) > retry.get("max_attempts", 0):
                    message = (
                        f"{label} reports an attempt beyond retry_policy.max_attempts"
                    )
                    if receipt_id in dispatch_journal_receipt_ids:
                        errors.append(message)
                    else:
                        warnings.append(message)
        else:
            if receipt.get("status") == "accepted":
                warnings.append(
                    f"{label} reports accepted after the attempt already had a "
                    "receipt; it is preserved as a fact but is not a dispatch journal"
                )
            if receipt.get("retry_of_attempt_id") != retry_parent[attempt_id]:
                errors.append(
                    f"{label}.retry_of_attempt_id must remain stable for one attempt"
                )
            if receipt.get("supersedes") != prior.get("receipt_id"):
                errors.append(
                    f"{label}.supersedes must name the latest receipt for this attempt"
                )
        latest_by_attempt[attempt_id] = receipt
        receipts_by_request[request_id].append(receipt)
    return (
        {key: tuple(value) for key, value in receipts_by_request.items()},
        attempts_by_request,
    )


def _inspect_project(
    root: Path,
    state: dict[str, Any],
    policy: Policy,
    *,
    pending: PendingAdapterExchange | None = None,
) -> _ProjectInspection:
    errors: list[str] = []
    warnings: list[str] = []
    sources = _registered_exchange_revisions(root, state, policy)
    if pending is not None:
        existing_numbers = [
            source.revision
            for source in sources
            if source.stage == pending.stage and source.artifact_id == pending.artifact_id
        ]
        sources.append(
            _ExchangeRevision(
                stage=pending.stage,
                artifact_id=pending.artifact_id,
                revision=max(existing_numbers, default=0) + 1,
                path=pending.path,
                registered_at=pending.registered_at,
                pending=True,
            )
        )
    parsed = [
        exchange
        for source in sorted(
            sources, key=lambda item: (item.stage, item.artifact_id, item.revision)
        )
        if (
            exchange := _parse_exchange(
                source,
                root=root,
                state=state,
                policy=policy,
                errors=errors,
            )
        )
        is not None
    ]

    histories: dict[tuple[str, str], list[_ParsedExchange]] = {}
    for exchange in parsed:
        histories.setdefault(
            (exchange.source.stage, exchange.source.artifact_id), []
        ).append(exchange)

    current: list[_ParsedExchange] = []
    first_sources: dict[tuple[str, str, str], _ExchangeRevision] = {}
    first_receipt_sources: dict[tuple[str, str, str], _ExchangeRevision] = {}
    pending_new_requests: set[tuple[str, str, str]] = set()
    dispatch_journals: set[tuple[str, str, str]] = set()
    pending_dispatch_journals: set[tuple[str, str, str]] = set()
    for key, history in histories.items():
        history.sort(key=lambda item: item.source.revision)
        previous: _ParsedExchange | None = None
        for exchange in history:
            request_prefix = previous.requests if previous is not None else ()
            receipt_prefix = previous.receipts if previous is not None else ()
            if (
                len(exchange.requests) < len(request_prefix)
                or exchange.requests[: len(request_prefix)] != request_prefix
            ):
                errors.append(
                    f"{exchange.source.label} must preserve prior requests as an "
                    "append-only prefix"
                )
            if (
                len(exchange.receipts) < len(receipt_prefix)
                or exchange.receipts[: len(receipt_prefix)] != receipt_prefix
            ):
                errors.append(
                    f"{exchange.source.label} must preserve prior receipts as an "
                    "append-only prefix"
                )
            for request in exchange.requests[len(request_prefix) :]:
                request_id = request.get("request_id")
                if _valid_identifier(request_id):
                    identity = (key[0], key[1], request_id)
                    first_sources.setdefault(identity, exchange.source)
                    if exchange.source.pending:
                        pending_new_requests.add(identity)
            known_attempts = {
                receipt.get("attempt_id")
                for receipt in receipt_prefix
                if _valid_identifier(receipt.get("attempt_id"))
            }
            newly_journaled_attempts: set[str] = set()
            for receipt in exchange.receipts[len(receipt_prefix) :]:
                receipt_id = receipt.get("receipt_id")
                if _valid_identifier(receipt_id):
                    first_receipt_sources.setdefault(
                        (key[0], key[1], receipt_id), exchange.source
                    )
                attempt_id = receipt.get("attempt_id")
                first_for_attempt = (
                    _valid_identifier(attempt_id) and attempt_id not in known_attempts
                )
                if (
                    first_for_attempt
                    and receipt.get("status") == "accepted"
                    and _valid_identifier(receipt_id)
                ):
                    identity = (key[0], key[1], receipt_id)
                    dispatch_journals.add(identity)
                    newly_journaled_attempts.add(attempt_id)
                    if exchange.source.pending:
                        pending_dispatch_journals.add(identity)
                elif (
                    _valid_identifier(attempt_id)
                    and attempt_id in newly_journaled_attempts
                ):
                    errors.append(
                        f"{exchange.source.label} must register an attempt's first "
                        "accepted receipt before appending any later observation "
                        "for that attempt"
                    )
                if _valid_identifier(attempt_id):
                    known_attempts.add(attempt_id)
            if exchange.source.pending:
                new_request_ids = {
                    request.get("request_id")
                    for request in exchange.requests[len(request_prefix) :]
                }
                for receipt in exchange.receipts[len(receipt_prefix) :]:
                    if receipt.get("request_id") in new_request_ids:
                        errors.append(
                            f"{exchange.source.label} cannot append a request and its "
                            "receipt in the same revision; persist the request before "
                            "any adapter side effect"
                        )
            previous = exchange
        current.append(history[-1])

    request_contexts: dict[str, _RequestContext] = {}
    authorization_owners: dict[str, str] = {}
    receipt_ids: set[str] = set()
    attempt_owners: dict[str, str] = {}
    receipt_count = 0
    for exchange in current:
        local_ids: set[str] = set()
        for index, request in enumerate(exchange.requests):
            request_id = request.get("request_id")
            label = f"{exchange.source.label}.requests[{index}]"
            if not _valid_identifier(request_id):
                continue
            if request_id in local_ids:
                errors.append(f"{label}.request_id {request_id!r} is duplicated")
            local_ids.add(request_id)
            if request_id in request_contexts:
                errors.append(
                    f"{label}.request_id {request_id!r} duplicates a project request"
                )
            identity = (exchange.source.stage, exchange.source.artifact_id, request_id)
            first_source = first_sources.get(identity, exchange.source)
            errors.extend(
                _request_authority_errors(
                    request,
                    source=first_source,
                    state=state,
                    policy=policy,
                    require_current=identity in pending_new_requests,
                )
            )
            authorization = request.get("human_authorization")
            authorization_id = (
                authorization.get("authorization_id")
                if isinstance(authorization, dict)
                else None
            )
            if _valid_identifier(authorization_id):
                owner = authorization_owners.setdefault(authorization_id, request_id)
                if owner != request_id:
                    errors.append(
                        f"{label}.human_authorization.authorization_id is already "
                        f"owned by request {owner!r}"
                    )

        receipts_by_request, _attempts = _lineage(
            exchange,
            policy=policy,
            errors=errors,
            warnings=warnings,
            global_receipt_ids=receipt_ids,
            global_attempt_owners=attempt_owners,
            dispatch_journal_receipt_ids={
                receipt_id
                for receipt_stage, receipt_artifact_id, receipt_id in (
                    pending_dispatch_journals
                )
                if receipt_stage == exchange.source.stage
                and receipt_artifact_id == exchange.source.artifact_id
            },
        )
        receipt_count += len(exchange.receipts)
        requests_by_id = {
            request.get("request_id"): request
            for request in exchange.requests
            if _valid_identifier(request.get("request_id"))
        }
        for index, receipt in enumerate(exchange.receipts):
            receipt_id = receipt.get("receipt_id")
            if not _valid_identifier(receipt_id):
                continue
            receipt_identity = (
                exchange.source.stage,
                exchange.source.artifact_id,
                receipt_id,
            )
            first_receipt_source = first_receipt_sources.get(
                receipt_identity, exchange.source
            )
            observed_at = parse_utc_timestamp(receipt.get("observed_at"))
            first_registered_at = parse_utc_timestamp(
                first_receipt_source.registered_at
            )
            if (
                observed_at is not None
                and first_registered_at is not None
                and observed_at > first_registered_at
            ):
                errors.append(
                    f"{exchange.source.label}.receipts[{index}].observed_at cannot "
                    "follow its first registration"
                )
            if receipt_identity in dispatch_journals:
                request = requests_by_id.get(receipt.get("request_id"))
                if isinstance(request, dict):
                    errors.extend(
                        _request_authority_errors(
                            request,
                            source=first_receipt_source,
                            state=state,
                            policy=policy,
                            require_current=(
                                receipt_identity in pending_dispatch_journals
                            ),
                        )
                    )
        for request in exchange.requests:
            request_id = request.get("request_id")
            if _valid_identifier(request_id) and request_id not in request_contexts:
                request_contexts[request_id] = _RequestContext(
                    exchange=exchange,
                    request=request,
                    receipts=receipts_by_request.get(request_id, ()),
                )

    public = AdapterInspection(
        errors=tuple(errors),
        warnings=tuple(warnings),
        request_count=len(request_contexts),
        receipt_count=receipt_count,
    )
    return _ProjectInspection(
        public=public,
        requests=request_contexts,
        attempt_owners=attempt_owners,
    )


def inspect_adapter_exchanges(
    root: Path,
    state: dict[str, Any],
    policy: Policy,
    *,
    pending: PendingAdapterExchange | None = None,
) -> AdapterInspection:
    """Validate every immutable request/receipt revision without executing it."""

    return _inspect_project(root, state, policy, pending=pending).public


def verify_adapter_dispatch(
    root: Path,
    state: dict[str, Any],
    policy: Policy,
    *,
    request_id: str,
    attempt_id: str,
    retry_of_attempt_id: str | None,
) -> dict[str, Any]:
    """Return a time-of-check envelope for one conforming Adapter dispatch.

    This function never invokes an Adapter and never turns an Adapter observation
    into Gate authority or scientific judgment.
    """

    if not _valid_identifier(request_id):
        raise ResearchCtlError("adapter request ID contains unsupported characters")
    if not _valid_identifier(attempt_id):
        raise ResearchCtlError("adapter attempt ID contains unsupported characters")
    if retry_of_attempt_id is not None and not _valid_identifier(retry_of_attempt_id):
        raise ResearchCtlError("adapter retry parent contains unsupported characters")
    project = _inspect_project(root, state, policy)
    if project.public.errors:
        raise ResearchCtlError(
            "adapter exchange is invalid; run `researchctl doctor`: "
            + "; ".join(project.public.errors[:3])
        )
    context = project.requests.get(request_id)
    if context is None:
        raise ResearchCtlError(f"unknown adapter request {request_id!r}")
    if attempt_id in project.attempt_owners:
        raise ResearchCtlError(f"adapter attempt {attempt_id!r} already has a receipt")
    current_errors = _request_authority_errors(
        context.request,
        source=context.exchange.source,
        state=state,
        policy=policy,
        require_current=True,
    )
    if current_errors:
        raise ResearchCtlError("; ".join(current_errors[:3]))

    attempts: list[str] = []
    latest_by_attempt: dict[str, dict[str, Any]] = {}
    for receipt in context.receipts:
        candidate = receipt["attempt_id"]
        if candidate not in attempts:
            attempts.append(candidate)
        latest_by_attempt[candidate] = receipt
    retry = context.request["retry_policy"]
    if not attempts:
        if retry_of_attempt_id is not None:
            raise ResearchCtlError(
                "the first adapter attempt cannot declare --retry-of-attempt-id"
            )
    else:
        latest_attempt = attempts[-1]
        if retry_of_attempt_id != latest_attempt:
            raise ResearchCtlError(
                "adapter retry must name the latest prior attempt with "
                "--retry-of-attempt-id"
            )
        if len(attempts) >= retry["max_attempts"]:
            raise ResearchCtlError("adapter retry_policy.max_attempts is exhausted")
        prior_status = latest_by_attempt[latest_attempt]["status"]
        mode = retry["mode"]
        if mode == "never":
            raise ResearchCtlError("adapter retry policy forbids every retry")
        if mode == "reconcile_before_retry" and prior_status == "unknown":
            raise ResearchCtlError(
                "adapter attempt outcome is unknown; reconcile the same attempt "
                "before retry"
            )
        allowed = (
            {"failed", "cancelled", "unknown"}
            if mode == "idempotent"
            else {"failed", "cancelled"}
        )
        if prior_status not in allowed:
            raise ResearchCtlError(
                f"adapter attempt status {prior_status!r} is not retryable"
            )

    request_hash = _canonical_hash(context.request)
    envelope = {
        "schema_version": policy.runtime.adapter_exchange_manifest_schema_version,
        "verification": "accepted",
        "verified_at": utc_now(),
        "request_hash": request_hash,
        "attempt_id": attempt_id,
        "retry_of_attempt_id": retry_of_attempt_id,
        "request": context.request,
    }
    if set(envelope) != set(policy.runtime.adapter_exchange_verification_fields):
        raise ResearchCtlError("runtime adapter verification fields are incompatible")
    return envelope
