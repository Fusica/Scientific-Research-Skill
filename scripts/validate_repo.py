#!/usr/bin/env python3
"""Validate skills, scientific artifact contracts, and vendor provenance."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:
    yaml = None


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
VENDOR = ROOT / "vendor"

EXPECTED_SKILLS = {
    "research-orchestrator",
    "idea-evolution",
    "literature-evidence",
    "method-formalization",
    "experiment-lifecycle",
    "result-synthesis",
    "paper-production",
    "review-revision",
}

EXPECTED_ARTIFACT_ROLES = {
    "project_state",
    "idea_card",
    "search_protocol",
    "paper_registry",
    "evidence_matrix",
    "closest_work",
    "method_contract",
    "experiment_matrix",
    "run_registry",
    "experiment_decision_log",
    "analysis_registry",
    "publication_artifact_manifest",
    "claim_ledger",
    "paper_claim_map",
    "paper_change_map",
    "review_map",
    "revision_change_log",
}

EXPECTED_UPSTREAM_COMMITS = {
    "claude-scholar": "6fa4540f2ceafeaa5c610532906fec5810ee4e19",
    "evoskills": "29e2c67f12858829ad0900645432b340c3f77522",
    "evoscientist": "49770949daa7ca4ef4744a2f089100f8b872b869",
    "nature-skills": "4170a8a6262642841699c55d468e21ff70a2fe34",
    "agent-research-skills": "9e6c085d65e313e475e921fdfe795ac11eb7589e",
}

VENDOR_ROOTS = {
    "claude-scholar": VENDOR / "claude-scholar",
    "evoskills": VENDOR / "evoskills",
    "nature-skills": VENDOR / "nature-skills",
}

EXPECTED_VENDOR_PATHS = {
    "vendor/claude-scholar/LICENSE",
    "vendor/claude-scholar/UPSTREAM.md",
    "vendor/claude-scholar/skills/research-ideation/SKILL.md",
    "vendor/claude-scholar/skills/results-analysis/SKILL.md",
    "vendor/claude-scholar/skills/results-report/SKILL.md",
    "vendor/claude-scholar/skills/ml-paper-writing/SKILL.md",
    "vendor/claude-scholar/skills/publication-chart-skill/SKILL.md",
    "vendor/evoskills/LICENSE",
    "vendor/evoskills/UPSTREAM.md",
    "vendor/evoskills/skills/research-ideation/SKILL.md",
    "vendor/evoskills/skills/paper-navigator/SKILL.md",
    "vendor/evoskills/skills/paper-planning/SKILL.md",
    "vendor/evoskills/skills/experiment-pipeline/SKILL.md",
    "vendor/evoskills/skills/experiment-craft/SKILL.md",
    "vendor/evoskills/skills/experiment-iterative-coder/SKILL.md",
    "vendor/evoskills/skills/evo-memory/SKILL.md",
    "vendor/evoscientist/NOTICE.md",
    "vendor/nature-skills/LICENSE",
    "vendor/nature-skills/UPSTREAM.md",
    "vendor/nature-skills/skills/_shared",
    "vendor/nature-skills/skills/nature-writing/SKILL.md",
    "vendor/nature-skills/skills/nature-response/SKILL.md",
    "vendor/nature-skills/skills/nature-statistics/SKILL.md",
    "vendor/agent-research-skills/NOTICE.md",
}


def load_yaml(path: Path, errors: list[str]) -> Any:
    if yaml is None:
        errors.append(
            "PyYAML is required for semantic validation; install requirements-dev.txt"
        )
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        errors.append(f"{path}: invalid YAML: {exc}")
        return None


def load_json(path: Path, errors: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: invalid JSON: {exc}")
        return None


def parse_frontmatter(path: Path) -> tuple[dict[str, str], list[str]]:
    errors: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        return {}, [f"{path}: missing opening frontmatter delimiter"]
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, [f"{path}: missing closing frontmatter delimiter"]

    data: dict[str, str] = {}
    for line in lines[1:end]:
        match = re.fullmatch(r"([A-Za-z0-9_-]+):\s*(.+)", line)
        if not match:
            errors.append(f"{path}: unsupported frontmatter line: {line!r}")
            continue
        data[match.group(1)] = match.group(2).strip()
    return data, errors


def require_keys(
    value: Any, required: set[str], label: str, errors: list[str]
) -> None:
    if not isinstance(value, dict):
        errors.append(f"{label}: expected a mapping")
        return
    missing = required - set(value)
    if missing:
        errors.append(f"{label}: missing keys {', '.join(sorted(missing))}")


def mapping_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(str(key) for key in value)
        for child in value.values():
            keys.update(mapping_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(mapping_keys(child))
    return keys


def validate_skill(path: Path) -> list[str]:
    errors: list[str] = []
    skill_md = path / "SKILL.md"
    openai_yaml = path / "agents" / "openai.yaml"

    if not skill_md.is_file():
        return [f"{path}: missing SKILL.md"]

    frontmatter, fm_errors = parse_frontmatter(skill_md)
    errors.extend(fm_errors)
    if set(frontmatter) != {"name", "description"}:
        errors.append(
            f"{skill_md}: frontmatter keys must be exactly name and description"
        )
    if frontmatter.get("name") != path.name:
        errors.append(f"{skill_md}: name does not match directory")
    if "Use when" not in frontmatter.get("description", ""):
        errors.append(f"{skill_md}: description must state when the skill applies")

    text = skill_md.read_text(encoding="utf-8")
    if "TODO" in text:
        errors.append(f"{skill_md}: unresolved TODO")
    for reference in re.findall(r"references/[A-Za-z0-9._/-]+", text):
        if not (path / reference).is_file():
            errors.append(f"{skill_md}: missing referenced file {reference}")

    if not openai_yaml.is_file():
        errors.append(f"{path}: missing agents/openai.yaml")
    else:
        metadata = load_yaml(openai_yaml, errors)
        interface = metadata.get("interface") if isinstance(metadata, dict) else None
        require_keys(
            interface,
            {"display_name", "short_description", "default_prompt"},
            str(openai_yaml),
            errors,
        )
        if isinstance(interface, dict):
            short = interface.get("short_description", "")
            if not isinstance(short, str) or not 25 <= len(short) <= 64:
                errors.append(
                    f"{openai_yaml}: short_description must contain 25-64 characters"
                )
            prompt = interface.get("default_prompt", "")
            if "$" + path.name not in prompt:
                errors.append(
                    f"{openai_yaml}: default_prompt must explicitly mention "
                    f"{'$' + path.name}"
                )
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


def validate_vendor(lock: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(lock, dict) or not isinstance(lock.get("upstreams"), list):
        return ["upstreams.lock.yaml: expected an upstreams list"]

    entries = {
        entry.get("id"): entry
        for entry in lock["upstreams"]
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    if set(entries) != set(EXPECTED_UPSTREAM_COMMITS):
        errors.append("upstreams.lock.yaml: upstream IDs do not match the audited set")

    for upstream_id, expected_commit in EXPECTED_UPSTREAM_COMMITS.items():
        entry = entries.get(upstream_id, {})
        if entry.get("commit") != expected_commit:
            errors.append(
                f"upstreams.lock.yaml: {upstream_id} commit does not match audit"
            )

    for upstream_id, root in VENDOR_ROOTS.items():
        entry = entries.get(upstream_id, {})
        selected = entry.get("selected")
        if entry.get("integration") != "vendored_verbatim":
            errors.append(f"{upstream_id}: expected vendored_verbatim integration")
            continue
        if not isinstance(selected, list) or not selected:
            errors.append(f"{upstream_id}: selected paths must be a non-empty list")
            continue

        for relative in selected:
            if not isinstance(relative, str) or not (root / relative).exists():
                errors.append(f"{upstream_id}: selected path is missing: {relative}")

        metadata_names = {"LICENSE", "UPSTREAM.md"}
        for path in root.rglob("*"):
            if not path.is_file() or path.name in metadata_names:
                continue
            relative = path.relative_to(root).as_posix()
            if not any(selected_path_contains(item, relative) for item in selected):
                errors.append(
                    f"{upstream_id}: unlisted vendored file outside selection: {relative}"
                )

        actual_digest = selection_digest(root)
        if entry.get("selection_sha256") != actual_digest:
            errors.append(
                f"{upstream_id}: selection SHA-256 mismatch "
                f"(expected {entry.get('selection_sha256')}, got {actual_digest})"
            )

    for relative in sorted(EXPECTED_VENDOR_PATHS):
        if not (ROOT / relative).exists():
            errors.append(f"missing vendor provenance or selection: {relative}")

    excluded_templates = (
        VENDOR / "claude-scholar/skills/ml-paper-writing/templates"
    )
    if excluded_templates.exists():
        errors.append(
            "Claude Scholar venue templates must remain excluded because they "
            "contain independent redistribution terms"
        )

    for path in VENDOR.rglob("*"):
        if path.is_symlink():
            errors.append(f"vendor symlinks are not allowed: {path}")
        if path.is_dir() and path.name == "__pycache__":
            errors.append(f"vendor cache directory must not be packaged: {path}")
        if path.is_file() and path.suffix == ".pyc":
            errors.append(f"vendor bytecode must not be packaged: {path}")
        if path.is_file() and path.name not in {"UPSTREAM.md", "NOTICE.md"}:
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if "LaTeX Project Public License" in text or "natbib.dtx" in text:
                errors.append(f"untracked nested LPPL material in vendor file: {path}")

    root_license = (ROOT / "LICENSE").read_text(encoding="utf-8")
    vendor_license = (VENDOR / "evoskills/LICENSE").read_text(encoding="utf-8")
    if root_license == vendor_license:
        errors.append("root LICENSE must not retain the EvoScientist copyright notice")
    if "Copyright 2026 Fusica" not in root_license:
        errors.append("root LICENSE must identify the local project copyright holder")

    return errors


def validate_contracts() -> list[str]:
    errors: list[str] = []

    for path in sorted((ROOT / "contracts").glob("*.yaml")):
        load_yaml(path, errors)
    for path in sorted((ROOT / "profiles").rglob("*.yaml")):
        load_yaml(path, errors)
    for path in sorted((ROOT / ".github").rglob("*.yml")):
        load_yaml(path, errors)
    for path in sorted((ROOT / "contracts").glob("*.json")):
        load_json(path, errors)

    catalog_path = ROOT / "contracts/artifact-catalog.yaml"
    catalog = load_yaml(catalog_path, errors)
    if isinstance(catalog, dict):
        if catalog.get("gate_authority") != ".research/project-state.yaml":
            errors.append(f"{catalog_path}: incorrect gate authority")
        records = catalog.get("artifacts")
        if not isinstance(records, list):
            errors.append(f"{catalog_path}: artifacts must be a list")
        else:
            roles = [record.get("role") for record in records if isinstance(record, dict)]
            paths = [
                record.get("canonical_path")
                for record in records
                if isinstance(record, dict)
            ]
            if set(roles) != EXPECTED_ARTIFACT_ROLES or len(roles) != len(set(roles)):
                errors.append(f"{catalog_path}: artifact roles are missing or duplicated")
            if len(paths) != len(set(paths)):
                errors.append(f"{catalog_path}: canonical paths must be unique")
            for record in records:
                require_keys(
                    record,
                    {"role", "canonical_path", "producer"},
                    f"{catalog_path} artifact",
                    errors,
                )
                if not isinstance(record, dict):
                    continue
                canonical = record.get("canonical_path")
                if not isinstance(canonical, str) or not canonical.startswith(".research/"):
                    errors.append(f"{catalog_path}: invalid canonical path {canonical!r}")
                template = record.get("template") or record.get("record_template")
                if template is not None and not (ROOT / template).is_file():
                    errors.append(f"{catalog_path}: missing template {template}")

    project_state = load_yaml(ROOT / "contracts/project-state.template.yaml", errors)
    if isinstance(project_state, dict):
        if set(project_state.get("gates", {})) != {
            "idea_freeze",
            "method_experiment",
            "claim_freeze",
            "external_release",
        }:
            errors.append("project-state: gate set is incomplete")
        if not isinstance(project_state.get("gate_decisions"), list):
            errors.append("project-state: gate_decisions must be a list")
        decision = load_yaml(
            ROOT / "contracts/gate-decision-record.template.yaml", errors
        )
        require_keys(
            decision,
            {
                "decision_id",
                "gate",
                "action",
                "release_target",
                "decided_by",
                "decided_at",
                "based_on_artifacts",
                "reason",
                "reopened_artifacts",
                "impacted_artifact_ids",
            },
            "project-state gate decision",
            errors,
        )
        if isinstance(decision, dict):
            based_on = decision.get("based_on_artifacts")
            based_artifact = (
                based_on[0] if isinstance(based_on, list) and based_on else None
            )
            require_keys(
                based_artifact,
                {"artifact_id", "artifact_version", "content_hash"},
                "gate decision artifact binding",
                errors,
            )
        registry = project_state.get("artifact_registry")
        artifact = registry[0] if isinstance(registry, list) and registry else None
        require_keys(
            artifact,
            {
                "artifact_id",
                "role",
                "path",
                "schema_version",
                "artifact_version",
                "content_hash",
                "status",
            },
            "project-state artifact registry",
            errors,
        )

    idea = load_yaml(ROOT / "contracts/idea-card.template.yaml", errors)
    require_keys(
        idea,
        {
            "artifact_id",
            "artifact_version",
            "content_hash",
            "idea_id",
            "idea_version",
            "gate_ref",
            "claim_candidates",
            "predictions",
        },
        "idea card",
        errors,
    )
    if isinstance(idea, dict):
        claim_candidates = idea.get("claim_candidates")
        idea_claim_ids = {
            item.get("claim_candidate_id")
            for item in claim_candidates
            if isinstance(item, dict)
        } if isinstance(claim_candidates, list) else set()
        predictions = idea.get("predictions")
        prediction = (
            predictions[0] if isinstance(predictions, list) and predictions else None
        )
        require_keys(
            prediction,
            {
                "prediction_id",
                "claim_candidate_ids",
                "observable",
                "falsifying_outcome",
                "baseline_or_intervention",
                "boundary_conditions",
            },
            "idea prediction",
            errors,
        )
        idea_prediction_ids = {
            item.get("prediction_id")
            for item in predictions
            if isinstance(item, dict)
        } if isinstance(predictions, list) else set()
    else:
        idea_claim_ids = set()
        idea_prediction_ids = set()

    experiment = load_yaml(ROOT / "contracts/experiment-matrix.template.yaml", errors)
    if isinstance(experiment, dict):
        require_keys(
            experiment,
            {
                "artifact_id",
                "artifact_version",
                "content_hash",
                "idea_ref",
                "method_contract_ref",
                "gate_ref",
                "experiments",
            },
            "experiment matrix",
            errors,
        )
        require_keys(
            experiment.get("method_contract_ref"),
            {"artifact_id", "artifact_version", "content_hash"},
            "experiment method contract reference",
            errors,
        )
        rows = experiment.get("experiments")
        row = rows[0] if isinstance(rows, list) and rows else None
        require_keys(
            row,
            {
                "experiment_id",
                "spec_version",
                "spec_hash",
                "origin_claim_candidate_ids",
                "prediction_ids",
                "statistical_unit",
                "analysis_plan",
            },
            "experiment row",
            errors,
        )
        if isinstance(row, dict):
            if not set(row.get("prediction_ids", [])) <= idea_prediction_ids:
                errors.append(
                    "experiment row: prediction_ids must resolve to the idea card"
                )
            if not set(row.get("origin_claim_candidate_ids", [])) <= idea_claim_ids:
                errors.append(
                    "experiment row: origin claim IDs must resolve to the idea card"
                )
            require_keys(
                row.get("analysis_plan"),
                {
                    "plan_id",
                    "version",
                    "content_hash",
                    "primary_estimand",
                    "inclusion_criteria",
                    "exclusion_criteria",
                    "method",
                    "uncertainty",
                    "multiplicity_family",
                    "sequential_stopping",
                },
                "experiment analysis plan",
                errors,
            )
    else:
        row = None

    run = load_json(ROOT / "contracts/run-record.template.json", errors)
    require_keys(
        run,
        {
            "run_id",
            "experiment_id",
            "experiment_spec_version",
            "experiment_spec_hash",
            "method_contract_ref",
            "execution_status",
            "scientific_outcome",
            "code",
            "config",
            "profile",
            "randomization_record",
            "data_or_environment",
            "runtime",
            "outputs",
            "failure",
            "analysis_eligibility",
        },
        "run record",
        errors,
    )
    if isinstance(run, dict):
        require_keys(
            run.get("method_contract_ref"),
            {"artifact_id", "artifact_version", "content_hash"},
            "run method contract reference",
            errors,
        )
        require_keys(
            run.get("code"),
            {
                "repository",
                "commit",
                "dirty",
                "dirty_patch_artifact_id",
                "dirty_patch_hash",
                "command",
                "arguments",
                "cwd",
            },
            "run code provenance",
            errors,
        )
        require_keys(
            run.get("profile"),
            {"id", "version", "content_hash"},
            "run profile provenance",
            errors,
        )
        outputs = run.get("outputs")
        output = outputs[0] if isinstance(outputs, list) and outputs else None
        require_keys(
            output,
            {"artifact_id", "path", "sha256", "media_type"},
            "run output artifact",
            errors,
        )
        require_keys(
            run.get("failure"),
            {
                "category",
                "symptoms",
                "minimal_reproduction_artifact_id",
                "root_cause",
                "changed_factor",
                "resolution",
            },
            "run failure diagnosis",
            errors,
        )
        require_keys(
            run.get("analysis_eligibility"),
            {"included", "reason"},
            "run analysis eligibility",
            errors,
        )
        if isinstance(row, dict):
            if run.get("experiment_id") != row.get("experiment_id"):
                errors.append("run record: experiment_id does not resolve")
            if run.get("experiment_spec_version") != row.get("spec_version"):
                errors.append("run record: experiment spec version does not match")
            if run.get("experiment_spec_hash") != row.get("spec_hash"):
                errors.append("run record: experiment spec hash does not match")
        if isinstance(experiment, dict):
            if run.get("method_contract_ref") != experiment.get(
                "method_contract_ref"
            ):
                errors.append("run record: method contract reference does not match")
        require_keys(
            run.get("config"),
            {"source_path", "resolved_artifact_id", "resolved_hash", "seed"},
            "run resolved configuration",
            errors,
        )
        require_keys(
            run.get("data_or_environment"),
            {"id", "version", "split_or_scenario", "content_hash"},
            "run data/environment provenance",
            errors,
        )
        require_keys(
            run.get("runtime"),
            {
                "hardware",
                "operating_system",
                "software_environment_artifact_id",
                "software_environment_hash",
                "container_digest",
            },
            "run runtime provenance",
            errors,
        )

    analysis = load_yaml(ROOT / "contracts/analysis-record.template.yaml", errors)
    require_keys(
        analysis,
        {
            "analysis_id",
            "analysis_version",
            "analysis_plan_ref",
            "experiment_ids",
            "origin_claim_candidate_ids",
            "prediction_ids",
            "run_population",
            "code",
            "config",
            "statistical_unit",
            "estimand",
            "outputs",
        },
        "analysis record",
        errors,
    )
    if isinstance(analysis, dict):
        require_keys(
            analysis.get("analysis_plan_ref"),
            {"plan_id", "version", "content_hash"},
            "analysis plan reference",
            errors,
        )
        if isinstance(row, dict):
            plan = row.get("analysis_plan")
            if isinstance(plan, dict):
                expected_plan_ref = {
                    key: plan.get(key)
                    for key in ("plan_id", "version", "content_hash")
                }
                if analysis.get("analysis_plan_ref") != expected_plan_ref:
                    errors.append(
                        "analysis record: analysis plan reference does not match"
                    )
        population = analysis.get("run_population")
        require_keys(
            population,
            {"included_run_ids", "excluded"},
            "analysis run population",
            errors,
        )
        if isinstance(population, dict):
            excluded = population.get("excluded")
            excluded_record = (
                excluded[0] if isinstance(excluded, list) and excluded else None
            )
            require_keys(
                excluded_record,
                {"run_id", "reason"},
                "analysis excluded-run record",
                errors,
            )
        require_keys(
            analysis.get("code"),
            {"repository", "commit", "dirty", "dirty_patch_hash", "entrypoint"},
            "analysis code provenance",
            errors,
        )
        require_keys(
            analysis.get("config"),
            {"artifact_id", "content_hash"},
            "analysis configuration",
            errors,
        )
        require_keys(
            analysis.get("uncertainty"),
            {"method", "level", "interval"},
            "analysis uncertainty",
            errors,
        )
        require_keys(
            analysis.get("outputs"),
            {"artifact_ids"},
            "analysis outputs",
            errors,
        )
        if not set(analysis.get("prediction_ids", [])) <= idea_prediction_ids:
            errors.append(
                "analysis record: prediction_ids must resolve to the idea card"
            )
        if not set(analysis.get("origin_claim_candidate_ids", [])) <= idea_claim_ids:
            errors.append(
                "analysis record: origin claim IDs must resolve to the idea card"
            )

    experiment_decision = load_yaml(
        ROOT / "contracts/experiment-decision-record.template.yaml", errors
    )
    require_keys(
        experiment_decision,
        {
            "decision_id",
            "decided_at",
            "trigger",
            "diagnosis",
            "controlled_change",
            "outcome",
            "next_action",
            "reopens",
            "impacted_artifact_ids",
        },
        "experiment decision record",
        errors,
    )
    if isinstance(experiment_decision, dict):
        require_keys(
            experiment_decision.get("trigger"),
            {"run_ids", "evidence_artifact_ids"},
            "experiment decision trigger",
            errors,
        )
        require_keys(
            experiment_decision.get("diagnosis"),
            {
                "category",
                "symptoms",
                "minimal_reproduction_artifact_id",
                "root_cause",
            },
            "experiment decision diagnosis",
            errors,
        )
        require_keys(
            experiment_decision.get("controlled_change"),
            {"factor", "from", "to"},
            "experiment controlled change",
            errors,
        )

    manifest = load_yaml(
        ROOT / "contracts/artifact-manifest-record.template.yaml", errors
    )
    require_keys(
        manifest,
        {
            "artifact_id",
            "artifact_type",
            "path",
            "sha256",
            "media_type",
            "created_by_analysis_id",
            "source_run_ids",
            "source_artifact_ids",
            "generation",
            "publication_metadata",
            "status",
        },
        "publication artifact manifest",
        errors,
    )
    if isinstance(manifest, dict):
        require_keys(
            manifest.get("generation"),
            {"code_commit", "entrypoint", "config_hash"},
            "artifact generation provenance",
            errors,
        )

    claim_ledger = load_yaml(ROOT / "contracts/claim-ledger.template.yaml", errors)
    if isinstance(claim_ledger, dict):
        if claim_ledger.get("gate_ref") != "claim_freeze":
            errors.append("claim ledger: gate_ref must be claim_freeze")
        claims = claim_ledger.get("claims")
        claim = claims[0] if isinstance(claims, list) and claims else None
        require_keys(
            claim,
            {
                "claim_id",
                "claim_version",
                "origin_claim_candidate_ids",
                "prediction_ids",
                "experiment_ids",
                "status",
                "evidence",
                "allowed_wording",
                "forbidden_stronger_wording",
            },
            "claim ledger entry",
            errors,
        )
        if isinstance(claim, dict) and claim.get("status") != "unassessed":
            errors.append("claim ledger: new claims must default to unassessed")
        if isinstance(claim, dict):
            if not set(claim.get("prediction_ids", [])) <= idea_prediction_ids:
                errors.append(
                    "claim ledger: prediction_ids must resolve to the idea card"
                )
            if not set(claim.get("origin_claim_candidate_ids", [])) <= idea_claim_ids:
                errors.append(
                    "claim ledger: origin claim IDs must resolve to the idea card"
                )
            require_keys(
                claim.get("evidence"),
                {
                    "literature_evidence_ids",
                    "run_ids",
                    "analysis_ids",
                    "artifact_ids",
                },
                "claim evidence",
                errors,
            )

    for label, artifact in (
        ("idea card", idea),
        ("experiment matrix", experiment),
        ("claim ledger", claim_ledger),
    ):
        forbidden = {"approval", "human_approval"} & mapping_keys(artifact)
        if forbidden:
            errors.append(
                f"{label}: gate approval belongs only in project state "
                f"(found {', '.join(sorted(forbidden))})"
            )

    paper_claim = load_yaml(ROOT / "contracts/paper-claim-map.template.yaml", errors)
    paper_change = load_yaml(ROOT / "contracts/paper-change-map.template.yaml", errors)
    if isinstance(paper_claim, dict):
        placements = paper_claim.get("claim_placements")
        placement = (
            placements[0] if isinstance(placements, list) and placements else None
        )
        require_keys(
            placement,
            {"paper_claim_id", "claim_id", "claim_version", "manuscript_locations"},
            "paper claim placement",
            errors,
        )
        paper_claim_ids = {
            item.get("paper_claim_id")
            for item in placements
            if isinstance(item, dict)
        } if isinstance(placements, list) else set()
    else:
        paper_claim_ids = set()
    if isinstance(paper_change, dict):
        changes = paper_change.get("changes")
        change = changes[0] if isinstance(changes, list) and changes else None
        require_keys(
            change,
            {"change_id", "paper_claim_ids", "claim_ids", "file", "verification"},
            "paper change",
            errors,
        )
        if isinstance(change, dict) and not set(
            change.get("paper_claim_ids", [])
        ) <= paper_claim_ids:
            errors.append(
                "paper change: paper_claim_ids must resolve to paper claim placements"
            )

    review = load_yaml(ROOT / "contracts/review-map.template.yaml", errors)
    if isinstance(review, dict):
        comments = review.get("comments")
        comment = comments[0] if isinstance(comments, list) and comments else None
        current = comment.get("current_state") if isinstance(comment, dict) else None
        require_keys(
            current,
            {
                "evidence_ids",
                "claim_ids",
                "experiment_ids",
                "run_ids",
                "analysis_ids",
                "manuscript_locations",
            },
            "review current state",
            errors,
        )

    method_text = (ROOT / "contracts/method-contract.template.md").read_text(
        encoding="utf-8"
    )
    for required in (
        "Artifact version",
        "Content hash",
        "Prediction ID",
        "Origin claim candidate IDs",
        "Experiment IDs",
    ):
        if required not in method_text:
            errors.append(f"method contract: missing {required}")

    safe_phrases = {
        "skills/idea-evolution/SKILL.md": "Scores organize judgment",
        "skills/literature-evidence/SKILL.md": "Never classify a paper's contribution type from citation counts",
        "skills/experiment-lifecycle/SKILL.md": "Do not import fixed thresholds",
        "skills/result-synthesis/SKILL.md": "A new claim always starts as `unassessed`",
    }
    for relative, phrase in safe_phrases.items():
        if phrase not in (ROOT / relative).read_text(encoding="utf-8"):
            errors.append(f"{relative}: missing scientific safety invariant")

    return errors


def validate_repository() -> list[str]:
    errors: list[str] = []
    if yaml is None:
        return [
            "PyYAML is required; run: python3 -m pip install -r requirements-dev.txt"
        ]

    actual = {
        path.name
        for path in SKILLS.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    }
    missing = EXPECTED_SKILLS - actual
    extra = actual - EXPECTED_SKILLS
    if missing:
        errors.append(f"missing composition skills: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"unexpected composition skills: {', '.join(sorted(extra))}")
    for skill in sorted(actual):
        errors.extend(validate_skill(SKILLS / skill))

    for required in (
        "README.md",
        "AGENTS.md",
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "upstreams.lock.yaml",
        "contracts/README.md",
        "requirements-dev.txt",
    ):
        if not (ROOT / required).is_file():
            errors.append(f"missing root file: {required}")

    lock = load_yaml(ROOT / "upstreams.lock.yaml", errors)
    errors.extend(validate_vendor(lock))
    errors.extend(validate_contracts())
    return errors


def main() -> int:
    errors = validate_repository()
    if errors:
        print("Repository validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(
        f"Repository validation passed: {len(EXPECTED_SKILLS)} composition skills, "
        f"{len(EXPECTED_ARTIFACT_ROLES)} artifact roles, "
        f"{len(VENDOR_ROOTS)} hashed vendor selections."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
