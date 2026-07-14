from __future__ import annotations

import hashlib
import errno
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts import researchctl as researchctl_module


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
            "required_fields": [
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
            ],
            "stage_ids": [
                "idea",
                "literature",
                "method",
                "experiment_results",
                "paper",
                "revision",
            ],
            "gate_ids": [
                "idea_freeze",
                "method_experiment_approval",
                "claim_freeze",
                "release",
            ],
            "gate_statuses": ["pending", "approved", "reopened"],
            "gate_actions": ["approve", "reopen"],
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
        "review_language": {
            "internal_review_default": "zh-CN",
            "formal_output_default": "en",
            "instruction": (
                ".research 中需要人工审核或维护的中间产物、memory、checkpoint summary "
                "和 Gate reason 采用中文骨干；论文、返修回复、代码及注释保持英文；"
                "JSON/YAML 字段、ID、枚举、路径、命令、公式、原始引文、书目信息与原始日志"
                "保持英文或原文。"
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
        return subprocess.run(
            [sys.executable, str(RESEARCHCTL), *arguments],
            cwd=cwd or self.project,
            env=self.ctl_environment(),
            check=False,
            capture_output=True,
            text=True,
        )

    def ctl_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment["RESEARCHCTL_POLICY"] = str(self.policy)
        environment["RESEARCHCTL_ACTOR"] = "unit-test-researcher"
        return environment

    def load_state(self) -> dict[str, object]:
        return json.loads(
            (self.project / ".research/state.json").read_text(encoding="utf-8")
        )

    def write_state(self, state: dict[str, object]) -> None:
        (self.project / ".research/state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
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
        generated_memory = memory.read_text(encoding="utf-8")
        self.assertIn(f"# 研究记忆：{self.project.name}", generated_memory)
        self.assertIn("## 已验证事实", generated_memory)
        self.assertIn("## 失败尝试与经验", generated_memory)
        self.assertNotIn("## Verified Facts", generated_memory)
        state_text = (self.project / ".research/state.json").read_text(encoding="utf-8")
        self.assertIn('"schema_version"', state_text)
        self.assertIn('"current_stage": "idea"', state_text)
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

    def test_enable_rejects_a_structurally_invalid_disabled_state(self) -> None:
        self.initialize()
        self.assertEqual(self.run_ctl("disable").returncode, 0)
        state = self.load_state()
        state["current_stage"] = "forged"
        self.write_state(state)

        result = self.run_ctl("enable")
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot be enabled", result.stderr)
        self.assertIs(self.load_state()["enabled"], False)

    def test_disable_remains_available_when_timestamp_cannot_advance(self) -> None:
        self.initialize()
        state = self.load_state()
        state["updated_at"] = "9999-12-31T23:59:59.999999Z"
        self.write_state(state)

        result = self.run_ctl("disable")
        self.assertEqual(result.returncode, 0, result.stderr)
        disabled = self.load_state()
        self.assertIs(disabled["enabled"], False)
        self.assertEqual(disabled["updated_at"], "9999-12-31T23:59:59.999999Z")

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
            "gate", "approve", "idea_freeze", "--reason", "证据包已完成审核"
        )
        self.assertEqual(approved.returncode, 0, approved.stderr)
        state = self.load_state()
        record = state["gates"]["idea_freeze"]
        self.assertEqual(record["status"], "approved")
        self.assertEqual(len(record["history"]), 1)
        decision = record["history"][0]
        self.assertEqual(decision["action"], "approve")
        self.assertEqual(decision["reason"], "证据包已完成审核")
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
            "checkpoint", "--summary", "文献检索协议已登记"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        checkpoint = self.load_state()["last_checkpoint"]
        self.assertEqual(
            checkpoint["summary"], "文献检索协议已登记"
        )
        self.assertTrue(checkpoint["timestamp"].endswith("Z"))
        state_text = (self.project / ".research/state.json").read_text(encoding="utf-8")
        self.assertIn("文献检索协议已登记", state_text)
        self.assertNotIn("\\u6587\\u732e", state_text)

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

    def test_checkpoint_cannot_advance_from_a_forged_gate_status(self) -> None:
        self.initialize()
        state = self.load_state()
        state["gates"]["idea_freeze"]["status"] = "approved"
        self.write_state(state)

        result = self.run_ctl(
            "checkpoint", "--summary", "伪造门禁推进", "--stage", "method"
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("without history must be pending", result.stderr)
        self.assertEqual(self.load_state()["current_stage"], "idea")

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

    def test_concurrent_artifact_registrations_preserve_every_successful_update(
        self,
    ) -> None:
        self.initialize()
        processes: list[subprocess.Popen[str]] = []
        expected_roles: set[str] = set()
        for index in range(12):
            role = f"stress_role_{index}"
            expected_roles.add(role)
            artifact = (
                self.project
                / ".research"
                / "artifacts"
                / "idea"
                / f"stress-{index}.md"
            )
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(f"# Concurrent artifact {index}\n", encoding="utf-8")
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        str(RESEARCHCTL),
                        "artifact",
                        "register",
                        role,
                        "--stage",
                        "idea",
                        "--path",
                        str(artifact),
                        "--artifact-id",
                        f"STRESS-{index}",
                        "--version",
                        "1",
                    ],
                    cwd=self.project,
                    env=self.ctl_environment(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )

        outcomes = [process.communicate(timeout=20) for process in processes]
        for process, (stdout, stderr) in zip(processes, outcomes):
            self.assertEqual(process.returncode, 0, stdout + stderr)
        self.assertEqual(
            set(self.load_state()["artifacts"]["idea"]), expected_roles
        )
        self.assertEqual(
            list((self.project / ".research").glob(".state.json.*.tmp")), []
        )
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

    def test_concurrent_init_observes_one_project_identity(self) -> None:
        processes = [
            subprocess.Popen(
                [sys.executable, str(RESEARCHCTL), "init"],
                cwd=self.project,
                env=self.ctl_environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(8)
        ]
        observed_ids: set[str] = set()
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            match = re.search(r"PROJECT-[A-Z0-9]+", stdout)
            self.assertIsNotNone(match, stdout)
            assert match is not None
            observed_ids.add(match.group(0))
        self.assertEqual(observed_ids, {self.load_state()["project_id"]})
        self.assertTrue((self.project / ".research/state.lock").is_file())

    def test_failed_init_never_activates_a_partial_project(self) -> None:
        research = self.project / ".research"
        research.mkdir()
        artifact_root = research / "artifacts"
        artifact_root.write_text("blocks directory creation\n", encoding="utf-8")

        failed = self.run_ctl("init")

        self.assertEqual(failed.returncode, 2)
        self.assertFalse((research / "state.json").exists())
        self.assertTrue((research / "memory.md").is_file())
        artifact_root.unlink()
        recovered = self.run_ctl("init")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertTrue((research / "state.json").is_file())
        self.assertEqual(self.run_ctl("doctor").returncode, 0)

    def test_checkpoint_same_stage_is_idempotent(self) -> None:
        self.initialize()
        result = self.run_ctl(
            "checkpoint",
            "--summary",
            "保持当前想法阶段，仅更新恢复点",
            "--stage",
            "idea",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.load_state()
        self.assertEqual(state["current_stage"], "idea")
        self.assertEqual(state["stage_history"], [])
        self.assertEqual(
            state["last_checkpoint"]["summary"], "保持当前想法阶段，仅更新恢复点"
        )

    def test_reopening_gate_never_advances_from_an_earlier_stage(self) -> None:
        self.initialize()
        for gate in (
            "idea_freeze",
            "method_experiment_approval",
            "claim_freeze",
        ):
            self.register_gate_artifacts(gate)
            approved = self.run_ctl(
                "gate", "approve", gate, "--reason", f"批准 {gate}"
            )
            self.assertEqual(approved.returncode, 0, approved.stderr)
        back = self.run_ctl(
            "checkpoint", "--summary", "回到文献阶段复核", "--stage", "literature"
        )
        self.assertEqual(back.returncode, 0, back.stderr)

        reopened = self.run_ctl(
            "gate", "reopen", "claim_freeze", "--reason", "重新检查主张依据"
        )

        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        state = self.load_state()
        self.assertEqual(state["current_stage"], "literature")
        self.assertNotEqual(
            state["stage_history"][-1]["to_stage"], "experiment_results"
        )
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

    def test_approved_artifact_identity_cannot_be_reused_for_new_content(
        self,
    ) -> None:
        self.initialize()
        first = self.project / ".research/artifacts/idea/identity-v1.md"
        self.register_artifact(
            "idea.idea_card",
            artifact_id="IDEA-CARD-IDENTITY",
            version="1",
            path=first,
            content="# Identity v1\n",
        )
        self.register_artifact("literature.evidence_base")
        self.assertEqual(
            self.run_ctl(
                "gate", "approve", "idea_freeze", "--reason", "批准版本一"
            ).returncode,
            0,
        )
        self.assertEqual(
            self.run_ctl(
                "gate", "reopen", "idea_freeze", "--reason", "准备版本二"
            ).returncode,
            0,
        )
        second = self.project / ".research/artifacts/idea/identity-v2.md"
        self.register_artifact(
            "idea.idea_card",
            artifact_id="IDEA-CARD-IDENTITY",
            version="2",
            path=second,
            content="# Identity v2\n",
        )

        reused = self.project / ".research/artifacts/idea/reused-v1.md"
        reused.write_text("# Different object claiming to be v1\n", encoding="utf-8")
        result = self.run_ctl(
            "artifact",
            "register",
            "idea_card",
            "--stage",
            "idea",
            "--path",
            str(reused),
            "--artifact-id",
            "IDEA-CARD-IDENTITY",
            "--version",
            "1",
            "--status",
            "approval-ready",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("already bound to different metadata", result.stderr)
        self.assertEqual(
            self.load_state()["artifacts"]["idea"]["idea_card"]
            ["IDEA-CARD-IDENTITY"]["version"],
            "2",
        )

    def test_all_artifact_pointer_field_names_are_reserved_as_ids(self) -> None:
        self.initialize()
        artifact = self.project / ".research/artifacts/idea/reserved.md"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("reserved\n", encoding="utf-8")
        for artifact_id in (
            "path",
            "artifact_id",
            "version",
            "content_hash",
            "status",
        ):
            with self.subTest(artifact_id=artifact_id):
                reserved = self.run_ctl(
                    "artifact",
                    "register",
                    "idea_card",
                    "--stage",
                    "idea",
                    "--path",
                    str(artifact),
                    "--artifact-id",
                    artifact_id,
                    "--version",
                    "1",
                )
                self.assertEqual(reserved.returncode, 2)
                self.assertIn("reserved", reserved.stderr)

    def test_current_control_file_hardlinks_are_rejected_as_evidence(self) -> None:
        self.initialize()
        alias = self.project / ".research/artifacts/idea/state-alias.json"
        alias.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(self.project / ".research/state.json", alias)
        except OSError as exc:  # pragma: no cover - filesystem capability
            unsupported = {
                errno.EPERM,
                errno.EXDEV,
                errno.ENOSYS,
                getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
            }
            if exc.errno in unsupported:
                self.skipTest(f"hardlinks unavailable: {exc}")
            raise
        hardlink = self.run_ctl(
            "artifact",
            "register",
            "idea_card",
            "--stage",
            "idea",
            "--path",
            str(alias),
            "--artifact-id",
            "STATE-ALIAS",
            "--version",
            "1",
        )
        self.assertEqual(hardlink.returncode, 2)
        self.assertIn("control metadata cannot be registered", hardlink.stderr)

    def test_doctor_rejects_artifact_mapping_key_mismatch(self) -> None:
        self.initialize()
        pointer = self.register_artifact(
            "idea.idea_card", artifact_id="IDEA-CARD-001"
        )
        state = self.load_state()
        bucket = state["artifacts"]["idea"]["idea_card"]
        del bucket["IDEA-CARD-001"]
        bucket["WRONG-KEY"] = pointer
        self.write_state(state)

        result = self.run_ctl("doctor")

        self.assertEqual(result.returncode, 1)
        self.assertIn("must match its artifact-ID mapping key", result.stdout)

    def test_dotted_artifact_ids_are_not_interpreted_as_nested_registry_keys(
        self,
    ) -> None:
        self.initialize()
        pointer = self.register_artifact(
            "idea.idea_card", artifact_id="IDEA.CARD:001"
        )
        self.register_artifact("literature.evidence_base")
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        approved = self.run_ctl(
            "gate", "approve", "idea_freeze", "--reason", "批准带点号的稳定 ID"
        )
        self.assertEqual(approved.returncode, 0, approved.stderr)

        reopened = self.run_ctl(
            "gate", "reopen", "idea_freeze", "--reason", "构造嵌套键反例"
        )
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        state = self.load_state()
        state["artifacts"]["idea"]["idea_card"] = {
            "IDEA": {"CARD:001": pointer}
        }
        self.write_state(state)
        forged = self.run_ctl("doctor")
        self.assertEqual(forged.returncode, 1)
        self.assertIn("unknown fields", forged.stdout)

    def test_artifact_identity_is_global_across_current_roles(self) -> None:
        self.initialize()
        source = self.project / ".research/artifacts/idea/shared.md"
        first = self.register_artifact(
            "idea.idea_card",
            artifact_id="SHARED-001",
            version="1",
            path=source,
            content="shared immutable object\n",
        )
        same = self.run_ctl(
            "artifact", "register", "alternate_card", "--stage", "idea",
            "--path", str(source), "--artifact-id", "SHARED-001", "--version", "1",
            "--status", "approval-ready",
        )
        self.assertEqual(same.returncode, 0, same.stderr)

        other = self.project / ".research/artifacts/idea/shared-forged.md"
        other.write_text("different object\n", encoding="utf-8")
        conflict = self.run_ctl(
            "artifact", "register", "third_card", "--stage", "idea",
            "--path", str(other), "--artifact-id", "SHARED-001", "--version", "1",
            "--status", "approval-ready",
        )
        self.assertEqual(conflict.returncode, 2)
        self.assertIn("already bound to different metadata", conflict.stderr)
        self.assertEqual(
            self.load_state()["artifacts"]["idea"]["alternate_card"]["SHARED-001"],
            first,
        )

    def test_doctor_rejects_approved_gate_current_artifact_drift(self) -> None:
        self.initialize()
        self.register_gate_artifacts("idea_freeze")
        approved = self.run_ctl(
            "gate", "approve", "idea_freeze", "--reason", "批准当前证据"
        )
        self.assertEqual(approved.returncode, 0, approved.stderr)

        replacement = self.project / ".research/artifacts/idea/unapproved-v2.md"
        replacement.write_text("# Unapproved replacement\n", encoding="utf-8")
        state = self.load_state()
        pointer = next(
            iter(state["artifacts"]["idea"]["idea_card"].values())
        )
        pointer.update(
            {
                "path": ".research/artifacts/idea/unapproved-v2.md",
                "content_hash": "sha256:"
                + hashlib.sha256(replacement.read_bytes()).hexdigest(),
            }
        )
        self.write_state(state)

        result = self.run_ctl("doctor")

        self.assertEqual(result.returncode, 1)
        self.assertIn("current artifacts differ", result.stdout)
        recovered = self.run_ctl(
            "gate", "reopen", "idea_freeze", "--reason", "发现未批准的当前指针漂移"
        )
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(
            self.load_state()["gates"]["idea_freeze"]["status"], "reopened"
        )
        repaired = self.run_ctl("doctor")
        self.assertEqual(repaired.returncode, 0, repaired.stdout + repaired.stderr)
        self.assertIn("register a new version", repaired.stdout)
        registered = self.run_ctl(
            "artifact", "register", "idea_card", "--stage", "idea",
            "--path", str(replacement), "--artifact-id", str(pointer["artifact_id"]),
            "--version", "2",
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        final = self.run_ctl("doctor")
        self.assertEqual(final.returncode, 0, final.stdout + final.stderr)
        self.assertNotIn("register a new version", final.stdout)

    def test_reopen_recovers_upstream_drift_in_reverse_gate_order(self) -> None:
        self.initialize()
        self.register_gate_artifacts("idea_freeze")
        self.assertEqual(
            self.run_ctl("gate", "approve", "idea_freeze", "--reason", "批准想法").returncode,
            0,
        )
        self.register_gate_artifacts("method_experiment_approval")
        self.assertEqual(
            self.run_ctl(
                "gate", "approve", "method_experiment_approval", "--reason", "批准实验"
            ).returncode,
            0,
        )
        state = self.load_state()
        pointer = next(iter(state["artifacts"]["idea"]["idea_card"].values()))
        drifted = self.project / ".research/artifacts/idea/drifted-same-version.md"
        drifted.write_text("drifted same version\n", encoding="utf-8")
        pointer["path"] = ".research/artifacts/idea/drifted-same-version.md"
        pointer["content_hash"] = "sha256:" + hashlib.sha256(drifted.read_bytes()).hexdigest()
        self.write_state(state)

        wrong_order = self.run_ctl(
            "gate", "reopen", "idea_freeze", "--reason", "尝试逆序恢复"
        )
        self.assertEqual(wrong_order.returncode, 2)
        self.assertIn("downstream Gate", wrong_order.stderr)
        downstream = self.run_ctl(
            "gate", "reopen", "method_experiment_approval", "--reason", "先重开下游"
        )
        self.assertEqual(downstream.returncode, 0, downstream.stderr)
        upstream = self.run_ctl(
            "gate", "reopen", "idea_freeze", "--reason", "再重开上游"
        )
        self.assertEqual(upstream.returncode, 0, upstream.stderr)
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

    def test_doctor_rejects_impossible_gate_state_machine_histories(self) -> None:
        baseline = self.initialize()

        def decision(
            identifier: str, action: str, previous: str, new: str
        ) -> dict[str, object]:
            return {
                "decision_id": identifier,
                "action": action,
                "previous_status": previous,
                "new_status": new,
                "reason": "构造状态机反例",
                "actor": "unit-test-researcher",
                "decided_at": "2026-07-14T00:00:00Z",
                "artifact_refs": [],
            }

        cases = {
            "reopen-from-pending": [
                decision("DEC-BAD-1", "reopen", "pending", "approved")
            ],
            "approve-to-reopened": [
                decision("DEC-BAD-2", "approve", "pending", "reopened")
            ],
            "broken-chain": [
                decision("DEC-BAD-3A", "approve", "pending", "approved"),
                decision("DEC-BAD-3B", "approve", "pending", "approved"),
            ],
        }
        malformed_ref = decision("DEC-BAD-4", "approve", "pending", "approved")
        malformed_ref["artifact_refs"] = ["not-a-pointer"]
        cases["malformed-artifact-ref"] = [malformed_ref]
        for label, history in cases.items():
            with self.subTest(label=label):
                state = json.loads(json.dumps(baseline))
                record = state["gates"]["idea_freeze"]
                record["history"] = history
                record["status"] = history[-1]["new_status"]
                record["latest_decision_id"] = history[-1]["decision_id"]
                self.write_state(state)
                result = self.run_ctl("doctor")
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_gate_type_fuzz_is_reported_without_tracebacks(self) -> None:
        self.initialize()
        self.register_gate_artifacts("idea_freeze")
        self.assertEqual(
            self.run_ctl("gate", "approve", "idea_freeze", "--reason", "建立合法基线").returncode,
            0,
        )
        baseline = self.load_state()
        cases = {
            "status-list": ("status", []),
            "action-list": ("action", []),
            "previous-object": ("previous_status", {}),
            "new-list": ("new_status", []),
        }
        for label, (field, value) in cases.items():
            with self.subTest(label=label):
                state = json.loads(json.dumps(baseline))
                record = state["gates"]["idea_freeze"]
                if field == "status":
                    record[field] = value
                else:
                    record["history"][0][field] = value
                self.write_state(state)
                result = self.run_ctl("doctor")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_doctor_rejects_gate_history_refs_from_unrequired_roles(self) -> None:
        self.initialize()
        self.register_gate_artifacts("idea_freeze")
        self.assertEqual(
            self.run_ctl("gate", "approve", "idea_freeze", "--reason", "批准正确证据").returncode,
            0,
        )
        self.assertEqual(
            self.run_ctl("gate", "reopen", "idea_freeze", "--reason", "准备伪造引用").returncode,
            0,
        )
        fake = self.project / ".research/artifacts/method/fake.md"
        fake.parent.mkdir(parents=True, exist_ok=True)
        fake.write_text("structurally valid but irrelevant\n", encoding="utf-8")
        reference = {
            "label": "artifacts.method.not_required.FAKE",
            "path": ".research/artifacts/method/fake.md",
            "artifact_id": "FAKE",
            "version": "1",
            "content_hash": "sha256:" + hashlib.sha256(fake.read_bytes()).hexdigest(),
            "status": "current",
        }
        state = self.load_state()
        history = state["gates"]["idea_freeze"]["history"]
        history[0]["artifact_refs"] = [reference]
        history[1]["artifact_refs"] = [reference]
        self.write_state(state)

        result = self.run_ctl("doctor")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unexpected roles", result.stdout)
        self.assertIn("missing required roles", result.stdout)

    def test_doctor_rejects_approved_downstream_gate_without_prerequisites(self) -> None:
        self.initialize()
        state = self.load_state()
        decision = {
            "decision_id": "DEC-FORGED-METHOD",
            "action": "approve",
            "previous_status": "pending",
            "new_status": "approved",
            "reason": "伪造的下游批准",
            "actor": "unit-test-researcher",
            "decided_at": state["updated_at"],
            "artifact_refs": [],
        }
        state["gates"]["method_experiment_approval"] = {
            "status": "approved",
            "latest_decision_id": decision["decision_id"],
            "history": [decision],
        }
        self.write_state(state)

        result = self.run_ctl("doctor")

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "approved Gate method_experiment_approval requires approved Gate idea_freeze",
            result.stdout,
        )

    def test_doctor_replays_prerequisite_status_at_each_historical_approval(
        self,
    ) -> None:
        self.initialize()
        self.register_gate_artifacts("idea_freeze")
        self.assertEqual(
            self.run_ctl("gate", "approve", "idea_freeze", "--reason", "批准想法").returncode,
            0,
        )
        self.assertEqual(
            self.run_ctl("gate", "reopen", "idea_freeze", "--reason", "撤回想法批准").returncode,
            0,
        )
        state = self.load_state()
        base = datetime.fromisoformat(str(state["updated_at"]).replace("Z", "+00:00"))

        def forged(identifier: str, action: str, previous: str, new: str, offset: int) -> dict[str, object]:
            return {
                "decision_id": identifier,
                "action": action,
                "previous_status": previous,
                "new_status": new,
                "reason": "构造历史前置门禁反例",
                "actor": "unit-test-researcher",
                "decided_at": (base + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z"),
                "artifact_refs": [],
            }

        method_history = [
            forged("DEC-FORGED-METHOD-APPROVE", "approve", "pending", "approved", 1),
            forged("DEC-FORGED-METHOD-REOPEN", "reopen", "approved", "reopened", 2),
        ]
        state["gates"]["method_experiment_approval"] = {
            "status": "reopened",
            "latest_decision_id": "DEC-FORGED-METHOD-REOPEN",
            "history": method_history,
        }
        state["updated_at"] = (base + timedelta(seconds=3)).isoformat().replace("+00:00", "Z")
        self.write_state(state)

        result = self.run_ctl("doctor")
        self.assertEqual(result.returncode, 1)
        self.assertIn("approval lacks a prior approval", result.stdout)

    def test_doctor_requires_utc_and_monotonic_timestamps(self) -> None:
        baseline = self.initialize()
        mutations = {
            "naive": {"created_at": "2026-07-14T00:00:00"},
            "non-utc": {"updated_at": "2026-07-14T08:00:00+08:00"},
            "reverse": {
                "created_at": "2026-07-14T00:00:01Z",
                "updated_at": "2026-07-14T00:00:00Z",
            },
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label):
                state = json.loads(json.dumps(baseline))
                state.update(mutation)
                self.write_state(state)
                result = self.run_ctl("doctor")
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_doctor_bounds_all_recorded_events_by_state_lifetime(self) -> None:
        self.initialize()
        state = self.load_state()
        state["last_checkpoint"] = {
            "summary": "伪造未来检查点",
            "timestamp": "2099-01-01T00:00:00Z",
        }
        self.write_state(state)

        result = self.run_ctl("doctor")
        self.assertEqual(result.returncode, 1)
        self.assertIn("must not be later than updated_at", result.stdout)

    def test_mutations_use_strictly_increasing_timestamps(self) -> None:
        self.initialize()
        first = self.run_ctl("checkpoint", "--summary", "第一次快速检查点")
        self.assertEqual(first.returncode, 0, first.stderr)
        first_state = self.load_state()
        second = self.run_ctl("checkpoint", "--summary", "第二次快速检查点")
        self.assertEqual(second.returncode, 0, second.stderr)
        second_state = self.load_state()

        parse = lambda value: datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        self.assertLess(parse(first_state["last_checkpoint"]["timestamp"]), parse(first_state["updated_at"]))
        self.assertLess(parse(first_state["updated_at"]), parse(second_state["last_checkpoint"]["timestamp"]))
        self.assertLess(parse(second_state["last_checkpoint"]["timestamp"]), parse(second_state["updated_at"]))

    def test_datetime_exhaustion_fails_cleanly_without_traceback(self) -> None:
        self.initialize()
        state = self.load_state()
        state["updated_at"] = "9999-12-31T23:59:59.999999Z"
        self.write_state(state)

        result = self.run_ctl("checkpoint", "--summary", "无法推进的检查点")
        self.assertEqual(result.returncode, 2)
        self.assertIn("timestamps cannot advance", result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_hashing_rejects_a_file_that_changes_during_the_read(self) -> None:
        artifact = self.project / "changing.bin"
        artifact.write_bytes(b"stable bytes")
        actual = artifact.stat()
        before = SimpleNamespace(
            st_dev=actual.st_dev,
            st_ino=actual.st_ino,
            st_size=actual.st_size,
            st_mtime_ns=actual.st_mtime_ns,
            st_ctime_ns=actual.st_ctime_ns,
        )
        after = SimpleNamespace(**{**before.__dict__, "st_mtime_ns": before.st_mtime_ns + 1})
        with mock.patch.object(Path, "stat", side_effect=[before, after]):
            with self.assertRaisesRegex(
                researchctl_module.ResearchCtlError, "changed while it was being hashed"
            ):
                researchctl_module.sha256_file(artifact)

    def test_doctor_links_gate_stage_transitions_to_the_exact_decision(self) -> None:
        self.initialize()
        self.register_gate_artifacts("idea_freeze")
        approved = self.run_ctl(
            "gate", "approve", "idea_freeze", "--reason", "批准并推进阶段"
        )
        self.assertEqual(approved.returncode, 0, approved.stderr)
        state = self.load_state()
        state["stage_history"][0]["trigger"] = "gate:idea_freeze:DEC-NOT-REAL"
        self.write_state(state)

        result = self.run_ctl("doctor")

        self.assertEqual(result.returncode, 1)
        self.assertIn("references an unknown Gate decision", result.stdout)

    def test_corrupt_state_inputs_fail_cleanly_without_tracebacks(self) -> None:
        self.initialize()
        state_path = self.project / ".research/state.json"
        payloads = {
            "invalid-utf8": b"\xff\xfe",
            "truncated-json": b'{"enabled": true',
            "excessive-nesting": (
                b'{"nested":' + (b"[" * 1800) + b"0" + (b"]" * 1800) + b"}"
            ),
        }
        for label, payload in payloads.items():
            with self.subTest(label=label):
                state_path.write_bytes(payload)
                result = self.run_ctl("doctor")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_nul_artifact_path_is_reported_without_a_traceback(self) -> None:
        self.initialize()
        state = self.load_state()
        state["artifacts"] = {
            "idea": {
                "idea_card": {
                    "IDEA-CARD-NUL": {
                        "path": "bad\u0000path",
                        "artifact_id": "IDEA-CARD-NUL",
                        "version": "1",
                        "content_hash": "sha256:" + ("0" * 64),
                        "status": "draft",
                    }
                }
            }
        }
        self.write_state(state)

        result = self.run_ctl("doctor")

        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot be resolved", result.stdout)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_doctor_requires_artifact_workspace_and_init_reports_disabled_truthfully(
        self,
    ) -> None:
        self.initialize()
        disabled = self.run_ctl("disable")
        self.assertEqual(disabled.returncode, 0, disabled.stderr)
        repeated = self.run_ctl("init")
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertIn("remains disabled", repeated.stdout)
        self.assertNotIn("workflow enabled for", repeated.stdout)

        (self.project / ".research/artifacts").rmdir()
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 1)
        self.assertIn("missing artifact workspace", doctor.stdout)

    def test_policy_state_contract_drift_fails_closed(self) -> None:
        self.initialize()
        for field, replacement in (
            ("required_fields", []),
            ("stage_ids", ["idea"]),
            ("gate_ids", ["idea_freeze"]),
            ("gate_statuses", ["pending", "approved", "reopened", "forged"]),
            ("gate_actions", ["approve"]),
            ("artifact_pointer_fields", ["path", "artifact_id"]),
        ):
            with self.subTest(field=field):
                policy = policy_document()
                policy["state_contract"][field] = replacement
                self.policy.write_text(
                    json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                result = self.run_ctl("doctor")
                self.assertEqual(result.returncode, 2)
                self.assertIn(f"state_contract.{field}", result.stderr)

    def test_unicode_project_without_git_initializes_and_audits(self) -> None:
        project = Path(self.temporary.name) / "无人机-研究"
        project.mkdir()
        result = self.run_ctl("init", cwd=project)
        self.assertEqual(result.returncode, 0, result.stderr)
        memory = (project / ".research/memory.md").read_text(encoding="utf-8")
        self.assertIn("# 研究记忆：无人机-研究", memory)
        doctor = self.run_ctl("doctor", cwd=project)
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        self.assertIn("Git worktree not detected", doctor.stdout)

    def test_shipped_policy_full_two_release_flow(self) -> None:
        self.policy = ROOT / "skills/research/references/policy.yaml"
        self.initialize()
        for gate in (
            "idea_freeze",
            "method_experiment_approval",
            "claim_freeze",
        ):
            self.register_gate_artifacts(gate)
            approved = self.run_ctl(
                "gate", "approve", gate, "--reason", f"真实策略审查通过：{gate}"
            )
            self.assertEqual(approved.returncode, 0, approved.stderr)

        self.register_gate_artifacts(
            "release", release_target="initial_submission"
        )
        initial = self.run_ctl(
            "gate", "approve", "release", "--reason", "初投稿发布包已人工审核"
        )
        self.assertEqual(initial.returncode, 0, initial.stderr)
        reopened = self.run_ctl(
            "gate", "reopen", "release", "--reason", "收到审稿意见，进入返修"
        )
        self.assertEqual(reopened.returncode, 0, reopened.stderr)

        self.register_gate_artifacts(
            "release", release_target="revision_rebuttal"
        )
        revision = self.run_ctl(
            "gate", "approve", "release", "--reason", "返修与回复发布包已人工审核"
        )
        self.assertEqual(revision.returncode, 0, revision.stderr)
        state = self.load_state()
        release_history = state["gates"]["release"]["history"]
        self.assertEqual(
            [entry["release_target"] for entry in release_history],
            ["initial_submission", "initial_submission", "revision_rebuttal"],
        )
        self.assertEqual(len(list(self.project.rglob("*.md"))), 26)
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

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
