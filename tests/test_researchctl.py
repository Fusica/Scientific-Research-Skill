from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESEARCHCTL = ROOT / "scripts/researchctl.py"


def policy_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "workflow_version": "1.1.0-test",
        "stage_order": [
            "idea",
            "literature",
            "method",
            "experiment_results",
            "paper",
            "revision",
        ],
        "gate_order": [
            "idea_freeze",
            "method_experiment_approval",
            "claim_freeze",
            "release",
        ],
        "state_contract": {
            "artifact_pointer_fields": [
                "path",
                "artifact_id",
                "version",
                "content_hash",
                "status",
            ]
        },
        "artifact_layout": {
            "generated_root": ".research/artifacts",
            "stage_path_template": ".research/artifacts/<stage-id>",
            "instruction": (
                "Write new workflow artifacts under .research/artifacts/<stage-id>/; "
                "never create project-root research/, contracts/, or artifacts/. "
                "Register existing user files in place."
            ),
        },
        "gates": {
            "idea_freeze": {
                "advance_to": "method",
                "reopen_to": "idea",
                "required_artifact_roles": [
                    "idea.idea_card",
                    "literature.evidence_base",
                ],
            },
            "method_experiment_approval": {
                "advance_to": "experiment_results",
                "reopen_to": "method",
                "required_artifact_roles": ["method.approval_package"],
            },
            "claim_freeze": {
                "advance_to": "paper",
                "reopen_to": "experiment_results",
                "required_artifact_roles": [
                    "experiment_results.experiment_matrix",
                    "experiment_results.run_registry",
                    "experiment_results.decision_log",
                    "experiment_results.analysis_registry",
                    "experiment_results.artifact_manifest",
                    "experiment_results.claim_ledger",
                ],
            },
            "release": {
                "advance_to": "revision",
                "release_targets": ["initial_submission", "revision_rebuttal"],
                "required_artifact_roles_by_target": {
                    "initial_submission": [
                        "paper.manuscript",
                        "paper.claim_map",
                        "paper.change_map",
                        "paper.bibliography_provenance",
                        "paper.compilation_log",
                        "paper.rendered_output",
                        "paper.render_inspection_record",
                        "paper.submission_checklist",
                    ],
                    "revision_rebuttal": [
                        "revision.revised_manuscript",
                        "revision.review_map",
                        "revision.change_log",
                        "revision.response_document",
                        "revision.manuscript_diff",
                        "revision.verification_records",
                        "revision.rendered_output",
                        "revision.release_checklist",
                    ],
                },
            },
        },
        "allowed_transitions": {
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
                {"to": "experiment_results", "required_gates": ["method_experiment_approval"]}
            ],
            "experiment_results": [
                {"to": "idea", "required_gates": []},
                {"to": "literature", "required_gates": []},
                {"to": "method", "required_gates": []},
                {"to": "paper", "required_gates": ["claim_freeze"]}
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
        },
        "stages": {},
        "global_prohibited_actions": [],
        "semantic_audit": {},
    }


class ResearchCtlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        temporary = Path(self.temporary.name)
        self.project = temporary / "research-project"
        self.project.mkdir()
        subprocess.run(
            ["git", "init", "-q", str(self.project)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.policy = temporary / "policy.yaml"
        self.policy.write_text(
            json.dumps(policy_document(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def run_ctl(
        self, *arguments: str, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["RESEARCHCTL_POLICY"] = str(self.policy)
        environment["RESEARCHCTL_ACTOR"] = "unit-test-researcher"
        return subprocess.run(
            [sys.executable, str(RESEARCHCTL), *arguments],
            cwd=cwd or self.project,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def load_state(self) -> dict[str, object]:
        return json.loads(
            (self.project / ".research/state.json").read_text(encoding="utf-8")
        )

    def initialize(self) -> dict[str, object]:
        result = self.run_ctl("init")
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.load_state()

    def register_artifact(
        self,
        role_reference: str,
        *,
        artifact_id: str | None = None,
        version: str = "1",
        status: str = "approval-ready",
        content: str | None = None,
        path: Path | None = None,
    ) -> dict[str, object]:
        stage, role = role_reference.split(".", 1)
        identifier = artifact_id or f"{stage}-{role}-001".upper().replace("_", "-")
        candidate = path or (
            self.project
            / ".research"
            / "artifacts"
            / stage
            / f"{stage}-{role}-v{version}.md"
        )
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(
            content or f"# {role_reference} {identifier}@{version}\n",
            encoding="utf-8",
        )
        result = self.run_ctl(
            "artifact",
            "register",
            role,
            "--stage",
            stage,
            "--path",
            str(candidate),
            "--artifact-id",
            identifier,
            "--version",
            version,
            "--status",
            status,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.load_state()["artifacts"][stage][role][identifier]

    def register_gate_artifacts(
        self, gate: str, *, release_target: str | None = None
    ) -> None:
        policy = json.loads(self.policy.read_text(encoding="utf-8"))
        spec = policy["gates"][gate]
        if gate == "release":
            roles = spec["required_artifact_roles_by_target"][release_target]
        else:
            roles = spec["required_artifact_roles"]
        for role in roles:
            self.register_artifact(role)

    def test_init_is_idempotent_preserves_memory_and_sets_local_exclude(self) -> None:
        original_state = self.initialize()
        memory = self.project / ".research/memory.md"
        artifact_root = self.project / ".research/artifacts"
        self.assertTrue(artifact_root.is_dir())
        memory.write_text("personal project memory\n", encoding="utf-8")
        artifact_root.rmdir()

        second = self.run_ctl("init")

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("state already exists; left unchanged", second.stdout)
        self.assertIn("memory already exists; left unchanged", second.stdout)
        self.assertEqual(self.load_state(), original_state)
        self.assertTrue(artifact_root.is_dir())
        for relative in ("research", "contracts", "artifacts"):
            self.assertFalse((self.project / relative).exists())
        self.assertEqual(memory.read_text(encoding="utf-8"), "personal project memory\n")
        exclude_path = subprocess.run(
            ["git", "-C", str(self.project), "rev-parse", "--git-path", "info/exclude"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        exclude = Path(exclude_path)
        if not exclude.is_absolute():
            exclude = self.project / exclude
        lines = exclude.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines.count(".research/"), 1)
        self.assertFalse((self.project / ".gitignore").exists())

    def test_policy_rejects_generated_artifact_root_outside_research(self) -> None:
        policy = policy_document()
        policy["artifact_layout"] = {
            "generated_root": "artifacts",
            "stage_path_template": "artifacts/<stage-id>",
            "instruction": "Write new workflow artifacts under artifacts/<stage-id>/.",
        }
        self.policy.write_text(
            json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        result = self.run_ctl("init")

        self.assertEqual(result.returncode, 2)
        self.assertIn("must stay under .research", result.stderr)
        self.assertFalse((self.project / ".research/state.json").exists())

    def test_status_json_and_nested_git_root(self) -> None:
        state = self.initialize()
        nested = self.project / "src/deep"
        nested.mkdir(parents=True)

        result = self.run_ctl("status", "--json", cwd=nested)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), state)

    def test_enable_and_disable_are_idempotent(self) -> None:
        self.initialize()

        disabled = self.run_ctl("disable")
        self.assertEqual(disabled.returncode, 0, disabled.stderr)
        self.assertIs(self.load_state()["enabled"], False)
        disabled_again = self.run_ctl("disable")
        self.assertEqual(disabled_again.returncode, 0, disabled_again.stderr)
        self.assertIn("already disabled", disabled_again.stdout)

        enabled = self.run_ctl("enable")
        self.assertEqual(enabled.returncode, 0, enabled.stderr)
        self.assertIs(self.load_state()["enabled"], True)

    def test_disable_remains_available_after_an_incompatible_update(self) -> None:
        self.initialize()
        state_path = self.project / ".research/state.json"
        state = self.load_state()
        state["workflow_version"] = "obsolete-workflow"
        state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")

        disabled = self.run_ctl("disable")
        self.assertEqual(disabled.returncode, 0, disabled.stderr)
        self.assertIs(self.load_state()["enabled"], False)

        enabled = self.run_ctl("enable")
        self.assertEqual(enabled.returncode, 2)
        self.assertIn("workflow_version does not match", enabled.stderr)

    def test_artifact_register_hashes_files_and_is_idempotent(self) -> None:
        self.initialize()
        path = self.project / ".research/artifacts/idea/idea-card-v1.md"
        pointer = self.register_artifact(
            "idea.idea_card",
            artifact_id="IDEA-CARD-001",
            path=path,
            content="# Frozen idea v1\n",
        )
        self.assertEqual(
            pointer["path"], ".research/artifacts/idea/idea-card-v1.md"
        )
        self.assertEqual(pointer["artifact_id"], "IDEA-CARD-001")
        self.assertEqual(pointer["version"], "1")
        self.assertEqual(pointer["status"], "approval-ready")
        self.assertRegex(pointer["content_hash"], r"^sha256:[0-9a-f]{64}$")

        repeated = self.run_ctl(
            "artifact",
            "register",
            "idea_card",
            "--stage",
            "idea",
            "--path",
            str(path),
            "--artifact-id",
            "IDEA-CARD-001",
            "--version",
            "1",
            "--status",
            "approval-ready",
        )
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertIn("already registered", repeated.stdout)

        conflicting = self.project / ".research/artifacts/idea/conflicting-v1.md"
        conflicting.write_text("different content\n", encoding="utf-8")
        rejected = self.run_ctl(
            "artifact",
            "register",
            "idea_card",
            "--stage",
            "idea",
            "--path",
            str(conflicting),
            "--artifact-id",
            "IDEA-CARD-001",
            "--version",
            "1",
            "--status",
            "approval-ready",
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("use a new version", rejected.stderr)
        self.assertEqual(
            self.load_state()["artifacts"]["idea"]["idea_card"]["IDEA-CARD-001"],
            pointer,
        )

    def test_existing_project_file_can_still_be_registered_in_place(self) -> None:
        self.initialize()
        manuscript = self.project / "paper/main.tex"
        pointer = self.register_artifact(
            "paper.manuscript",
            artifact_id="MANUSCRIPT-001",
            path=manuscript,
            content="Existing manuscript source\n",
        )

        self.assertEqual(pointer["path"], "paper/main.tex")
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

    def test_artifact_register_defaults_status_and_rejects_control_metadata(self) -> None:
        self.initialize()
        artifact = self.project / ".research/artifacts/idea/idea-card.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("# Idea\n", encoding="utf-8")
        registered = self.run_ctl(
            "artifact",
            "register",
            "idea_card",
            "--stage",
            "idea",
            "--path",
            str(artifact),
            "--artifact-id",
            "IDEA-CARD-001",
            "--version",
            "1",
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        pointer = self.load_state()["artifacts"]["idea"]["idea_card"][
            "IDEA-CARD-001"
        ]
        self.assertEqual(pointer["status"], "current")

        for candidate in (
            self.project / ".research/state.json",
            self.project / ".research/memory.md",
        ):
            rejected = self.run_ctl(
                "artifact",
                "register",
                "evidence_base",
                "--stage",
                "literature",
                "--path",
                str(candidate),
                "--artifact-id",
                "EVIDENCE-001",
                "--version",
                "1",
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("control metadata cannot be registered", rejected.stderr)

    def test_artifact_register_conservatively_upgrades_legacy_containers(self) -> None:
        self.initialize()
        legacy = self.project / "artifacts/legacy-note.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("legacy\n", encoding="utf-8")
        state_path = self.project / ".research/state.json"
        state = self.load_state()
        state["artifacts"] = ["artifacts/legacy-note.md"]
        state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")

        pointer = self.register_artifact("idea.idea_card")
        state = self.load_state()
        self.assertEqual(
            state["artifacts"]["idea"]["idea_card"][pointer["artifact_id"]],
            pointer,
        )
        self.assertEqual(
            state["artifacts"]["_legacy"]["artifacts"],
            ["artifacts/legacy-note.md"],
        )

        state["artifacts"] = {"idea": {"idea_card": pointer}}
        state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")
        upgraded = self.register_artifact(
            "idea.idea_card",
            artifact_id=str(pointer["artifact_id"]),
            version="2",
            content="# Updated idea\n",
        )
        self.assertEqual(upgraded["version"], "2")
        self.assertNotIn("path", self.load_state()["artifacts"]["idea"]["idea_card"])

    def test_artifact_register_rejects_missing_and_directory_paths(self) -> None:
        self.initialize()
        missing = self.run_ctl(
            "artifact",
            "register",
            "idea_card",
            "--path",
            ".research/artifacts/idea/missing.md",
            "--artifact-id",
            "IDEA-CARD-001",
            "--version",
            "1",
            "--status",
            "draft",
        )
        self.assertEqual(missing.returncode, 2)
        self.assertIn("cannot be resolved", missing.stderr)

        directory = self.project / ".research/artifacts/idea/directory"
        directory.mkdir(parents=True)
        rejected = self.run_ctl(
            "artifact",
            "register",
            "idea_card",
            "--path",
            str(directory),
            "--artifact-id",
            "IDEA-CARD-001",
            "--version",
            "1",
            "--status",
            "draft",
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("regular file", rejected.stderr)
        self.assertEqual(self.load_state()["artifacts"], {})

    def test_gate_requires_complete_current_hash_verified_roles(self) -> None:
        self.initialize()
        missing = self.run_ctl(
            "gate", "approve", "idea_freeze", "--reason", "Ready"
        )
        self.assertEqual(missing.returncode, 2)
        self.assertIn("missing required artifact role idea.idea_card", missing.stderr)

        self.register_artifact("idea.idea_card")
        partial = self.run_ctl(
            "gate", "approve", "idea_freeze", "--reason", "Ready"
        )
        self.assertEqual(partial.returncode, 2)
        self.assertIn(
            "missing required artifact role literature.evidence_base", partial.stderr
        )

        self.register_artifact("literature.evidence_base")
        idea_path = (
            self.project
            / ".research/artifacts/idea/idea-idea_card-v1.md"
        )
        idea_path.write_text("tampered after registration\n", encoding="utf-8")
        stale = self.run_ctl(
            "gate", "approve", "idea_freeze", "--reason", "Ready"
        )
        self.assertEqual(stale.returncode, 2)
        self.assertIn("hash mismatch", stale.stderr)
        self.assertEqual(
            self.load_state()["gates"]["idea_freeze"]["status"], "pending"
        )

    def test_gate_decisions_keep_history_and_use_policy_stage_advance(self) -> None:
        self.initialize()
        self.register_gate_artifacts("idea_freeze")

        approved = self.run_ctl(
            "gate", "approve", "idea_freeze", "--reason", "Evidence package reviewed"
        )
        self.assertEqual(approved.returncode, 0, approved.stderr)
        state = self.load_state()
        record = state["gates"]["idea_freeze"]
        self.assertEqual(record["status"], "approved")
        self.assertEqual(len(record["history"]), 1)
        decision = record["history"][0]
        self.assertEqual(decision["action"], "approve")
        self.assertEqual(decision["reason"], "Evidence package reviewed")
        self.assertEqual(decision["actor"], "unit-test-researcher")
        self.assertEqual(
            {reference["label"] for reference in decision["artifact_refs"]},
            {
                "artifacts.idea.idea_card.IDEA-IDEA-CARD-001",
                "artifacts.literature.evidence_base.LITERATURE-EVIDENCE-BASE-001",
            },
        )
        for reference in decision["artifact_refs"]:
            self.assertRegex(reference["content_hash"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(record["latest_decision_id"], decision["decision_id"])
        self.assertEqual(state["current_stage"], "method")
        self.assertEqual(state["stage_history"][0]["from_stage"], "idea")
        self.assertEqual(state["stage_history"][0]["to_stage"], "method")

        reopened = self.run_ctl(
            "gate", "reopen", "idea_freeze", "--reason", "New contradictory evidence"
        )
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        record = self.load_state()["gates"]["idea_freeze"]
        self.assertEqual(record["status"], "reopened")
        self.assertEqual(len(record["history"]), 2)
        self.assertEqual(record["history"][-1]["previous_status"], "approved")
        self.assertEqual(record["history"][-1]["new_status"], "reopened")
        self.assertEqual(
            record["history"][-1]["artifact_refs"],
            record["history"][0]["artifact_refs"],
        )
        self.assertEqual(self.load_state()["current_stage"], "idea")

    def test_new_artifact_version_preserves_gate_history_and_tamper_is_detected(
        self,
    ) -> None:
        self.initialize()
        first_path = self.project / ".research/artifacts/idea/idea-card-v1.md"
        self.register_artifact(
            "idea.idea_card",
            artifact_id="IDEA-CARD-001",
            version="1",
            path=first_path,
            content="# Idea v1\n",
        )
        self.register_artifact("literature.evidence_base")
        approved_v1 = self.run_ctl(
            "gate", "approve", "idea_freeze", "--reason", "Approve v1"
        )
        self.assertEqual(approved_v1.returncode, 0, approved_v1.stderr)
        first_decision = self.load_state()["gates"]["idea_freeze"]["history"][0]
        first_reference = next(
            reference
            for reference in first_decision["artifact_refs"]
            if reference["artifact_id"] == "IDEA-CARD-001"
        )

        blocked_path = (
            self.project / ".research/artifacts/idea/idea-card-blocked-v2.md"
        )
        blocked_path.write_text("# Blocked idea v2\n", encoding="utf-8")
        frozen = self.run_ctl(
            "artifact",
            "register",
            "idea_card",
            "--stage",
            "idea",
            "--path",
            str(blocked_path),
            "--artifact-id",
            "IDEA-CARD-001",
            "--version",
            "2",
            "--status",
            "approval-ready",
        )
        self.assertEqual(frozen.returncode, 2)
        self.assertIn("reopen that Gate", frozen.stderr)

        reopened = self.run_ctl(
            "gate", "reopen", "idea_freeze", "--reason", "Idea changed"
        )
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        first_path.write_text("# Attempted in-place v2\n", encoding="utf-8")
        reused = self.run_ctl(
            "artifact",
            "register",
            "idea_card",
            "--stage",
            "idea",
            "--path",
            str(first_path),
            "--artifact-id",
            "IDEA-CARD-001",
            "--version",
            "2",
            "--status",
            "approval-ready",
        )
        self.assertEqual(reused.returncode, 2)
        self.assertIn("new version at a new path", reused.stderr)
        first_path.write_text("# Idea v1\n", encoding="utf-8")
        second_path = self.project / ".research/artifacts/idea/idea-card-v2.md"
        self.register_artifact(
            "idea.idea_card",
            artifact_id="IDEA-CARD-001",
            version="2",
            path=second_path,
            content="# Idea v2\n",
        )
        approved_v2 = self.run_ctl(
            "gate", "approve", "idea_freeze", "--reason", "Approve v2"
        )
        self.assertEqual(approved_v2.returncode, 0, approved_v2.stderr)
        history = self.load_state()["gates"]["idea_freeze"]["history"]
        latest_reference = next(
            reference
            for reference in history[-1]["artifact_refs"]
            if reference["artifact_id"] == "IDEA-CARD-001"
        )
        self.assertEqual(first_reference["version"], "1")
        self.assertEqual(
            first_reference["path"],
            ".research/artifacts/idea/idea-card-v1.md",
        )
        self.assertEqual(latest_reference["version"], "2")
        self.assertEqual(
            latest_reference["path"],
            ".research/artifacts/idea/idea-card-v2.md",
        )

        first_path.write_text("overwrote approved v1\n", encoding="utf-8")
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        self.assertIn("historical Gate artifact is no longer verifiable", doctor.stdout)
        self.assertIn("hash mismatch", doctor.stdout)
        recoverable = self.run_ctl(
            "gate", "reopen", "idea_freeze", "--reason", "Approved file changed"
        )
        self.assertEqual(recoverable.returncode, 0, recoverable.stderr)

        third_path = self.project / ".research/artifacts/idea/idea-card-v3.md"
        self.register_artifact(
            "idea.idea_card",
            artifact_id="IDEA-CARD-001",
            version="3",
            path=third_path,
            content="# Idea v3 after historical loss\n",
        )
        approved_v3 = self.run_ctl(
            "gate", "approve", "idea_freeze", "--reason", "Approve recoverable v3"
        )
        self.assertEqual(approved_v3.returncode, 0, approved_v3.stderr)
        audit = self.run_ctl("doctor")
        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
        self.assertIn("historical Gate artifact is no longer verifiable", audit.stdout)

    def test_gate_rejects_empty_reason_and_invalid_reopen(self) -> None:
        self.initialize()

        empty = self.run_ctl(
            "gate", "approve", "idea_freeze", "--reason", "   "
        )
        self.assertEqual(empty.returncode, 2)
        self.assertIn("non-empty --reason", empty.stderr)
        invalid = self.run_ctl(
            "gate", "reopen", "idea_freeze", "--reason", "Not approved yet"
        )
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("only be reopened from approved", invalid.stderr)

        out_of_order = self.run_ctl(
            "gate",
            "approve",
            "method_experiment_approval",
            "--reason",
            "Tried to skip the idea decision",
        )
        self.assertEqual(out_of_order.returncode, 2)
        self.assertIn("requires approved Gate idea_freeze", out_of_order.stderr)

    def test_checkpoint_rejects_empty_and_records_summary(self) -> None:
        self.initialize()

        result = self.run_ctl(
            "checkpoint", "--summary", "Literature search protocol registered"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        checkpoint = self.load_state()["last_checkpoint"]
        self.assertEqual(
            checkpoint["summary"], "Literature search protocol registered"
        )
        self.assertTrue(checkpoint["timestamp"].endswith("Z"))

        empty = self.run_ctl("checkpoint", "--summary", "")
        self.assertEqual(empty.returncode, 2)
        self.assertIn("non-empty --summary", empty.stderr)

    def test_checkpoint_can_make_only_policy_allowed_stage_transitions(self) -> None:
        self.initialize()

        moved = self.run_ctl(
            "checkpoint",
            "--summary",
            "Begin registered search",
            "--stage",
            "literature",
        )
        self.assertEqual(moved.returncode, 0, moved.stderr)
        state = self.load_state()
        self.assertEqual(state["current_stage"], "literature")
        self.assertEqual(state["stage_history"][-1]["trigger"], "checkpoint")

        blocked = self.run_ctl(
            "checkpoint", "--summary", "Skip evidence", "--stage", "paper"
        )
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("does not allow stage transition literature->paper", blocked.stderr)

        gate_blocked = self.run_ctl(
            "checkpoint", "--summary", "Enter method", "--stage", "method"
        )
        self.assertEqual(gate_blocked.returncode, 2)
        self.assertIn("requires approved Gate idea_freeze", gate_blocked.stderr)

    def test_release_target_is_derived_from_the_approved_stage(self) -> None:
        self.initialize()
        for gate in (
            "idea_freeze",
            "method_experiment_approval",
            "claim_freeze",
        ):
            self.register_gate_artifacts(gate)
            result = self.run_ctl(
                "gate", "approve", gate, "--reason", f"Approved evidence for {gate}"
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        self.register_gate_artifacts(
            "release", release_target="initial_submission"
        )
        initial = self.run_ctl(
            "gate", "approve", "release", "--reason", "Submission package verified"
        )
        self.assertEqual(initial.returncode, 0, initial.stderr)
        release = self.load_state()["gates"]["release"]
        self.assertEqual(release["history"][-1]["release_target"], "initial_submission")
        self.assertEqual(self.load_state()["current_stage"], "revision")

        duplicate = self.run_ctl(
            "gate", "approve", "release", "--reason", "Duplicate approval"
        )
        self.assertEqual(duplicate.returncode, 2)
        self.assertIn("already approved", duplicate.stderr)

        reopened_initial = self.run_ctl(
            "gate", "reopen", "release", "--reason", "Reviewer revision required"
        )
        self.assertEqual(reopened_initial.returncode, 0, reopened_initial.stderr)
        self.assertEqual(self.load_state()["current_stage"], "revision")

        self.register_gate_artifacts(
            "release", release_target="revision_rebuttal"
        )
        rebuttal = self.run_ctl(
            "gate", "approve", "release", "--reason", "Rebuttal package verified"
        )
        self.assertEqual(rebuttal.returncode, 0, rebuttal.stderr)
        release = self.load_state()["gates"]["release"]
        self.assertEqual(
            release["history"][-1]["release_target"], "revision_rebuttal"
        )

        reopened_rebuttal = self.run_ctl(
            "gate", "reopen", "release", "--reason", "Final response changed"
        )
        self.assertEqual(reopened_rebuttal.returncode, 0, reopened_rebuttal.stderr)
        self.assertEqual(self.load_state()["current_stage"], "revision")

    def test_upstream_gate_must_be_reopened_in_reverse_order(self) -> None:
        self.initialize()
        for gate in ("idea_freeze", "method_experiment_approval"):
            self.register_gate_artifacts(gate)
            result = self.run_ctl(
                "gate", "approve", gate, "--reason", f"Approved {gate}"
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        blocked = self.run_ctl(
            "gate", "reopen", "idea_freeze", "--reason", "Idea changed"
        )
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("reopen method_experiment_approval first", blocked.stderr)

        method = self.run_ctl(
            "gate",
            "reopen",
            "method_experiment_approval",
            "--reason",
            "Method changed",
        )
        self.assertEqual(method.returncode, 0, method.stderr)
        self.assertEqual(self.load_state()["current_stage"], "method")
        idea = self.run_ctl(
            "gate", "reopen", "idea_freeze", "--reason", "Idea changed"
        )
        self.assertEqual(idea.returncode, 0, idea.stderr)
        self.assertEqual(self.load_state()["current_stage"], "idea")

    def test_doctor_distinguishes_missing_artifact_warning_from_state_error(self) -> None:
        self.initialize()
        state_path = self.project / ".research/state.json"
        state = self.load_state()
        state["artifacts"] = {"method_contract": ".research/method/missing.md"}
        state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")

        warning = self.run_ctl("doctor")
        self.assertEqual(warning.returncode, 0, warning.stdout + warning.stderr)
        self.assertIn("[WARNING] artifact pointer does not exist", warning.stdout)
        self.assertIn("doctor: 0 error(s), 1 warning(s)", warning.stdout)

        state["current_stage"] = "imaginary-stage"
        state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")
        error = self.run_ctl("doctor")
        self.assertEqual(error.returncode, 1)
        self.assertIn("[ERROR] unknown current_stage", error.stdout)

    def test_doctor_rejects_gated_stage_without_prerequisite_approval(self) -> None:
        self.initialize()
        state_path = self.project / ".research/state.json"
        state = self.load_state()
        state["current_stage"] = "method"
        state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")

        result = self.run_ctl("doctor")

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "current_stage 'method' requires approved Gate idea_freeze",
            result.stdout,
        )

    def test_doctor_warns_for_legacy_artifact_metadata_without_a_path(self) -> None:
        self.initialize()
        state_path = self.project / ".research/state.json"
        state = self.load_state()
        state["artifacts"] = {
            "idea_card": {"artifact_id": "IDEA-CARD-001", "version": 1}
        }
        state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")

        result = self.run_ctl("doctor")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "legacy artifact pointer: artifacts.idea_card is not a valid artifact path",
            result.stdout,
        )

    def test_doctor_reports_missing_clone_local_exclusion(self) -> None:
        self.initialize()
        exclude_path = subprocess.run(
            ["git", "-C", str(self.project), "rev-parse", "--git-path", "info/exclude"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        exclude = Path(exclude_path)
        if not exclude.is_absolute():
            exclude = self.project / exclude
        lines = [
            line
            for line in exclude.read_text(encoding="utf-8").splitlines()
            if line.strip() != ".research/"
        ]
        exclude.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = self.run_ctl("doctor")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("not present in this clone's Git info/exclude", result.stdout)

    def test_doctor_rejects_unrecorded_ungated_stage_change(self) -> None:
        self.initialize()
        state_path = self.project / ".research/state.json"
        state = self.load_state()
        state["current_stage"] = "literature"
        state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")

        result = self.run_ctl("doctor")

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "current_stage does not match the final recorded stage transition",
            result.stdout,
        )

    def test_legacy_state_is_conservatively_migrated_and_preserved(self) -> None:
        research = self.project / ".research"
        research.mkdir()
        legacy = research / "project-state.yaml"
        legacy.write_text(
            "schema_version: 1\n"
            "project_id: PROJECT-LEGACY\n"
            "title: Legacy Study\n"
            "current_stage: result\n"
            "gates:\n"
            "  idea_freeze:\n"
            "    status: approved\n",
            encoding="utf-8",
        )

        result = self.run_ctl("init")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(legacy.is_file())
        self.assertIn("legacy Gate approvals were intentionally not migrated", result.stderr)
        state = self.load_state()
        self.assertEqual(state["project_id"], "PROJECT-LEGACY")
        self.assertEqual(state["project_name"], "Legacy Study")
        self.assertEqual(state["current_stage"], "idea")
        self.assertIn("was not migrated because its Gate approvals", result.stderr)
        self.assertTrue(
            all(gate["status"] == "pending" for gate in state["gates"].values())
        )

        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        self.assertIn("[WARNING] legacy state retained", doctor.stdout)

    def test_unparseable_legacy_is_reported_but_never_deleted(self) -> None:
        research = self.project / ".research"
        research.mkdir()
        legacy = research / "project-state.yaml"
        legacy.write_text("::: not a recognized legacy state :::\n", encoding="utf-8")

        result = self.run_ctl("init")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(legacy.is_file())
        self.assertTrue((research / "state.json").is_file())
        self.assertIn("not safely parseable", result.stderr)


if __name__ == "__main__":
    unittest.main()
