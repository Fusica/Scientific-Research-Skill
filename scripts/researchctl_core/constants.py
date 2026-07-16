"""Shared schemas, paths, and immutable workflow constants."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = PLUGIN_ROOT / "skills/research/references/policy.yaml"
DEFAULT_RUNTIME_CONTRACT_PATH = (
    PLUGIN_ROOT / "skills/research/assets/runtime-contract.json"
)
DEFAULT_MEMORY_TEMPLATE = PLUGIN_ROOT / "skills/research/assets/memory.template.md"
RESEARCH_DIR = ".research"
STATE_RELATIVE_PATH = Path(RESEARCH_DIR) / "state.json"
LOCK_RELATIVE_PATH = Path(RESEARCH_DIR) / "state.lock"
MEMORY_RELATIVE_PATH = Path(RESEARCH_DIR) / "memory.md"
DASHBOARD_RELATIVE_PATH = Path(RESEARCH_DIR) / "dashboard.html"
LEGACY_RELATIVE_PATH = Path(RESEARCH_DIR) / "project-state.yaml"
LOCK_TIMEOUT_SECONDS = 60.0
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
SUPPORTED_RUNTIME_CONTRACT_VERSION = "2.0"
CLEAN_BREAK_REINIT_GUIDANCE = (
    "preserve anything needed, delete .research, then run `researchctl init`; "
    "no automatic migration"
)

ARTIFACT_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
ARTIFACT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ResearchCtlError(RuntimeError):
    """An expected, user-actionable command failure."""


class TimestampExhaustionError(ResearchCtlError):
    """The persisted chronology has no representable successor timestamp."""


@dataclass(frozen=True)
class RuntimeContract:
    contract_version: str
    state_schema_version: str
    state_required_fields: tuple[str, ...]
    decision_required_fields: tuple[str, ...]
    lifecycle_statuses: tuple[str, ...]
    lifecycle_actions: tuple[str, ...]
    lifecycle_record_fields: tuple[str, ...]
    lifecycle_decision_fields: tuple[str, ...]
    lifecycle_decision_optional_fields: tuple[str, ...]
    activation_actions: tuple[str, ...]
    activation_event_fields: tuple[str, ...]
    gate_statuses: tuple[str, ...]
    gate_actions: tuple[str, ...]
    gate_record_fields: tuple[str, ...]
    gate_target_container_fields: tuple[str, ...]
    gate_decision_optional_fields: tuple[str, ...]
    cascade_fields: tuple[str, ...]
    gate_ref_required_fields: tuple[str, ...]
    gate_ref_optional_fields: tuple[str, ...]
    selection_fields: tuple[str, ...]
    artifact_entry_fields: tuple[str, ...]
    artifact_revision_fields: tuple[str, ...]
    artifact_reference_prefix_fields: tuple[str, ...]
    checkpoint_fields: tuple[str, ...]
    stage_transition_fields: tuple[str, ...]
    stage_transition_trigger_prefixes: tuple[str, ...]
    scientific_record_manifest_schema_version: str
    scientific_record_artifact_role: str
    scientific_record_manifest_fields: tuple[str, ...]
    scientific_record_fields: tuple[str, ...]
    scientific_record_source_fields: tuple[str, ...]
    scientific_record_relation_fields: tuple[str, ...]
    scientific_record_kinds: tuple[str, ...]
    scientific_record_relation_kinds: tuple[str, ...]
    scientific_record_relation_signatures: dict[
        str, tuple[tuple[str, ...], tuple[str, ...]]
    ]
    adapter_exchange_manifest_schema_version: str
    adapter_exchange_protocol_version: str
    adapter_exchange_artifact_role: str
    adapter_exchange_manifest_fields: tuple[str, ...]
    adapter_exchange_request_fields: tuple[str, ...]
    adapter_exchange_payload_fields: tuple[str, ...]
    adapter_exchange_gate_binding_fields: tuple[str, ...]
    adapter_exchange_human_authorization_fields: tuple[str, ...]
    adapter_exchange_retry_policy_fields: tuple[str, ...]
    adapter_exchange_receipt_fields: tuple[str, ...]
    adapter_exchange_adapter_fields: tuple[str, ...]
    adapter_exchange_verification_fields: tuple[str, ...]
    adapter_exchange_operation_kinds: tuple[str, ...]
    adapter_exchange_effect_classes: tuple[str, ...]
    adapter_exchange_retry_modes: tuple[str, ...]
    adapter_exchange_receipt_statuses: tuple[str, ...]
    raw: dict[str, Any]


@dataclass(frozen=True)
class Policy:
    schema_version: str
    workflow_version: str
    stage_order: tuple[str, ...]
    gate_order: tuple[str, ...]
    gate_sequence: tuple[tuple[str, str | None], ...]
    stage_transitions: dict[str, list[dict[str, Any]]]
    stage_exit_requirements: dict[str, dict[str, str] | None]
    gate_specs: dict[str, dict[str, Any]]
    release_gate: str
    release_targets: tuple[str, ...]
    initial_release_target: str
    artifact_root: Path
    snapshot_root: Path
    runtime: RuntimeContract
    raw: dict[str, Any]
