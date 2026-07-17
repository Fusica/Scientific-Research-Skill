#!/usr/bin/env python3
"""Validate the compact Scientific Research Skill plugin repository."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from researchctl_core.constants import ResearchCtlError
from researchctl_core.jsonutil import (
    DuplicateJsonKeyError,
    NonStandardJsonConstantError,
    strict_json_loads,
)
from researchctl_core.policy import load_policy as load_runtime_policy

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - reported as a validation error
    yaml = None


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "scientific-research-skill"
HOOK_EVENTS = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}
REQUIRED_CORE_MODULES = {
    "__init__.py",
    "adapters.py",
    "artifacts.py",
    "audit_bundle.py",
    "cli.py",
    "commands.py",
    "constants.py",
    "dashboard.py",
    "doctor.py",
    "gates.py",
    "gate_records.py",
    "gate_validation.py",
    "jsonutil.py",
    "manifest_commands.py",
    "publish.py",
    "policy.py",
    "records.py",
    "runtime_contract.py",
    "state_validation.py",
    "store.py",
    "timeutils.py",
    "trace.py",
    "workspace_validation.py",
}
REQUIRED_PLUGIN_SCRIPTS = {
    "acceptance_evidence.py",
    "capability_acceptance.py",
    "innovation_benchmark.py",
    "reference_stack.py",
    "researchctl.py",
    "validate_acceptance.py",
    "validate_repo.py",
}
REQUIRED_BOUNDARY_DECISIONS = {
    "0005-innovation-protocol-and-evo-acceptance.md",
    "0006-reference-paper-production-contract.md",
    "0007-reference-execution-and-acceptance-boundary.md",
    "0008-project-local-trace-and-offline-audit.md",
}
EXTERNAL_REFERENCE_URLS = (
    "https://github.com/Galaxy-Dawn/claude-scholar",
    "https://github.com/EvoScientist/EvoSkills",
    "https://github.com/Yuan1z0825/nature-skills",
    "https://github.com/lingzhi227/agent-research-skills",
)


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_json(path: Path, errors: list[str]) -> Any:
    try:
        return strict_json_loads(path.read_text(encoding="utf-8"))
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        DuplicateJsonKeyError,
        NonStandardJsonConstantError,
        RecursionError,
    ) as exc:
        errors.append(f"{_display_path(path)}: invalid strict JSON: {exc}")
        return None


def mapping(value: Any, label: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{label}: expected an object")
        return {}
    return value


def unique_strings(value: Any, label: str, errors: list[str]) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item.strip() for item in value)
        or len(value) != len(set(value))
    ):
        errors.append(f"{label}: expected a non-empty unique string list")
        return []
    return value


def artifact_role(value: Any, stages: set[str]) -> bool:
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r"([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)", value)
    return match is not None and match.group(1) in stages


def validate_skill() -> list[str]:
    errors: list[str] = []
    skills_root = ROOT / "skills"
    discovered = {
        item.name
        for item in skills_root.iterdir()
        if item.is_dir() and (item / "SKILL.md").is_file()
    }
    if discovered != {"research"}:
        errors.append(f"skills: expected only research, found {sorted(discovered)}")
    skill = skills_root / "research"
    for relative in (
        "SKILL.md",
        "agents/openai.yaml",
        "assets/memory.template.md",
        "assets/capability-acceptance-corpus.json",
        "assets/capability-evidence-pack.template.json",
        "assets/capability-evidence-result.template.json",
        "assets/capability-provenance-result.template.json",
        "assets/capability-invariant-result.template.json",
        "assets/reference-stack-payload.template.json",
        "assets/runtime-contract.json",
        "assets/state.template.json",
        "references/retrospective-revision-import.md",
        "references/policy.yaml",
    ):
        if not (skill / relative).is_file():
            errors.append(f"skills/research: missing {relative}")
    if errors:
        return errors

    policy = mapping(
        load_json(skill / "references/policy.yaml", errors), "policy.yaml", errors
    )
    runtime_contract = mapping(
        load_json(skill / "assets/runtime-contract.json", errors),
        "runtime-contract.json",
        errors,
    )
    schema_version = policy.get("schema_version")
    workflow_version = policy.get("workflow_version")
    if schema_version != runtime_contract.get("state_schema_version"):
        errors.append(
            "policy.yaml: schema_version must match runtime-contract.json"
        )
    if not isinstance(workflow_version, str) or not workflow_version.strip():
        errors.append("policy.yaml: workflow_version must be non-empty")
    previous_override = os.environ.pop("RESEARCHCTL_POLICY", None)
    previous_runtime_override = os.environ.pop(
        "RESEARCHCTL_RUNTIME_CONTRACT", None
    )
    try:
        try:
            runtime_policy = load_runtime_policy()
        except ResearchCtlError as exc:
            errors.append(f"policy runtime validation failed: {exc}")
            runtime_policy = None
    finally:
        if previous_override is not None:
            os.environ["RESEARCHCTL_POLICY"] = previous_override
        if previous_runtime_override is not None:
            os.environ["RESEARCHCTL_RUNTIME_CONTRACT"] = previous_runtime_override
    graph = mapping(policy.get("workflow_graph"), "policy workflow_graph", errors)
    stage_order = unique_strings(
        graph.get("stage_order"), "policy workflow_graph.stage_order", errors
    )
    gate_order = list(runtime_policy.gate_order) if runtime_policy is not None else []
    stage_set = set(stage_order)
    gate_set = set(gate_order)

    if "state_contract" in policy or "gate_reopen_contract" in policy:
        errors.append(
            "policy: fixed machine schema belongs only in runtime-contract.json"
        )
    if policy.get("artifact_role_cardinality_default") != "one":
        errors.append("policy: artifact_role_cardinality_default must be one")

    layout = mapping(policy.get("artifact_layout"), "policy artifact_layout", errors)
    for field in ("generated_root", "stage_path_template", "snapshot_root", "snapshot_stage_path_template", "instruction"):
        if not isinstance(layout.get(field), str) or not layout[field].strip():
            errors.append(f"policy artifact_layout.{field} must be non-empty")
    for field in ("generated_root", "snapshot_root"):
        value = layout.get(field)
        if isinstance(value, str):
            parts = Path(value).parts
            if not parts or parts[0] != ".research" or ".." in parts:
                errors.append(f"policy artifact_layout.{field} must stay under .research")
    if layout.get("stage_path_template") != f"{layout.get('generated_root')}/<stage-id>":
        errors.append("policy artifact stage template must derive from generated_root")
    if layout.get("snapshot_stage_path_template") != f"{layout.get('snapshot_root')}/<stage-id>":
        errors.append("policy snapshot stage template must derive from snapshot_root")

    stages = mapping(policy.get("stages"), "policy stages", errors)
    if set(stages) != stage_set:
        errors.append("policy stages keys must match stage_order")
    references: list[str] = []
    for stage in stage_order:
        spec = mapping(stages.get(stage), f"policy stage {stage}", errors)
        reference = spec.get("reference")
        if not isinstance(reference, str) or not re.fullmatch(
            r"\d{2}-[a-z0-9-]+\.md", reference
        ):
            errors.append(f"policy stage {stage}: invalid numbered reference")
        else:
            references.append(reference)
            if not (skill / "references" / reference).is_file():
                errors.append(f"policy stage {stage}: missing reference {reference}")
        for field in (
            "required_inputs",
            "allowed_actions",
            "required_evidence",
            "exit_criteria",
            "prohibited_actions",
        ):
            unique_strings(spec.get(field), f"policy stage {stage}.{field}", errors)
        if "gate_to_exit" in spec:
            errors.append(f"policy stage {stage}: gate_to_exit duplicates workflow_graph")
    if len(references) != len(set(references)):
        errors.append("policy stage references must be unique")
    shipped_numbered = {
        item.name
        for item in (skill / "references").glob("[0-9][0-9]-*.md")
        if item.is_file()
    }
    if shipped_numbered != set(references):
        errors.append("numbered stage references must be driven exactly by policy stages")

    gates = mapping(policy.get("gates"), "policy gates", errors)
    if set(gates) != gate_set:
        errors.append("policy gates keys must match gate_order")
    for gate in gate_order:
        spec = mapping(gates.get(gate), f"policy Gate {gate}", errors)
        for field in (
            "requires_gates",
            "required_for",
            "advance_to",
            "reopen_to",
            "required_stage",
            "release_targets",
            "required_artifact_roles_by_target",
            "retrospective_revision_import",
        ):
            if field in spec:
                errors.append(f"policy Gate {gate}: obsolete relation field {field}")
        role_lists: list[Any]
        contracts = spec.get("approval_targets") or spec.get("approval_modes")
        if contracts is not None:
            contracts = mapping(contracts, f"policy Gate {gate} contracts", errors)
            role_lists = [
                mapping(value, f"policy Gate {gate} contract {name}", errors).get(
                    "required_artifact_roles"
                )
                for name, value in contracts.items()
            ]
        else:
            role_lists = [spec.get("required_artifact_roles")]
        flattened: list[str] = []
        for roles in role_lists:
            values = unique_strings(roles, f"policy Gate {gate} artifact roles", errors)
            if any(not artifact_role(role, stage_set) for role in values):
                errors.append(f"policy Gate {gate}: invalid artifact role")
            flattened.extend(values)
        selection = spec.get("selection_artifact_role")
        if selection is not None and selection not in flattened:
            errors.append(f"policy Gate {gate}: selection role must be required")

    transitions = mapping(graph.get("stage_transitions"), "policy transitions", errors)
    if set(transitions) != stage_set:
        errors.append("policy transitions keys must match stage_order")
    for source, candidates in transitions.items():
        if not isinstance(candidates, list):
            errors.append(f"policy transitions.{source} must be a list")
            continue
        targets: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, dict) or set(candidate) != {"to", "trigger"}:
                errors.append(f"policy transition from {source} has invalid fields")
                continue
            target = candidate.get("to")
            if target not in stage_set or target in targets:
                errors.append(f"policy transition from {source} has invalid target")
            targets.add(target)

    skill_text = (skill / "SKILL.md").read_text(encoding="utf-8")
    if not skill_text.startswith("---\n") or "\nname: research\n" not in skill_text:
        errors.append("SKILL.md: invalid research frontmatter")
    if "references/policy.yaml" not in skill_text:
        errors.append("SKILL.md: does not route through references/policy.yaml")
    if "policy.stages[current_stage].reference" not in skill_text:
        errors.append("SKILL.md: stage router must derive one reference from policy")
    if "references/retrospective-revision-import.md" not in skill_text:
        errors.append("SKILL.md: missing conditional retrospective reference")
    if ".research/state.json` with `enabled: true" not in skill_text:
        errors.append("SKILL.md: description must enforce the activation boundary")

    state = mapping(load_json(skill / "assets/state.template.json", errors), "state template", errors)
    runtime_state = mapping(
        runtime_contract.get("state"), "runtime contract state", errors
    )
    required_state_fields = runtime_state.get("required_fields")
    if isinstance(required_state_fields, list) and set(state) != set(required_state_fields):
        errors.append(
            "state template fields must match runtime contract state.required_fields"
        )
    if state.get("schema_version") != schema_version or state.get("workflow_version") != workflow_version:
        errors.append("state template versions must match policy")
    if stage_order and state.get("current_stage") != stage_order[0]:
        errors.append("state template must start at the first policy stage")
    state_gates = mapping(state.get("gates"), "state template gates", errors)
    if set(state_gates) != gate_set:
        errors.append("state template Gates must match policy gate_order")
    runtime_gate = mapping(
        runtime_contract.get("gate"), "runtime contract gate", errors
    )
    record_fields = runtime_gate.get("record_fields")
    if not isinstance(record_fields, list):
        record_fields = []
    pending_values = {
        "status": "pending",
        "latest_decision_id": None,
        "history": [],
    }
    pending_record = {
        field: pending_values.get(field) for field in record_fields
    }
    target_fields = runtime_gate.get("target_container_fields")
    target_field = (
        target_fields[0]
        if isinstance(target_fields, list) and len(target_fields) == 1
        else "<invalid-target-container>"
    )
    for gate, record in state_gates.items():
        targets = (
            runtime_policy.gate_sequence if runtime_policy is not None else ()
        )
        gate_targets = [target for candidate, target in targets if candidate == gate and target]
        expected = (
            {target_field: {target: pending_record for target in gate_targets}}
            if gate_targets
            else pending_record
        )
        if record != expected:
            errors.append(f"state template Gate {gate} does not match its target shape")
    if state.get("artifacts") != {} or state.get("stage_history") != []:
        errors.append("state template artifact and stage histories must start empty")

    if yaml is None:
        errors.append("PyYAML is required; install requirements-dev.txt")
    else:
        try:
            agent = yaml.safe_load(
                (skill / "agents/openai.yaml").read_text(encoding="utf-8")
            )
        except (OSError, yaml.YAMLError) as exc:
            errors.append(f"agents/openai.yaml: invalid YAML: {exc}")
        else:
            interface = mapping(mapping(agent, "agents/openai.yaml", errors).get("interface"), "agents interface", errors)
            if "$research" not in str(interface.get("default_prompt", "")):
                errors.append("agents/openai.yaml: default_prompt must mention $research")
    return errors


def validate_plugin() -> list[str]:
    errors: list[str] = []
    manifest = mapping(load_json(ROOT / ".codex-plugin/plugin.json", errors), "plugin.json", errors)
    if manifest.get("name") != PLUGIN_NAME:
        errors.append("plugin.json: name mismatch")
    version = manifest.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        errors.append("plugin.json: version must be semver")
    if manifest.get("skills") != "./skills/" or "hooks" in manifest:
        errors.append("plugin.json: use ./skills/ and let Codex discover hooks/hooks.json")

    marketplace = mapping(load_json(ROOT / ".agents/plugins/marketplace.json", errors), "marketplace.json", errors)
    entries = marketplace.get("plugins")
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        errors.append("marketplace.json: expected one plugin entry")
    else:
        entry = entries[0]
        if entry.get("name") != manifest.get("name") or entry.get("version") != version:
            errors.append("marketplace.json: name/version must match plugin manifest")
        if entry.get("source") != {"source": "local", "path": "."}:
            errors.append("marketplace.json: source must be this repository")

    hooks = mapping(
        mapping(load_json(ROOT / "hooks/hooks.json", errors), "hooks.json", errors).get("hooks"),
        "hooks.json hooks",
        errors,
    )
    if set(hooks) != HOOK_EVENTS:
        errors.append("hooks.json: event set mismatch")
    for event, groups in hooks.items():
        handlers = groups[0].get("hooks") if isinstance(groups, list) and groups and isinstance(groups[0], dict) else None
        if not isinstance(handlers, list) or len(handlers) != 1 or not isinstance(handlers[0], dict):
            errors.append(f"hooks.json: {event} must have one command handler")
            continue
        handler = handlers[0]
        if handler.get("type") != "command" or "research-workflow-hook.js" not in str(handler.get("command")):
            errors.append(f"hooks.json: {event} handler is invalid")

    modules = {item.name for item in (ROOT / "scripts/researchctl_core").glob("*.py")}
    missing = REQUIRED_CORE_MODULES - modules
    if missing:
        errors.append(f"researchctl_core: missing {', '.join(sorted(missing))}")
    if "migration.py" in modules:
        errors.append("researchctl_core: v2 must not ship an automatic migration module")
    scripts = {
        item.name for item in (ROOT / "scripts").glob("*.py") if item.is_file()
    }
    missing_scripts = REQUIRED_PLUGIN_SCRIPTS - scripts
    if missing_scripts:
        errors.append(
            "scripts: missing plugin entry points "
            + ", ".join(sorted(missing_scripts))
        )
    decisions = {
        item.name for item in (ROOT / "decisions").glob("*.md") if item.is_file()
    }
    missing_decisions = REQUIRED_BOUNDARY_DECISIONS - decisions
    if missing_decisions:
        errors.append(
            "decisions: missing capability boundary ADRs "
            + ", ".join(sorted(missing_decisions))
        )
    for legacy in (ROOT / "contracts", ROOT / "profiles"):
        if legacy.exists() and any(legacy.rglob("*")):
            errors.append(f"{legacy.name}: legacy runtime layer must remain removed")
    expected_agent_docs = {
        Path("docs/agents/domain.md"),
        Path("docs/agents/issue-tracker.md"),
        Path("docs/agents/triage-labels.md"),
    }
    actual_docs = {
        path.relative_to(ROOT)
        for path in (ROOT / "docs").rglob("*")
        if path.is_file()
    }
    if actual_docs != expected_agent_docs:
        missing = sorted(str(path) for path in expected_agent_docs - actual_docs)
        unexpected = sorted(str(path) for path in actual_docs - expected_agent_docs)
        if missing:
            errors.append(f"docs/agents: missing maintainer config {', '.join(missing)}")
        if unexpected:
            errors.append(
                "docs: only maintainer agent config is allowed; unexpected "
                + ", ".join(unexpected)
            )
    for stale in ("vendor", "THIRD_PARTY_NOTICES.md", "upstreams.lock.yaml"):
        if (ROOT / stale).exists():
            errors.append(f"{stale}: external references must remain link-only")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for url in EXTERNAL_REFERENCE_URLS:
        if url not in readme:
            errors.append(f"README.md: missing external reference {url}")
    if "Copyright 2026 Fusica" not in (ROOT / "LICENSE").read_text(encoding="utf-8"):
        errors.append("LICENSE: local project owner missing")
    return errors


def main() -> int:
    errors = validate_skill() + validate_plugin()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    policy = json.loads(
        (ROOT / "skills/research/references/policy.yaml").read_text(encoding="utf-8")
    )
    manifest = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    stage_order = policy["workflow_graph"]["stage_order"]
    gate_count = len(
        {
            requirement["gate"]
            for requirement in policy["workflow_graph"]["stage_exit_requirements"].values()
            if isinstance(requirement, dict)
        }
    )
    print(
        f"Validated scientific-research-skill {manifest['version']} "
        f"(workflow {policy['workflow_version']}): one Skill, "
        f"{len(stage_order)} stages, {gate_count} Gates, "
        "v2 revision snapshots, project-local trace, offline audit, "
        "and five Hook events."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
