from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import yaml

from scripts.researchctl_core.constants import ResearchCtlError
from scripts.researchctl_core.cli import build_parser
from scripts.researchctl_core.gates import (
    release_allowed_stages_for_target,
    release_stage_for_target,
    release_target_sequence,
)
from scripts.researchctl_core.policy import load_policy, retrospective_gate_contract
from scripts.researchctl_core.timeutils import format_utc_timestamp


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_CONTRACT = ROOT / "skills/research/assets/runtime-contract.json"


def renamed_semantic_policy() -> dict[str, object]:
    candidate = json.loads(
        (ROOT / "skills/research/references/policy.yaml").read_text(
            encoding="utf-8"
        )
    )
    release = candidate["gates"].pop("release")
    target_specs = release["approval_targets"]
    release["approval_targets"] = {
        "first_round": target_specs["initial_submission"],
        "review_round": target_specs["revision_rebuttal"],
    }
    candidate["gates"]["external_release"] = release
    for requirement in candidate["workflow_graph"][
        "stage_exit_requirements"
    ].values():
        if isinstance(requirement, dict) and requirement.get("gate") == "release":
            requirement["gate"] = "external_release"
            requirement["target"] = {
                "initial_submission": "first_round",
                "revision_rebuttal": "review_round",
            }[requirement["target"]]

    claim = candidate["gates"]["claim_freeze"]
    modes = claim["approval_modes"]
    claim["approval_modes"] = {
        "standard_review": modes["normal"],
        "legacy_revision_rescue": modes["retrospective_revision_import"],
    }
    claim["default_approval_mode"] = "standard_review"
    return candidate


class RepositoryContractTest(unittest.TestCase):
    def run_python(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, *arguments],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_timestamp_serialization_preserves_lexical_order(self) -> None:
        first = datetime(2026, 7, 15, 8, 43, 17, tzinfo=timezone.utc)
        second = first + timedelta(microseconds=1)

        self.assertEqual(format_utc_timestamp(first), "2026-07-15T08:43:17.000000Z")
        self.assertLess(
            format_utc_timestamp(first),
            format_utc_timestamp(second),
        )

    def test_internal_repository_validator(self) -> None:
        result = self.run_python("scripts/validate_repo.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("one Skill, 6 stages, 4 Gates", result.stdout)
        self.assertIn("v2 revision snapshots", result.stdout)

    def test_one_public_skill_routes_six_references_through_one_policy(self) -> None:
        skills = {
            item.name
            for item in (ROOT / "skills").iterdir()
            if item.is_dir() and (item / "SKILL.md").is_file()
        }
        self.assertEqual(skills, {"research"})
        skill_text = (ROOT / "skills/research/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("references/policy.yaml", skill_text)
        self.assertIn("policy.stages[current_stage].reference", skill_text)
        self.assertIn("references/retrospective-revision-import.md", skill_text)
        self.assertIn(".research/state.json` with `enabled: true", skill_text)
        policy = json.loads(
            (ROOT / "skills/research/references/policy.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(policy["stages"]), set(policy["workflow_graph"]["stage_order"])
        )
        metadata = yaml.safe_load(
            (ROOT / "skills/research/agents/openai.yaml").read_text(encoding="utf-8")
        )
        self.assertIn("$research", metadata["interface"]["default_prompt"])

    def test_policy_and_runtime_contract_have_one_non_overlapping_authority_each(self) -> None:
        policy = json.loads(
            (ROOT / "skills/research/references/policy.yaml").read_text(encoding="utf-8")
        )
        runtime = json.loads(RUNTIME_CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(policy["schema_version"], "2.0")
        self.assertEqual(policy["workflow_version"], "2.0.0")
        self.assertEqual(policy["schema_version"], runtime["state_schema_version"])
        self.assertNotIn("state_contract", policy)
        self.assertNotIn("gate_reopen_contract", policy)
        self.assertEqual(
            runtime["lifecycle"]["statuses"],
            ["active", "terminated", "completed"],
        )
        self.assertEqual(
            runtime["decision"]["required_fields"][-4:],
            [
                "supporting_evidence_ids",
                "opposing_evidence_ids",
                "unresolved_risks",
                "decision_conditions",
            ],
        )
        self.assertEqual(runtime["activation"]["actions"], ["enable", "disable"])
        lifecycle_policy = policy["workspace_lifecycle"]
        self.assertIn("one paper-bound research mainline", lifecycle_policy["scope"])
        self.assertIn("Release is repeatable", lifecycle_policy["completion"])
        self.assertIn("status --json or Dashboard export", lifecycle_policy["terminal_access"])
        self.assertIn("No paused state", lifecycle_policy["inactivity"])
        self.assertIn("inherits no Gate", lifecycle_policy["cross_workspace_reuse"])
        self.assertNotIn("research_line_id", json.dumps(policy))
        entry_fields = runtime["artifact"]["entry_fields"]
        revision_fields = runtime["artifact"]["revision_fields"]
        self.assertTrue(entry_fields)
        self.assertTrue(revision_fields)
        self.assertEqual(len(entry_fields), len(set(entry_fields)))
        self.assertEqual(len(revision_fields), len(set(revision_fields)))
        self.assertTrue(set(entry_fields).isdisjoint(revision_fields))
        self.assertNotIn("version", revision_fields)
        self.assertNotIn("status", revision_fields)
        self.assertEqual(
            runtime["gate"]["cascade_fields"],
            ["upstream_gate_ref", "upstream_decision_id", "upstream_reason"],
        )
        self.assertEqual(
            runtime["stage_transition"]["trigger_prefixes"],
            ["checkpoint", "gate-approve", "gate-reopen"],
        )
        self.assertNotIn("bounded_exploration", policy)
        self.assertEqual(policy["artifact_role_cardinality_default"], "one")
        self.assertEqual(policy["artifact_layout"]["generated_root"], ".research/artifacts")
        self.assertEqual(policy["artifact_layout"]["snapshot_root"], ".research/snapshots")
        self.assertEqual(
            policy["gates"]["idea_freeze"]["selection_artifact_role"],
            "idea.idea_card",
        )
        self.assertEqual(
            policy["gates"]["method_experiment_approval"]["selection_artifact_role"],
            "method.approval_package",
        )
        self.assertNotIn("selection_artifact_role", policy["gates"]["claim_freeze"])
        self.assertNotIn("selection_artifact_role", policy["gates"]["release"])

    def test_state_template_is_empty_v2_control_metadata(self) -> None:
        template = json.loads(
            (ROOT / "skills/research/assets/state.template.json").read_text(
                encoding="utf-8"
            )
        )
        policy = json.loads(
            (ROOT / "skills/research/references/policy.yaml").read_text(encoding="utf-8")
        )
        runtime = json.loads(RUNTIME_CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(set(template), set(runtime["state"]["required_fields"]))
        self.assertEqual(template["schema_version"], "2.0")
        self.assertEqual(template["workflow_version"], "2.0.0")
        self.assertEqual(template["artifacts"], {})
        self.assertEqual(
            template["lifecycle"],
            {"status": "active", "latest_decision_id": None, "history": []},
        )
        self.assertEqual(template["activation_history"], [])
        self.assertEqual(template["stage_history"], [])
        self.assertIsNone(template["last_checkpoint"])
        pending = {"status": "pending", "latest_decision_id": None, "history": []}
        self.assertTrue(
            all(template["gates"][gate] == pending for gate in (
                "idea_freeze",
                "method_experiment_approval",
                "claim_freeze",
            ))
        )
        self.assertEqual(
            template["gates"]["release"],
            {"targets": {
                "initial_submission": pending,
                "revision_rebuttal": pending,
            }},
        )

    def test_skill_progressive_router_and_stage_templates_are_complete(self) -> None:
        skill = (ROOT / "skills/research/SKILL.md").read_text(encoding="utf-8")
        references = ROOT / "skills/research/references"
        numbered_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(references.glob("[0-9][0-9]-*.md"))
        )
        self.assertIn("Check `<project-root>/.research/state.json` before", skill)
        self.assertIn(
            "For ordinary code or repository maintenance", skill
        )
        self.assertIn("do not load a numbered stage reference", skill)
        self.assertIn("For scientific workflow work", skill)
        self.assertIn(
            "read the one reference named by `policy.stages[current_stage].reference` "
            "completely",
            skill,
        )
        self.assertIn(
            "`references/policy.yaml` alone defines workflow, transitions, artifact "
            "roles, Gate requirements, approval modes, cascade, lifecycle, and authority",
            skill,
        )
        self.assertLessEqual(len(skill.split()), 600)
        self.assertNotIn("| `idea` |", skill)
        self.assertNotIn(
            "reopens every currently approved downstream GateRef",
            skill + numbered_text,
        )
        self.assertNotIn("reverse `gate_order`", skill + numbered_text)
        self.assertNotIn("reopen_to", skill + numbered_text)

        experiment = (references / "04-experiment-results.md").read_text(
            encoding="utf-8"
        )
        retrospective = (
            references / "retrospective-revision-import.md"
        ).read_text(encoding="utf-8")
        self.assertNotIn("--retrospective-revision-import", experiment)
        self.assertIn("--retrospective-revision-import", retrospective)
        self.assertIn("method_id: METHOD-001", experiment)
        self.assertIn("## Run a cumulative experiment review", experiment)
        self.assertIn(
            "After every non-retry experiment reaches a terminal outcome", experiment
        )
        self.assertIn(
            "do not propose a new experiment or parameter change", experiment
        )
        self.assertIn("mandatory Skill-level planning boundary", experiment)
        for field in (
            "retry_of_run_id:",
            "execution_differences:",
            "all_execution_differences_declared:",
            "scientific_identity_justification:",
            "research_question_unchanged:",
            "method_parameters_unchanged:",
            "expected_estimand_unchanged:",
        ):
            self.assertIn(field, experiment)
        self.assertIn("does not mechanically prove scientific identity", experiment)
        for field in (
            "reviewed_run_ids:",
            "current_direction_judgment:",
            "supporting_evidence_ids:",
            "opposing_evidence_ids:",
            "eliminated_directions:",
            "next_experiment:",
            "stop_conditions:",
        ):
            self.assertIn(field, experiment)
        self.assertIn("not as an approval or a claim", experiment)

        idea = (references / "01-idea.md").read_text(encoding="utf-8")
        method = (references / "03-method.md").read_text(encoding="utf-8")
        paper = (references / "05-paper.md").read_text(encoding="utf-8")
        revision = (references / "06-revision.md").read_text(encoding="utf-8")
        self.assertIn("literature_evidence_base_ref:", idea)
        self.assertIn("experiment_contract:\n  method_id:", method)
        self.assertIn("release_target: initial_submission", paper)
        self.assertIn(
            "GateRef in `policy.workflow_graph.stage_exit_requirements`", paper
        )
        self.assertIn(
            "GateRef in `policy.workflow_graph.stage_exit_requirements`", revision
        )
        self.assertNotIn("--target initial_submission", paper)
        self.assertNotIn("--target revision_rebuttal", revision)

    def test_runtime_rejects_malformed_or_unsupported_machine_contracts(self) -> None:
        canonical = json.loads(RUNTIME_CONTRACT.read_text(encoding="utf-8"))

        def unsupported_version(contract: dict[str, object]) -> None:
            contract["contract_version"] = "3.0"

        def blank_state_schema(contract: dict[str, object]) -> None:
            contract["state_schema_version"] = " "

        def unknown_root_field(contract: dict[str, object]) -> None:
            contract["shadow_schema"] = {}

        def non_object_section(contract: dict[str, object]) -> None:
            contract["activation"] = []

        def missing_section_field(contract: dict[str, object]) -> None:
            del contract["lifecycle"]["actions"]

        def empty_field_list(contract: dict[str, object]) -> None:
            contract["artifact"]["revision_fields"] = []

        def duplicate_field(contract: dict[str, object]) -> None:
            contract["state"]["required_fields"].append("schema_version")

        def state_contract_drops_writer_field(contract: dict[str, object]) -> None:
            contract["state"]["required_fields"].remove("schema_version")

        def lifecycle_contract_drops_writer_field(
            contract: dict[str, object]
        ) -> None:
            contract["lifecycle"]["record_fields"].remove("history")

        def renamed_gate_target_container(contract: dict[str, object]) -> None:
            contract["gate"]["target_container_fields"] = ["rounds"]

        def decision_contract_drops_writer_field(contract: dict[str, object]) -> None:
            contract["decision"]["required_fields"].remove("reason")

        def activation_contract_drops_writer_field(contract: dict[str, object]) -> None:
            contract["activation"]["event_fields"].remove("actor")

        def cascade_contract_drops_writer_field(contract: dict[str, object]) -> None:
            contract["gate"]["cascade_fields"].remove("upstream_reason")

        def selection_contract_drops_writer_field(contract: dict[str, object]) -> None:
            contract["gate"]["selection_fields"].remove("artifact_ref")

        def artifact_entry_drops_writer_field(contract: dict[str, object]) -> None:
            contract["artifact"]["entry_fields"].remove("revisions")

        def artifact_revision_drops_writer_field(contract: dict[str, object]) -> None:
            contract["artifact"]["revision_fields"].remove("content_hash")

        def checkpoint_contract_drops_writer_field(contract: dict[str, object]) -> None:
            contract["checkpoint"]["fields"].remove("timestamp")

        def transition_contract_drops_writer_field(contract: dict[str, object]) -> None:
            contract["stage_transition"]["fields"].remove("timestamp")

        def required_optional_overlap(contract: dict[str, object]) -> None:
            contract["gate"]["decision_optional_fields"].append("action")

        def gate_ref_overlap(contract: dict[str, object]) -> None:
            contract["gate"]["gate_ref_optional_fields"].append("gate")

        def unsupported_gate_action(contract: dict[str, object]) -> None:
            contract["gate"]["actions"] = ["delete"]

        def unsupported_lifecycle_status(contract: dict[str, object]) -> None:
            contract["lifecycle"]["statuses"] = ["active", "paused"]

        def scientific_record_contract_drops_kind(
            contract: dict[str, object]
        ) -> None:
            contract["scientific_record"]["record_kinds"].remove("attempt")

        def scientific_record_contract_renames_field(
            contract: dict[str, object]
        ) -> None:
            contract["scientific_record"]["source_fields"] = [
                "artifact_role",
                "artifact_id",
                "version",
                "locator",
            ]

        def scientific_record_contract_drops_relation_signature(
            contract: dict[str, object]
        ) -> None:
            del contract["scientific_record"]["relation_signatures"]["attempt_of"]

        def scientific_record_contract_uses_unknown_endpoint_kind(
            contract: dict[str, object]
        ) -> None:
            contract["scientific_record"]["relation_signatures"]["expresses"][
                "source_kinds"
            ] = ["paragraph"]

        mutations = (
            unsupported_version,
            blank_state_schema,
            unknown_root_field,
            non_object_section,
            missing_section_field,
            empty_field_list,
            duplicate_field,
            state_contract_drops_writer_field,
            lifecycle_contract_drops_writer_field,
            renamed_gate_target_container,
            decision_contract_drops_writer_field,
            activation_contract_drops_writer_field,
            cascade_contract_drops_writer_field,
            selection_contract_drops_writer_field,
            artifact_entry_drops_writer_field,
            artifact_revision_drops_writer_field,
            checkpoint_contract_drops_writer_field,
            transition_contract_drops_writer_field,
            required_optional_overlap,
            gate_ref_overlap,
            unsupported_gate_action,
            unsupported_lifecycle_status,
            scientific_record_contract_drops_kind,
            scientific_record_contract_renames_field,
            scientific_record_contract_drops_relation_signature,
            scientific_record_contract_uses_unknown_endpoint_kind,
        )
        with tempfile.TemporaryDirectory() as directory:
            runtime_path = Path(directory) / "runtime-contract.json"
            for mutate in mutations:
                with self.subTest(mutation=mutate.__name__):
                    candidate = json.loads(json.dumps(canonical))
                    mutate(candidate)
                    runtime_path.write_text(
                        json.dumps(candidate, ensure_ascii=False), encoding="utf-8"
                    )
                    with mock.patch.dict(
                        os.environ,
                        {"RESEARCHCTL_RUNTIME_CONTRACT": str(runtime_path)},
                    ):
                        with self.assertRaisesRegex(
                            ResearchCtlError, "runtime contract"
                        ):
                            load_policy()

    def test_runtime_contract_optional_fields_are_data_driven(self) -> None:
        canonical = json.loads(RUNTIME_CONTRACT.read_text(encoding="utf-8"))
        canonical["gate"]["decision_optional_fields"].append("review_note")
        canonical["lifecycle"]["decision_optional_fields"].append(
            "review_context"
        )
        with tempfile.TemporaryDirectory() as directory:
            runtime_path = Path(directory) / "runtime-contract.json"
            runtime_path.write_text(
                json.dumps(canonical, ensure_ascii=False), encoding="utf-8"
            )
            with mock.patch.dict(
                os.environ,
                {"RESEARCHCTL_RUNTIME_CONTRACT": str(runtime_path)},
            ):
                policy = load_policy()

        self.assertIn("review_note", policy.runtime.gate_decision_optional_fields)
        self.assertIn(
            "review_context", policy.runtime.lifecycle_decision_optional_fields
        )

    def test_policy_rejects_ambiguous_or_incomplete_gate_ref_graphs(self) -> None:
        canonical = json.loads(
            (ROOT / "skills/research/references/policy.yaml").read_text(
                encoding="utf-8"
            )
        )

        def duplicate_release_owner(policy: dict[str, object]) -> None:
            policy["workflow_graph"]["stage_exit_requirements"]["revision"] = {
                "gate": "release",
                "target": "initial_submission",
            }

        def unknown_release_target(policy: dict[str, object]) -> None:
            policy["workflow_graph"]["stage_exit_requirements"]["revision"][
                "target"
            ] = "unknown"

        def missing_release_target(policy: dict[str, object]) -> None:
            del policy["workflow_graph"]["stage_exit_requirements"]["paper"][
                "target"
            ]

        def unowned_declared_target(policy: dict[str, object]) -> None:
            policy["gates"]["release"]["approval_targets"]["camera_ready"] = {
                "required_artifact_roles": ["paper.manuscript"]
            }

        def exitless_stage_trigger(policy: dict[str, object]) -> None:
            policy["workflow_graph"]["stage_transitions"]["idea"][1][
                "trigger"
            ]["stage"] = "literature"

        def duplicated_relation_field(policy: dict[str, object]) -> None:
            policy["workflow_graph"]["stage_transitions"]["idea"][1][
                "trigger"
            ]["gate"] = "idea_freeze"

        def missing_normal_claim_mode(policy: dict[str, object]) -> None:
            del policy["gates"]["claim_freeze"]["approval_modes"]["normal"]

        def incomplete_claim_mode(policy: dict[str, object]) -> None:
            policy["gates"]["claim_freeze"]["approval_modes"]["provisional"] = {
                "required_artifact_roles": ["experiment_results.claim_ledger"],
                "cli_flag": "--provisional",
            }

        def wrong_default_claim_mode(policy: dict[str, object]) -> None:
            policy["gates"]["claim_freeze"][
                "default_approval_mode"
            ] = "retrospective_revision_import"

        def removes_claim_mode_contract(policy: dict[str, object]) -> None:
            claim = policy["gates"]["claim_freeze"]
            claim["required_artifact_roles"] = claim["approval_modes"]["normal"][
                "required_artifact_roles"
            ]
            del claim["approval_modes"]
            del claim["default_approval_mode"]

        mutations = (
            duplicate_release_owner,
            unknown_release_target,
            missing_release_target,
            unowned_declared_target,
            exitless_stage_trigger,
            duplicated_relation_field,
            missing_normal_claim_mode,
            incomplete_claim_mode,
            wrong_default_claim_mode,
            removes_claim_mode_contract,
        )
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            for mutate in mutations:
                with self.subTest(mutation=mutate.__name__):
                    candidate = json.loads(json.dumps(canonical))
                    mutate(candidate)
                    policy_path.write_text(
                        json.dumps(candidate, ensure_ascii=False), encoding="utf-8"
                    )
                    with mock.patch.dict(
                        os.environ, {"RESEARCHCTL_POLICY": str(policy_path)}
                    ):
                        with self.assertRaises(ResearchCtlError):
                            load_policy()

    def test_record_manifests_are_registered_artifacts_not_state(self) -> None:
        contract = json.loads(RUNTIME_CONTRACT.read_text(encoding="utf-8"))
        state_template = json.loads(
            (ROOT / "skills/research/assets/state.template.json").read_text(
                encoding="utf-8"
            )
        )
        policy = json.loads(
            (ROOT / "skills/research/references/policy.yaml").read_text(
                encoding="utf-8"
            )
        )
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        records_source = (
            ROOT / "scripts/researchctl_core/records.py"
        ).read_text(encoding="utf-8")

        self.assertEqual(
            contract["scientific_record"]["artifact_role"], "record_manifest"
        )
        self.assertEqual(
            set(contract["scientific_record"]["record_kinds"]),
            {
                "candidate",
                "search_run",
                "passage_evidence",
                "experiment",
                "attempt",
                "analysis",
                "claim",
                "paper_location",
                "review_concern",
            },
        )
        self.assertEqual(
            set(contract["scientific_record"]["relation_signatures"]),
            set(contract["scientific_record"]["relation_kinds"]),
        )
        self.assertNotIn("records", state_template)
        self.assertIn(
            "optional stage record manifest",
            policy["artifact_layout"]["instruction"],
        )
        self.assertIn("artifact register record_manifest", readme)
        self.assertIn("inspect_record_manifests", records_source)

    def test_policy_semantic_names_drive_release_and_approval_modes(self) -> None:
        candidate = renamed_semantic_policy()

        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            policy_path.write_text(
                json.dumps(candidate, ensure_ascii=False), encoding="utf-8"
            )
            with mock.patch.dict(
                os.environ, {"RESEARCHCTL_POLICY": str(policy_path)}
            ):
                policy = load_policy()

        self.assertEqual(policy.release_gate, "external_release")
        self.assertEqual(policy.release_targets, ("first_round", "review_round"))
        self.assertEqual(policy.initial_release_target, "first_round")
        self.assertEqual(release_target_sequence(policy), policy.release_targets)
        self.assertEqual(release_stage_for_target(policy, "first_round"), "paper")
        self.assertEqual(release_stage_for_target(policy, "review_round"), "revision")
        self.assertIn(
            "paper", release_allowed_stages_for_target(policy, "first_round")
        )
        retrospective = retrospective_gate_contract(policy)
        self.assertIsNotNone(retrospective)
        self.assertEqual(retrospective[:2], ("claim_freeze", "legacy_revision_rescue"))
        parser = build_parser(policy)
        generic = parser.parse_args(
            [
                "gate",
                "approve",
                "claim_freeze",
                "--reason",
                "Policy-derived mode.",
                "--supporting-evidence-id",
                "EVID-001",
                "--decision-condition",
                "Reopen if evidence changes.",
                "--approval-mode",
                "standard_review",
            ]
        )
        self.assertEqual(generic.approval_mode, "standard_review")
        compatibility_alias = parser.parse_args(
            [
                "gate",
                "approve",
                "claim_freeze",
                "--reason",
                "Policy-derived exception.",
                "--supporting-evidence-id",
                "EVID-001",
                "--decision-condition",
                "Reopen if evidence changes.",
                "--retrospective-revision-import",
            ]
        )
        self.assertEqual(
            compatibility_alias.retrospective_mode_requested,
            "legacy_revision_rescue",
        )

    def test_retrospective_mode_mutability_is_optional_in_both_loaders(self) -> None:
        candidate = json.loads(
            (ROOT / "skills/research/references/policy.yaml").read_text(
                encoding="utf-8"
            )
        )
        del candidate["gates"]["claim_freeze"]["approval_modes"][
            "retrospective_revision_import"
        ]["mutable_after_approval_roles"]

        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            policy_path.write_text(
                json.dumps(candidate, ensure_ascii=False), encoding="utf-8"
            )
            with mock.patch.dict(
                os.environ, {"RESEARCHCTL_POLICY": str(policy_path)}
            ):
                policy = load_policy()

        self.assertEqual(
            policy.gate_specs["claim_freeze"]["approval_modes"][
                "retrospective_revision_import"
            ].get("mutable_after_approval_roles", []),
            [],
        )

    def test_policy_loader_rejects_malformed_semantic_governance_sections(self) -> None:
        canonical = json.loads(
            (ROOT / "skills/research/references/policy.yaml").read_text(
                encoding="utf-8"
            )
        )

        def unknown_root_section(policy: dict[str, object]) -> None:
            policy["shadow_governance"] = {"enabled": True}

        def missing_lifecycle_rule(policy: dict[str, object]) -> None:
            del policy["workspace_lifecycle"]["termination"]

        def empty_lifecycle_rule(policy: dict[str, object]) -> None:
            policy["workspace_lifecycle"]["terminal_access"] = "  "

        def malformed_authority_boundary(policy: dict[str, object]) -> None:
            policy["authority_boundary"] = [
                "Gate decisions remain human decisions.",
                "Gate decisions remain human decisions.",
            ]

        def malformed_stage_contract(policy: dict[str, object]) -> None:
            policy["stages"]["idea"]["allowed_actions"] = []

        def unknown_gate_field(policy: dict[str, object]) -> None:
            policy["gates"]["idea_freeze"]["shadow_requirement"] = "ignored"

        def empty_gate_review_list(policy: dict[str, object]) -> None:
            policy["gates"]["release"]["approval_requires"] = []

        def unknown_target_field(policy: dict[str, object]) -> None:
            policy["gates"]["release"]["approval_targets"][
                "initial_submission"
            ]["shadow_binding"] = True

        def unknown_mode_field(policy: dict[str, object]) -> None:
            policy["gates"]["claim_freeze"]["approval_modes"]["normal"][
                "shadow_mode"
            ] = "ignored"

        def misleading_artifact_layout_instruction(policy: dict[str, object]) -> None:
            policy["artifact_layout"]["instruction"] = "Use some other layout."

        def conflicting_retrospective_cli_flag(policy: dict[str, object]) -> None:
            policy["gates"]["claim_freeze"]["approval_modes"][
                "retrospective_revision_import"
            ]["cli_flag"] = "--approval-mode"

        def conflicting_retrospective_help_flag(policy: dict[str, object]) -> None:
            policy["gates"]["claim_freeze"]["approval_modes"][
                "retrospective_revision_import"
            ]["cli_flag"] = "--help"

        mutations = (
            unknown_root_section,
            missing_lifecycle_rule,
            empty_lifecycle_rule,
            malformed_authority_boundary,
            malformed_stage_contract,
            unknown_gate_field,
            empty_gate_review_list,
            unknown_target_field,
            unknown_mode_field,
            misleading_artifact_layout_instruction,
            conflicting_retrospective_cli_flag,
            conflicting_retrospective_help_flag,
        )
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            for mutate in mutations:
                with self.subTest(mutation=mutate.__name__):
                    candidate = json.loads(json.dumps(canonical))
                    mutate(candidate)
                    policy_path.write_text(
                        json.dumps(candidate, ensure_ascii=False), encoding="utf-8"
                    )
                    with mock.patch.dict(
                        os.environ, {"RESEARCHCTL_POLICY": str(policy_path)}
                    ):
                        with self.assertRaises(ResearchCtlError):
                            load_policy()

    def test_generic_approval_mode_name_is_scoped_to_its_gate(self) -> None:
        candidate = json.loads(
            (ROOT / "skills/research/references/policy.yaml").read_text(
                encoding="utf-8"
            )
        )
        idea = candidate["gates"]["idea_freeze"]
        roles = idea.pop("required_artifact_roles")
        approval_requires = idea.pop("approval_requires")
        idea.pop("selection_artifact_role")
        ordinary_contract = {
            "required_artifact_roles": roles,
            "approval_requires": approval_requires,
        }
        idea["approval_modes"] = {
            "normal": json.loads(json.dumps(ordinary_contract)),
            "retrospective_revision_import": json.loads(
                json.dumps(ordinary_contract)
            ),
        }
        idea["default_approval_mode"] = "normal"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            subprocess.run(
                ["git", "init", "-q", str(project)],
                check=True,
                capture_output=True,
                text=True,
            )
            policy_path = root / "policy.json"
            policy_path.write_text(
                json.dumps(candidate, ensure_ascii=False), encoding="utf-8"
            )
            environment = os.environ.copy()
            environment["RESEARCHCTL_POLICY"] = str(policy_path)
            environment.pop("RESEARCHCTL_RUNTIME_CONTRACT", None)
            environment["RESEARCHCTL_ACTOR"] = "policy-test"

            def run_ctl(*arguments: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(ROOT / "scripts/researchctl.py"), *arguments],
                    cwd=project,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )

            initialized = run_ctl("init")
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            for role_reference in roles:
                stage, role = role_reference.split(".", 1)
                source = project / "work" / stage / f"{role}.md"
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(f"# {role_reference}\n", encoding="utf-8")
                registered = run_ctl(
                    "artifact",
                    "register",
                    role,
                    "--stage",
                    stage,
                    "--path",
                    str(source),
                    "--artifact-id",
                    f"{stage}-{role}".upper().replace("_", "-"),
                )
                self.assertEqual(registered.returncode, 0, registered.stderr)

            approved = run_ctl(
                "gate",
                "approve",
                "idea_freeze",
                "--reason",
                "Approve the Gate-local ordinary mode.",
                "--supporting-evidence-id",
                "EVID-LOCAL-MODE",
                "--decision-condition",
                "Reopen if the bound evidence changes.",
                "--approval-mode",
                "retrospective_revision_import",
            )
            self.assertEqual(approved.returncode, 0, approved.stderr)
            state = json.loads(
                (project / ".research/state.json").read_text(encoding="utf-8")
            )
            decision = state["gates"]["idea_freeze"]["history"][-1]
            self.assertEqual(
                decision["approval_mode"], "retrospective_revision_import"
            )
            self.assertNotIn("waived_artifact_roles", decision)

    def test_renamed_policy_semantics_work_through_cli_completion(self) -> None:
        candidate = renamed_semantic_policy()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            subprocess.run(
                ["git", "init", "-q", str(project)],
                check=True,
                capture_output=True,
                text=True,
            )
            policy_path = root / "policy.json"
            policy_path.write_text(
                json.dumps(candidate, ensure_ascii=False), encoding="utf-8"
            )
            environment = os.environ.copy()
            environment["RESEARCHCTL_POLICY"] = str(policy_path)
            environment.pop("RESEARCHCTL_RUNTIME_CONTRACT", None)
            environment["RESEARCHCTL_ACTOR"] = "policy-test"

            def run_ctl(*arguments: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(ROOT / "scripts/researchctl.py"), *arguments],
                    cwd=project,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )

            initialized = run_ctl("init")
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            registered: set[str] = set()

            def register_roles(roles: list[str]) -> None:
                for role_reference in roles:
                    if role_reference in registered:
                        continue
                    stage, role = role_reference.split(".", 1)
                    source = project / "work" / stage / f"{role}.md"
                    source.parent.mkdir(parents=True, exist_ok=True)
                    source.write_text(f"# {role_reference}\n", encoding="utf-8")
                    result = run_ctl(
                        "artifact",
                        "register",
                        role,
                        "--stage",
                        stage,
                        "--path",
                        str(source),
                        "--artifact-id",
                        f"{stage}-{role}".upper().replace("_", "-"),
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    registered.add(role_reference)

            def approve(
                gate: str,
                roles: list[str],
                *,
                selected_id: str | None = None,
                target: str | None = None,
                approval_mode: str | None = None,
            ) -> None:
                register_roles(roles)
                arguments = [
                    "gate",
                    "approve",
                    gate,
                    "--reason",
                    f"Approve renamed policy Gate {gate}.",
                    "--supporting-evidence-id",
                    f"EVID-{gate}",
                    "--decision-condition",
                    f"Reopen {gate} when its evidence changes.",
                ]
                if selected_id is not None:
                    arguments.extend(["--selected-id", selected_id])
                if target is not None:
                    arguments.extend(["--target", target])
                if approval_mode is not None:
                    arguments.extend(["--approval-mode", approval_mode])
                result = run_ctl(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)

            approve(
                "idea_freeze",
                candidate["gates"]["idea_freeze"]["required_artifact_roles"],
                selected_id="IDEA-RENAMED",
            )
            approve(
                "method_experiment_approval",
                candidate["gates"]["method_experiment_approval"][
                    "required_artifact_roles"
                ],
                selected_id="METHOD-RENAMED",
            )
            approve(
                "claim_freeze",
                candidate["gates"]["claim_freeze"]["approval_modes"][
                    "standard_review"
                ]["required_artifact_roles"],
                approval_mode="standard_review",
            )
            approve(
                "external_release",
                candidate["gates"]["external_release"]["approval_targets"][
                    "first_round"
                ]["required_artifact_roles"],
                target="first_round",
            )
            completed = run_ctl(
                "lifecycle",
                "complete",
                "--reason",
                "The renamed first release round is complete.",
                "--supporting-evidence-id",
                "EVID-COMPLETE",
                "--decision-condition",
                "Reopen if the same mainline resumes.",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            state = json.loads(
                (project / ".research/state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["lifecycle"]["status"], "completed")
            self.assertEqual(
                state["gates"]["external_release"]["targets"]["first_round"][
                    "status"
                ],
                "approved",
            )

    def test_plugin_marketplace_and_hook_manifests_match(self) -> None:
        plugin = json.loads(
            (ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        marketplace = json.loads(
            (ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
        )["plugins"][0]
        self.assertEqual(plugin["name"], "scientific-research-skill")
        self.assertEqual(marketplace["name"], plugin["name"])
        self.assertEqual(marketplace["version"], plugin["version"])
        self.assertEqual(marketplace["source"], {"source": "local", "path": "."})
        self.assertNotIn("hooks", plugin)
        hooks = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
        self.assertEqual(
            set(hooks),
            {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"},
        )
        for event, groups in hooks.items():
            handler = groups[0]["hooks"][0]
            self.assertEqual(handler["type"], "command", event)
            self.assertIn("${PLUGIN_ROOT}", handler["command"])
            self.assertIn("research-workflow-hook.js", handler["command"])

    def test_public_cli_exposes_auto_revision_but_not_manual_version_status(self) -> None:
        help_result = self.run_python("scripts/researchctl.py", "--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        for command in (
            "init",
            "status",
            "enable",
            "disable",
            "artifact",
            "gate",
            "lifecycle",
            "checkpoint",
            "dashboard",
            "doctor",
        ):
            self.assertIn(command, help_result.stdout)
        register = self.run_python(
            "scripts/researchctl.py", "artifact", "register", "--help"
        )
        self.assertEqual(register.returncode, 0, register.stderr)
        self.assertIn("--artifact-id", register.stdout)
        self.assertNotIn("--version", register.stdout)
        self.assertNotIn("--status", register.stdout)
        gate = self.run_python("scripts/researchctl.py", "gate", "--help")
        self.assertIn("--selected-id", gate.stdout)
        self.assertIn("--supporting-evidence-id", gate.stdout)
        self.assertIn("--decision-condition", gate.stdout)
        lifecycle = self.run_python(
            "scripts/researchctl.py", "lifecycle", "--help"
        )
        self.assertEqual(lifecycle.returncode, 0, lifecycle.stderr)
        self.assertIn("--gate", lifecycle.stdout)
        disable = self.run_python("scripts/researchctl.py", "disable", "--help")
        self.assertIn("--reason", disable.stdout)

    def test_runtime_has_no_migration_module_or_duplicate_project_memory(self) -> None:
        core = ROOT / "scripts/researchctl_core"
        self.assertFalse((core / "migration.py").exists())
        self.assertFalse((ROOT / "contracts").exists())
        self.assertFalse((ROOT / "profiles").exists())
        source = "\n".join(
            item.read_text(encoding="utf-8") for item in core.glob("*.py")
        )
        self.assertNotIn("Codex-global memory", source)
        self.assertNotIn("project-overview", source)
        self.assertNotIn("overview.md", source)
        self.assertNotIn("round_id", source)

    def test_docs_contains_only_matt_maintainer_configuration(self) -> None:
        docs = {
            path.relative_to(ROOT)
            for path in (ROOT / "docs").rglob("*")
            if path.is_file()
        }
        self.assertEqual(
            docs,
            {
                Path("docs/agents/domain.md"),
                Path("docs/agents/issue-tracker.md"),
                Path("docs/agents/triage-labels.md"),
            },
        )
        domain = (ROOT / "docs/agents/domain.md").read_text(encoding="utf-8")
        self.assertIn("decisions/glossary.md", domain)
        self.assertIn("Do not create `CONTEXT.md`", domain)

    def test_public_capability_claims_are_boundary_qualified(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        glossary = (ROOT / "decisions/glossary.md").read_text(encoding="utf-8")
        decision = (
            ROOT / "decisions/0002-evidence-qualified-capability-claims.md"
        ).read_text(encoding="utf-8")

        for required in (
            "Core + Reference Stack",
            "Current",
            "Target",
            "Benchmark-verified",
            "不保证科研正确性、统计有效性、真实创新、论文质量或录用",
        ):
            self.assertIn(required, readme)
        for required in (
            "**Process capability — accepted**",
            "**High — accepted capability level**",
            "**Very high — accepted capability level**",
            "**Approaches Evo — accepted comparison result**",
        ):
            self.assertIn(required, glossary)
        self.assertIn("Every public capability claim must name", decision)
        self.assertIn("Track A and Track B", decision)

    def test_external_repositories_remain_links_and_root_license_is_preserved(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for url in (
            "https://github.com/Galaxy-Dawn/claude-scholar",
            "https://github.com/EvoScientist/EvoSkills",
            "https://github.com/Yuan1z0825/nature-skills",
            "https://github.com/lingzhi227/agent-research-skills",
        ):
            self.assertIn(url, readme)
        for stale in ("vendor", "THIRD_PARTY_NOTICES.md", "upstreams.lock.yaml"):
            self.assertFalse((ROOT / stale).exists())
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", license_text)
        self.assertIn("Copyright 2026 Fusica", license_text)


if __name__ == "__main__":
    unittest.main()
