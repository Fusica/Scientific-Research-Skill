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
        "workflow_version": "1.0.0-test",
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
        "state_contract": {},
        "gates": {
            "idea_freeze": {"advance_to": "method", "reopen_to": "idea"},
            "method_experiment_approval": {
                "advance_to": "experiment_results",
                "reopen_to": "method",
            },
            "claim_freeze": {
                "advance_to": "paper",
                "reopen_to": "experiment_results",
            },
            "release": {
                "advance_to": "revision",
                "release_targets": ["initial_submission", "revision_rebuttal"],
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

    def test_init_is_idempotent_preserves_memory_and_sets_local_exclude(self) -> None:
        original_state = self.initialize()
        memory = self.project / ".research/memory.md"
        memory.write_text("personal project memory\n", encoding="utf-8")

        second = self.run_ctl("init")

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("state already exists; left unchanged", second.stdout)
        self.assertIn("memory already exists; left unchanged", second.stdout)
        self.assertEqual(self.load_state(), original_state)
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

    def test_gate_decisions_keep_history_and_use_policy_stage_advance(self) -> None:
        self.initialize()
        artifact = self.project / "artifacts/idea-card.md"
        artifact.parent.mkdir()
        artifact.write_text("# Frozen idea\n", encoding="utf-8")
        state_path = self.project / ".research/state.json"
        state = self.load_state()
        state["artifacts"] = {
            "idea_card": {
                "path": "artifacts/idea-card.md",
                "artifact_id": "IDEA-CARD-001",
                "version": 1,
                "content_hash": "sha256:test",
                "status": "approval-ready",
            }
        }
        state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")

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
            decision["artifact_refs"],
            [
                {
                    "label": "artifacts.idea_card",
                    "path": "artifacts/idea-card.md",
                    "artifact_id": "IDEA-CARD-001",
                    "version": 1,
                    "content_hash": "sha256:test",
                    "status": "approval-ready",
                }
            ],
        )
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
        self.assertEqual(self.load_state()["current_stage"], "idea")

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
            result = self.run_ctl(
                "gate", "approve", gate, "--reason", f"Approved evidence for {gate}"
            )
            self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_doctor_rejects_artifact_metadata_without_a_path(self) -> None:
        self.initialize()
        state_path = self.project / ".research/state.json"
        state = self.load_state()
        state["artifacts"] = {
            "idea_card": {"artifact_id": "IDEA-CARD-001", "version": 1}
        }
        state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")

        result = self.run_ctl("doctor")

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "artifacts.idea_card is not a valid artifact path", result.stdout
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
