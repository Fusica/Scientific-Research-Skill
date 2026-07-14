from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from scripts import validate_repo as validate_repo_module


ROOT = Path(__file__).resolve().parents[1]


class RepositoryTest(unittest.TestCase):
    def run_python(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, *args],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def validate_at(self, root: Path) -> list[str]:
        with mock.patch.object(validate_repo_module, "ROOT", root):
            return (
                validate_repo_module.validate_skill()
                + validate_repo_module.validate_plugin()
            )

    def test_repository_validator(self) -> None:
        result = self.run_python("scripts/validate_repo.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("one Skill, six stages, four Gates", result.stdout)

    def test_exactly_one_public_skill(self) -> None:
        skills = {
            path.name
            for path in (ROOT / "skills").iterdir()
            if path.is_dir() and (path / "SKILL.md").is_file()
        }
        self.assertEqual(skills, {"research"})
        metadata = yaml.safe_load(
            (ROOT / "skills/research/agents/openai.yaml").read_text(encoding="utf-8")
        )
        self.assertIn("$research", metadata["interface"]["default_prompt"])

    def test_policy_is_json_compatible_and_canonical(self) -> None:
        policy = json.loads(
            (ROOT / "skills/research/references/policy.yaml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            policy["stage_order"],
            [
                "idea",
                "literature",
                "method",
                "experiment_results",
                "paper",
                "revision",
            ],
        )
        self.assertEqual(
            policy["gate_order"],
            [
                "idea_freeze",
                "method_experiment_approval",
                "claim_freeze",
                "release",
            ],
        )
        self.assertEqual(
            policy["artifact_layout"]["generated_root"], ".research/artifacts"
        )
        self.assertEqual(
            policy["artifact_layout"]["stage_path_template"],
            ".research/artifacts/<stage-id>",
        )
        self.assertEqual(
            policy["review_language"]["internal_review_default"], "zh-CN"
        )
        self.assertEqual(policy["review_language"]["formal_output_default"], "en")
        language_instruction = policy["review_language"]["instruction"]
        self.assertIn("论文、返修回复、代码及注释保持英文", language_instruction)
        self.assertIn("JSON/YAML 字段", language_instruction)
        self.assertEqual(policy["gates"]["idea_freeze"]["advance_to"], "method")
        self.assertEqual(
            policy["gates"]["method_experiment_approval"]["advance_to"],
            "experiment_results",
        )
        self.assertTrue(
            {
                "experiment_results.experiment_matrix",
                "experiment_results.run_registry",
                "experiment_results.decision_log",
                "experiment_results.analysis_registry",
                "experiment_results.artifact_manifest",
                "experiment_results.claim_ledger",
            }.issubset(policy["gates"]["claim_freeze"]["required_artifact_roles"])
        )
        release_roles = policy["gates"]["release"][
            "required_artifact_roles_by_target"
        ]
        self.assertIn("paper.rendered_output", release_roles["initial_submission"])
        self.assertTrue(
            {
                "revision.review_map",
                "revision.change_log",
                "revision.response_document",
                "revision.manuscript_diff",
                "revision.verification_records",
                "revision.rendered_output",
            }.issubset(release_roles["revision_rebuttal"])
        )

    def test_state_and_memory_templates_are_project_local_contract(self) -> None:
        policy = json.loads(
            (ROOT / "skills/research/references/policy.yaml").read_text(
                encoding="utf-8"
            )
        )
        state = json.loads(
            (ROOT / "skills/research/assets/state.template.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(policy["workflow_version"], "1.1.0")
        self.assertEqual(state["workflow_version"], policy["workflow_version"])
        self.assertTrue(state["enabled"])
        self.assertEqual(state["current_stage"], "idea")
        self.assertEqual(set(state), set(policy["state_contract"]["required_fields"]))
        self.assertTrue(all(gate["status"] == "pending" for gate in state["gates"].values()))
        memory = (ROOT / "skills/research/assets/memory.template.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("已验证事实", memory)
        self.assertIn("失败尝试与经验", memory)
        self.assertNotIn("Verified Facts", memory)

    def test_validator_rejects_state_contract_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            isolated = Path(temporary) / "plugin"
            isolated.mkdir()
            for relative in (
                ".agents",
                ".codex-plugin",
                "hooks",
                "scripts",
                "skills",
                "LICENSE",
                "README.md",
            ):
                source = ROOT / relative
                destination = isolated / relative
                if source.is_dir():
                    shutil.copytree(source, destination)
                else:
                    shutil.copy2(source, destination)
            policy_path = isolated / "skills/research/references/policy.yaml"
            baseline = json.loads(policy_path.read_text(encoding="utf-8"))
            mutations = {
                "required_fields": [],
                "gate_statuses": ["pending", "approved", "reopened", "forged"],
                "gate_actions": ["approve"],
            }
            for field, replacement in mutations.items():
                with self.subTest(field=field):
                    document = json.loads(json.dumps(baseline))
                    document["state_contract"][field] = replacement
                    policy_path.write_text(
                        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    errors = self.validate_at(isolated)
                    self.assertIn(
                        f"state_contract.{field} mismatch",
                        "\n".join(errors),
                    )

    def test_validator_rejects_cross_file_contract_and_packaging_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            isolated = Path(temporary) / "plugin"
            isolated.mkdir()
            for relative in (
                ".agents",
                ".codex-plugin",
                "hooks",
                "scripts",
                "skills",
                "LICENSE",
                "README.md",
            ):
                source = ROOT / relative
                destination = isolated / relative
                if source.is_dir():
                    shutil.copytree(source, destination)
                else:
                    shutil.copy2(source, destination)

            watched = [
                ".codex-plugin/plugin.json",
                ".agents/plugins/marketplace.json",
                "hooks/hooks.json",
                "skills/research/references/policy.yaml",
                "skills/research/assets/state.template.json",
                "skills/research/assets/memory.template.md",
                "LICENSE",
                "README.md",
            ]
            baseline = {
                relative: (isolated / relative).read_bytes() for relative in watched
            }

            def mutate_json(relative: str, mutation) -> None:
                target = isolated / relative
                document = json.loads(target.read_text(encoding="utf-8"))
                mutation(document)
                target.write_text(
                    json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

            cases = [
                (
                    "invalid-manifest-json",
                    lambda: (isolated / ".codex-plugin/plugin.json").write_text("{", encoding="utf-8"),
                    "invalid JSON",
                ),
                (
                    "empty-marketplace",
                    lambda: mutate_json(
                        ".agents/plugins/marketplace.json",
                        lambda value: value.__setitem__("plugins", []),
                    ),
                    "expected one plugin entry",
                ),
                (
                    "missing-hook-event",
                    lambda: mutate_json(
                        "hooks/hooks.json",
                        lambda value: value["hooks"].pop("Stop"),
                    ),
                    "expected events",
                ),
                (
                    "missing-stage-reference",
                    lambda: mutate_json(
                        "skills/research/references/policy.yaml",
                        lambda value: value["stages"]["idea"].__setitem__(
                            "reference", "missing.md"
                        ),
                    ),
                    "invalid reference",
                ),
                (
                    "state-template-field-drift",
                    lambda: mutate_json(
                        "skills/research/assets/state.template.json",
                        lambda value: value.pop("project_name"),
                    ),
                    "state.template.json: missing project_name",
                ),
                (
                    "memory-section-missing",
                    lambda: (isolated / "skills/research/assets/memory.template.md").write_text(
                        baseline["skills/research/assets/memory.template.md"]
                        .decode("utf-8")
                        .replace("开放问题", "待处理事项"),
                        encoding="utf-8",
                    ),
                    "memory.template.md: missing section",
                ),
                (
                    "license-owner-missing",
                    lambda: (isolated / "LICENSE").write_text("MIT License\n", encoding="utf-8"),
                    "local project owner missing",
                ),
                (
                    "external-reference-missing",
                    lambda: (isolated / "README.md").write_text(
                        baseline["README.md"]
                        .decode("utf-8")
                        .replace("https://github.com/Galaxy-Dawn/claude-scholar", ""),
                        encoding="utf-8",
                    ),
                    "missing external reference",
                ),
            ]
            for label, mutation, expected in cases:
                with self.subTest(label=label):
                    for relative, content in baseline.items():
                        (isolated / relative).write_bytes(content)
                    mutation()
                    errors = self.validate_at(isolated)
                    self.assertIn(expected, "\n".join(errors))

    def test_manifest_and_marketplace_match(self) -> None:
        manifest = json.loads(
            (ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        marketplace = json.loads(
            (ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
        )
        entry = marketplace["plugins"][0]
        self.assertEqual(manifest["version"], "1.1.3")
        self.assertEqual(entry["name"], manifest["name"])
        self.assertEqual(entry["version"], manifest["version"])
        self.assertEqual(entry["source"], {"source": "local", "path": "."})
        self.assertNotIn("hooks", manifest)

    def test_legacy_runtime_layers_are_removed(self) -> None:
        for relative in ("contracts", "profiles", "docs"):
            root = ROOT / relative
            self.assertFalse(root.exists() and any(root.rglob("*")), relative)
        self.assertFalse((ROOT / "scripts/install_codex.py").exists())
        self.assertFalse((ROOT / "skills/research-orchestrator").exists())

    def test_researchctl_has_all_public_commands(self) -> None:
        result = self.run_python("scripts/researchctl.py", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in (
            "init",
            "status",
            "enable",
            "disable",
            "artifact",
            "gate",
            "checkpoint",
            "doctor",
        ):
            self.assertIn(command, result.stdout)

    def test_root_license_and_external_references_remain_link_only(self) -> None:
        self.assertIn("Copyright 2026 Fusica", (ROOT / "LICENSE").read_text())
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for url in (
            "https://github.com/Galaxy-Dawn/claude-scholar",
            "https://github.com/EvoScientist/EvoSkills",
            "https://github.com/Yuan1z0825/nature-skills",
            "https://github.com/lingzhi227/agent-research-skills",
        ):
            self.assertIn(url, readme)
        for relative in ("vendor", "THIRD_PARTY_NOTICES.md", "upstreams.lock.yaml"):
            self.assertFalse((ROOT / relative).exists(), relative)


if __name__ == "__main__":
    unittest.main()
