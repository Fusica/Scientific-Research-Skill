"""Shared schemas, paths, and immutable workflow constants."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = PLUGIN_ROOT / "skills/research/references/policy.yaml"
DEFAULT_MEMORY_TEMPLATE = PLUGIN_ROOT / "skills/research/assets/memory.template.md"
RESEARCH_DIR = ".research"
STATE_RELATIVE_PATH = Path(RESEARCH_DIR) / "state.json"
LOCK_RELATIVE_PATH = Path(RESEARCH_DIR) / "state.lock"
MEMORY_RELATIVE_PATH = Path(RESEARCH_DIR) / "memory.md"
LEGACY_RELATIVE_PATH = Path(RESEARCH_DIR) / "project-state.yaml"
LOCK_TIMEOUT_SECONDS = 60.0

GATE_IDS = (
    "idea_freeze",
    "method_experiment_approval",
    "claim_freeze",
    "release",
)
GATE_STATUSES = {"pending", "approved", "reopened"}
GATE_ACTIONS = {"approve", "reopen"}
ARTIFACT_METADATA_FIELDS = ("artifact_id", "version", "content_hash", "status")
ARTIFACT_POINTER_FIELDS = ("path", *ARTIFACT_METADATA_FIELDS)
RESERVED_ARTIFACT_IDS = frozenset(ARTIFACT_POINTER_FIELDS)
ARTIFACT_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
ARTIFACT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
STATE_FIELD_ORDER = (
    "schema_version",
    "workflow_version",
    "enabled",
    "project_id",
    "project_name",
    "current_stage",
    "gates",
    "artifacts",
    "last_checkpoint",
    "stage_history",
    "created_at",
    "updated_at",
)
REQUIRED_STATE_FIELDS = set(STATE_FIELD_ORDER)


class ResearchCtlError(RuntimeError):
    """An expected, user-actionable command failure."""

class TimestampExhaustionError(ResearchCtlError):
    """The persisted chronology has no representable successor timestamp."""

@dataclass(frozen=True)
class Policy:
    schema_version: Any
    workflow_version: str
    stage_order: tuple[str, ...]
    gate_order: tuple[str, ...]
    gate_specs: dict[str, dict[str, Any]]
    artifact_root: Path
    raw: dict[str, Any]
