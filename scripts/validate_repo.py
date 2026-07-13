#!/usr/bin/env python3
"""Validate the compact Scientific Research Skill plugin."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - reported as a validation error
    yaml = None


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "scientific-research-skill"
PLUGIN_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0"

STAGES = [
    "idea",
    "literature",
    "method",
    "experiment_results",
    "paper",
    "revision",
]
GATES = [
    "idea_freeze",
    "method_experiment_approval",
    "claim_freeze",
    "release",
]
GATE_ADVANCE = {
    "idea_freeze": "method",
    "method_experiment_approval": "experiment_results",
    "claim_freeze": "paper",
    "release": "revision",
}
GATE_REOPEN = {
    "idea_freeze": "idea",
    "method_experiment_approval": "method",
    "claim_freeze": "experiment_results",
}
EXPECTED_TRANSITIONS = {
    "idea": [
        {"to": "literature", "required_gates": []},
        {"to": "method", "required_gates": ["idea_freeze"]},
    ],
    "literature": [
        {"to": "idea", "required_gates": []},
        {"to": "method", "required_gates": ["idea_freeze"]},
    ],
    "method": [
        {"to": "idea", "required_gates": []},
        {"to": "literature", "required_gates": []},
        {
            "to": "experiment_results",
            "required_gates": ["method_experiment_approval"],
        },
    ],
    "experiment_results": [
        {"to": "idea", "required_gates": []},
        {"to": "literature", "required_gates": []},
        {"to": "method", "required_gates": []},
        {"to": "paper", "required_gates": ["claim_freeze"]},
    ],
    "paper": [
        {"to": "literature", "required_gates": []},
        {"to": "method", "required_gates": []},
        {"to": "experiment_results", "required_gates": []},
        {"to": "revision", "required_gates": ["release"]},
    ],
    "revision": [
        {"to": "idea", "required_gates": []},
        {"to": "literature", "required_gates": []},
        {"to": "method", "required_gates": []},
        {"to": "experiment_results", "required_gates": []},
        {"to": "paper", "required_gates": []},
    ],
}

EXPECTED_SKILL_FILES = {
    "SKILL.md",
    "agents/openai.yaml",
    "references/policy.yaml",
    "references/01-idea.md",
    "references/02-literature.md",
    "references/03-method.md",
    "references/04-experiment-results.md",
    "references/05-paper.md",
    "references/06-revision.md",
    "assets/state.template.json",
    "assets/memory.template.md",
}

EXPECTED_UPSTREAM_COMMITS = {
    "claude-scholar": "6fa4540f2ceafeaa5c610532906fec5810ee4e19",
    "evoskills": "29e2c67f12858829ad0900645432b340c3f77522",
    "evoscientist": "49770949daa7ca4ef4744a2f089100f8b872b869",
    "nature-skills": "4170a8a6262642841699c55d468e21ff70a2fe34",
    "agent-research-skills": "9e6c085d65e313e475e921fdfe795ac11eb7589e",
}
VENDOR_ROOTS = {
    "claude-scholar": ROOT / "vendor/claude-scholar",
    "evoskills": ROOT / "vendor/evoskills",
    "nature-skills": ROOT / "vendor/nature-skills",
}


def load_json(path: Path, errors: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path.relative_to(ROOT)}: invalid JSON: {exc}")
        return None


def load_yaml(path: Path, errors: list[str]) -> Any:
    if yaml is None:
        errors.append("PyYAML is required; install requirements-dev.txt")
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        errors.append(f"{path.relative_to(ROOT)}: invalid YAML: {exc}")
        return None


def require_mapping(value: Any, label: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{label}: expected a mapping")
        return {}
    return value


def require_keys(
    value: Any, keys: set[str], label: str, errors: list[str]
) -> dict[str, Any]:
    mapping = require_mapping(value, label, errors)
    missing = keys - set(mapping)
    if missing:
        errors.append(f"{label}: missing {', '.join(sorted(missing))}")
    return mapping


def parse_frontmatter(path: Path, errors: list[str]) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        errors.append(f"{path.relative_to(ROOT)}: missing YAML frontmatter")
        return {}
    try:
        end = lines.index("---", 1)
    except ValueError:
        errors.append(f"{path.relative_to(ROOT)}: unterminated YAML frontmatter")
        return {}
    data: dict[str, str] = {}
    for line in lines[1:end]:
        match = re.fullmatch(r"([A-Za-z0-9_-]+):\s*(.+)", line)
        if not match:
            errors.append(f"{path.relative_to(ROOT)}: unsupported frontmatter {line!r}")
            continue
        data[match.group(1)] = match.group(2).strip()
    return data


def validate_skill() -> list[str]:
    errors: list[str] = []
    skills_root = ROOT / "skills"
    discovered = {
        path.name
        for path in skills_root.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    }
    if discovered != {"research"}:
        errors.append(f"skills: expected only research, found {sorted(discovered)}")

    skill = skills_root / "research"
    for relative in sorted(EXPECTED_SKILL_FILES):
        if not (skill / relative).is_file():
            errors.append(f"skills/research: missing {relative}")
    if errors:
        return errors

    metadata = parse_frontmatter(skill / "SKILL.md", errors)
    if set(metadata) != {"name", "description"}:
        errors.append("skills/research/SKILL.md: frontmatter must contain only name and description")
    if metadata.get("name") != "research":
        errors.append("skills/research/SKILL.md: name must be research")
    if "Use when" not in metadata.get("description", ""):
        errors.append("skills/research/SKILL.md: description must say when to use it")
    skill_text = (skill / "SKILL.md").read_text(encoding="utf-8")
    for relative in sorted(EXPECTED_SKILL_FILES):
        if relative.startswith("references/") and relative not in skill_text:
            errors.append(f"skills/research/SKILL.md: does not route to {relative}")

    agent = load_yaml(skill / "agents/openai.yaml", errors)
    interface = require_keys(
        require_mapping(agent, "agents/openai.yaml", errors).get("interface"),
        {"display_name", "short_description", "default_prompt"},
        "agents/openai.yaml interface",
        errors,
    )
    short = interface.get("short_description")
    if not isinstance(short, str) or not 25 <= len(short) <= 64:
        errors.append("agents/openai.yaml: short_description must be 25-64 characters")
    if "$research" not in str(interface.get("default_prompt", "")):
        errors.append("agents/openai.yaml: default_prompt must mention $research")

    policy = load_json(skill / "references/policy.yaml", errors)
    policy = require_keys(
        policy,
        {
            "schema_version",
            "workflow_version",
            "stage_order",
            "gate_order",
            "state_contract",
            "gates",
            "allowed_transitions",
            "stages",
            "global_prohibited_actions",
            "semantic_audit",
        },
        "policy.yaml",
        errors,
    )
    if policy.get("schema_version") != SCHEMA_VERSION:
        errors.append("policy.yaml: schema_version mismatch")
    if policy.get("workflow_version") != PLUGIN_VERSION:
        errors.append("policy.yaml: workflow_version must match plugin version")
    if policy.get("stage_order") != STAGES:
        errors.append("policy.yaml: stage_order must contain the six canonical stages")
    if policy.get("gate_order") != GATES:
        errors.append("policy.yaml: gate_order must contain the four canonical Gates")
    stages = require_mapping(policy.get("stages"), "policy.yaml stages", errors)
    gates = require_mapping(policy.get("gates"), "policy.yaml gates", errors)
    if set(stages) != set(STAGES):
        errors.append("policy.yaml: stages keys do not match stage_order")
    if set(gates) != set(GATES):
        errors.append("policy.yaml: gates keys do not match gate_order")
    for stage_id, stage in stages.items():
        spec = require_keys(
            stage,
            {
                "label",
                "reference",
                "required_inputs",
                "allowed_actions",
                "required_evidence",
                "exit_criteria",
                "prohibited_actions",
                "gate_to_exit",
            },
            f"policy.yaml stage {stage_id}",
            errors,
        )
        reference = spec.get("reference")
        if not isinstance(reference, str) or not (skill / "references" / reference).is_file():
            errors.append(f"policy.yaml stage {stage_id}: invalid reference {reference!r}")
        for field in (
            "required_inputs",
            "allowed_actions",
            "required_evidence",
            "exit_criteria",
            "prohibited_actions",
        ):
            values = spec.get(field)
            if not isinstance(values, list) or not values or not all(
                isinstance(item, str) and item.strip() for item in values
            ):
                errors.append(
                    f"policy.yaml stage {stage_id}: {field} must be a non-empty string list"
                )
    for gate_id, expected_stage in GATE_ADVANCE.items():
        spec = require_mapping(gates.get(gate_id), f"policy.yaml gate {gate_id}", errors)
        if spec.get("advance_to") != expected_stage:
            errors.append(f"policy.yaml gate {gate_id}: advance_to must be {expected_stage}")
        if gate_id in GATE_REOPEN and spec.get("reopen_to") != GATE_REOPEN[gate_id]:
            errors.append(f"policy.yaml gate {gate_id}: reopen_to contract mismatch")
        for field in ("approval_requires", "reopen_when_changed"):
            values = spec.get(field)
            if not isinstance(values, list) or not values or not all(
                isinstance(item, str) and item.strip() for item in values
            ):
                errors.append(
                    f"policy.yaml gate {gate_id}: {field} must be a non-empty string list"
                )
    if policy.get("allowed_transitions") != EXPECTED_TRANSITIONS:
        errors.append("policy.yaml: allowed_transitions must match the six-stage Gate map")
    audit = policy.get("semantic_audit")
    if not isinstance(audit, list) or len(audit) < 4 or not all(isinstance(x, str) and x for x in audit):
        errors.append("policy.yaml: semantic_audit must contain at least four checks")

    state = load_json(skill / "assets/state.template.json", errors)
    state = require_keys(
        state,
        {
            "schema_version",
            "workflow_version",
            "enabled",
            "project_id",
            "current_stage",
            "gates",
            "artifacts",
            "last_checkpoint",
        },
        "state.template.json",
        errors,
    )
    if state.get("schema_version") != SCHEMA_VERSION:
        errors.append("state.template.json: schema_version mismatch")
    if state.get("workflow_version") != PLUGIN_VERSION:
        errors.append("state.template.json: workflow_version mismatch")
    if state.get("current_stage") != STAGES[0]:
        errors.append("state.template.json: current_stage must start at idea")
    state_gates = require_mapping(state.get("gates"), "state.template.json gates", errors)
    if set(state_gates) != set(GATES):
        errors.append("state.template.json: gate set mismatch")
    for gate_id, gate in state_gates.items():
        record = require_keys(
            gate,
            {"status", "latest_decision_id", "history"},
            f"state.template.json gate {gate_id}",
            errors,
        )
        if record.get("status") != "pending" or record.get("history") != []:
            errors.append(f"state.template.json gate {gate_id}: must start pending with empty history")

    memory = (skill / "assets/memory.template.md").read_text(encoding="utf-8")
    for heading in (
        "Research Kernel",
        "Verified Facts",
        "Decisions and Rationale",
        "Failed Attempts and Lessons",
        "Open Questions",
        "Next Checkpoint",
    ):
        if heading not in memory:
            errors.append(f"memory.template.md: missing section {heading}")
    return errors


def validate_plugin() -> list[str]:
    errors: list[str] = []
    manifest = require_keys(
        load_json(ROOT / ".codex-plugin/plugin.json", errors),
        {"name", "version", "description", "author", "skills", "interface"},
        "plugin.json",
        errors,
    )
    if manifest.get("name") != PLUGIN_NAME:
        errors.append("plugin.json: name mismatch")
    if manifest.get("version") != PLUGIN_VERSION:
        errors.append("plugin.json: version mismatch")
    if manifest.get("skills") != "./skills/":
        errors.append("plugin.json: skills must be ./skills/")
    if "hooks" in manifest:
        errors.append("plugin.json: omit hooks; Codex discovers hooks/hooks.json")

    marketplace = require_keys(
        load_json(ROOT / ".agents/plugins/marketplace.json", errors),
        {"name", "interface", "plugins"},
        "marketplace.json",
        errors,
    )
    entries = marketplace.get("plugins")
    if not isinstance(entries, list) or len(entries) != 1:
        errors.append("marketplace.json: expected one plugin entry")
    else:
        entry = require_keys(
            entries[0],
            {"name", "source", "version", "policy", "category"},
            "marketplace plugin",
            errors,
        )
        if entry.get("name") != PLUGIN_NAME or entry.get("version") != PLUGIN_VERSION:
            errors.append("marketplace.json: name/version must match plugin manifest")
        if entry.get("source") != {"source": "local", "path": "."}:
            errors.append("marketplace.json: this repo-root plugin must use local path .")
        policy = entry.get("policy")
        if policy != {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}:
            errors.append("marketplace.json: installation/authentication policy mismatch")

    hooks = load_json(ROOT / "hooks/hooks.json", errors)
    hook_map = require_mapping(
        require_mapping(hooks, "hooks/hooks.json", errors).get("hooks"),
        "hooks/hooks.json hooks",
        errors,
    )
    expected_events = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}
    if set(hook_map) != expected_events:
        errors.append(f"hooks/hooks.json: expected events {sorted(expected_events)}")
    for event, groups in hook_map.items():
        if not isinstance(groups, list) or not groups:
            errors.append(f"hooks/hooks.json {event}: expected a handler group")
            continue
        handlers = groups[0].get("hooks") if isinstance(groups[0], dict) else None
        if not isinstance(handlers, list) or len(handlers) != 1:
            errors.append(f"hooks/hooks.json {event}: expected one handler")
            continue
        handler = require_keys(
            handlers[0],
            {"type", "command", "commandWindows", "timeout", "statusMessage"},
            f"hooks/hooks.json {event}",
            errors,
        )
        if handler.get("type") != "command":
            errors.append(f"hooks/hooks.json {event}: only command handlers are supported")
        if "research-workflow-hook.js" not in str(handler.get("command")):
            errors.append(f"hooks/hooks.json {event}: command does not call shared hook")
        if "research-workflow-hook.js" not in str(handler.get("commandWindows")):
            errors.append(f"hooks/hooks.json {event}: Windows command does not call shared hook")
    if not (ROOT / "hooks/research-workflow-hook.js").is_file():
        errors.append("hooks: missing research-workflow-hook.js")
    if not (ROOT / "scripts/researchctl.py").is_file():
        errors.append("scripts: missing researchctl.py")

    for legacy in (ROOT / "contracts", ROOT / "profiles", ROOT / "docs"):
        if legacy.exists() and any(legacy.rglob("*")):
            errors.append(f"{legacy.name}: legacy runtime layer must remain removed")
    return errors


def selection_digest(root: Path) -> str:
    digest = hashlib.sha256()
    files = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.name not in {"LICENSE", "UPSTREAM.md"}
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    ]
    for path in sorted(files):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(relative + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def selected_path_contains(selected: str, relative: str) -> bool:
    return relative == selected or relative.startswith(selected.rstrip("/") + "/")


def validate_vendor() -> list[str]:
    errors: list[str] = []
    lock = load_yaml(ROOT / "upstreams.lock.yaml", errors)
    upstreams = lock.get("upstreams") if isinstance(lock, dict) else None
    if not isinstance(upstreams, list):
        return errors + ["upstreams.lock.yaml: expected upstreams list"]
    entries = {
        item.get("id"): item
        for item in upstreams
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if set(entries) != set(EXPECTED_UPSTREAM_COMMITS):
        errors.append("upstreams.lock.yaml: audited upstream set changed")
    for upstream_id, expected_commit in EXPECTED_UPSTREAM_COMMITS.items():
        entry = entries.get(upstream_id, {})
        if entry.get("commit") != expected_commit:
            errors.append(f"upstreams.lock.yaml: {upstream_id} commit mismatch")
    for upstream_id, root in VENDOR_ROOTS.items():
        entry = entries.get(upstream_id, {})
        selected = entry.get("selected")
        if entry.get("integration") != "vendored_verbatim":
            errors.append(f"{upstream_id}: expected vendored_verbatim")
            continue
        if not isinstance(selected, list) or not selected:
            errors.append(f"{upstream_id}: missing selected paths")
            continue
        for relative in selected:
            if not isinstance(relative, str) or not (root / relative).exists():
                errors.append(f"{upstream_id}: selected path missing: {relative}")
        for path in root.rglob("*"):
            if not path.is_file() or path.name in {"LICENSE", "UPSTREAM.md"}:
                continue
            relative = path.relative_to(root).as_posix()
            if not any(selected_path_contains(item, relative) for item in selected):
                errors.append(f"{upstream_id}: unlisted vendored file: {relative}")
        if entry.get("selection_sha256") != selection_digest(root):
            errors.append(f"{upstream_id}: selection SHA-256 mismatch")
    if (ROOT / "vendor/claude-scholar/skills/ml-paper-writing/templates").exists():
        errors.append("Claude Scholar LPPL venue templates must remain excluded")
    for path in (ROOT / "vendor").rglob("*"):
        if path.is_symlink() or path.name == "__pycache__" or path.suffix == ".pyc":
            errors.append(f"vendor: forbidden generated/symlink path {path.relative_to(ROOT)}")
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    if "Copyright 2026 Fusica" not in license_text:
        errors.append("LICENSE: local project owner missing")
    return errors


def main() -> int:
    errors = validate_skill() + validate_plugin() + validate_vendor()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "Validated scientific-research-skill 1.0.0: one Skill, six stages, "
        "four Gates, project-local state, five Hook events, and vendor provenance."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
