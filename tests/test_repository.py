from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

import yaml


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
        self.assertEqual(policy["gates"]["idea_freeze"]["advance_to"], "method")
        self.assertEqual(
            policy["gates"]["method_experiment_approval"]["advance_to"],
            "experiment_results",
        )

    def test_state_and_memory_templates_are_project_local_contract(self) -> None:
        state = json.loads(
            (ROOT / "skills/research/assets/state.template.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(state["enabled"])
        self.assertEqual(state["current_stage"], "idea")
        self.assertTrue(all(gate["status"] == "pending" for gate in state["gates"].values()))
        memory = (ROOT / "skills/research/assets/memory.template.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Verified Facts", memory)
        self.assertIn("Failed Attempts and Lessons", memory)

    def test_manifest_and_marketplace_match(self) -> None:
        manifest = json.loads(
            (ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        marketplace = json.loads(
            (ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
        )
        entry = marketplace["plugins"][0]
        self.assertEqual(manifest["version"], "1.0.0")
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
        for command in ("init", "status", "enable", "disable", "gate", "checkpoint", "doctor"):
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
