from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class RepositoryTest(unittest.TestCase):
    def run_script(
        self, *args: str, cwd: Path = ROOT
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )

    def clone_for_mutation(self, destination: Path) -> Path:
        clone = destination / "repo"
        shutil.copytree(
            ROOT,
            clone,
            ignore=shutil.ignore_patterns(
                ".git", ".venv", "__pycache__", "*.pyc", ".DS_Store"
            ),
        )
        return clone

    def test_repository_validator(self) -> None:
        result = self.run_script("scripts/validate_repo.py")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_installer_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "skills"
            result = self.run_script(
                "scripts/install_codex.py",
                "--destination",
                str(destination),
                "--dry-run",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(destination.exists())

    def test_installer_copy_single_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "skills"
            result = self.run_script(
                "scripts/install_codex.py",
                "--mode",
                "copy",
                "--destination",
                str(destination),
                "--skill",
                "idea-evolution",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((destination / "idea-evolution" / "SKILL.md").is_file())
            self.assertFalse((destination / "research-orchestrator").exists())

    def test_full_install_never_copies_vendor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "skills"
            result = self.run_script(
                "scripts/install_codex.py",
                "--mode",
                "copy",
                "--destination",
                str(destination),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            installed = {path.name for path in destination.iterdir()}
            self.assertEqual(len(installed), 8)
            self.assertNotIn("vendor", installed)

    def test_json_contracts_parse(self) -> None:
        for path in (ROOT / "contracts").glob("*.json"):
            with self.subTest(path=path.name):
                json.loads(path.read_text(encoding="utf-8"))

    def test_artifact_catalog_has_unique_roles_and_paths(self) -> None:
        catalog = yaml.safe_load(
            (ROOT / "contracts/artifact-catalog.yaml").read_text(encoding="utf-8")
        )
        records = catalog["artifacts"]
        roles = [record["role"] for record in records]
        paths = [record["canonical_path"] for record in records]
        self.assertEqual(len(roles), len(set(roles)))
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(catalog["gate_authority"], ".research/project-state.yaml")

    def test_new_claim_is_unassessed(self) -> None:
        ledger = yaml.safe_load(
            (ROOT / "contracts/claim-ledger.template.yaml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(ledger["claims"][0]["status"], "unassessed")

    def test_root_license_has_local_owner(self) -> None:
        root_license = (ROOT / "LICENSE").read_text(encoding="utf-8")
        vendor_license = (ROOT / "vendor/evoskills/LICENSE").read_text(
            encoding="utf-8"
        )
        self.assertIn("Copyright 2026 Fusica", root_license)
        self.assertNotEqual(root_license, vendor_license)

    def test_validator_rejects_broken_prediction_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clone = self.clone_for_mutation(Path(temporary))
            matrix_path = clone / "contracts/experiment-matrix.template.yaml"
            matrix = yaml.safe_load(matrix_path.read_text(encoding="utf-8"))
            matrix["experiments"][0]["prediction_ids"] = ["PRED-999"]
            matrix_path.write_text(
                yaml.safe_dump(matrix, sort_keys=False), encoding="utf-8"
            )
            result = self.run_script(
                "scripts/validate_repo.py",
                cwd=clone,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("prediction_ids must resolve", result.stderr)

    def test_validator_rejects_manifest_without_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clone = self.clone_for_mutation(Path(temporary))
            manifest_path = (
                clone / "contracts/artifact-manifest-record.template.yaml"
            )
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            del manifest["sha256"]
            manifest_path.write_text(
                yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
            )
            result = self.run_script(
                "scripts/validate_repo.py",
                cwd=clone,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing keys sha256", result.stderr)


if __name__ == "__main__":
    unittest.main()
