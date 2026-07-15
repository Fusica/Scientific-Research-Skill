from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.researchctl_core.gates import artifact_role_contract_for_gate
from scripts.researchctl_core.policy import load_policy


ROOT = Path(__file__).resolve().parents[1]
RESEARCHCTL = ROOT / "scripts/researchctl.py"


class ResearchProjectTestCase(unittest.TestCase):
    """Small real-CLI fixture shared by workflow and dashboard tests."""

    project: Path

    def setUp(self) -> None:
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name) / "research-project"
        self.project.mkdir()
        subprocess.run(
            ["git", "init", "-q", str(self.project)],
            check=True,
            capture_output=True,
            text=True,
        )
        result = self.run_ctl("init")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.sources: dict[str, Path] = {}

    def environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment.pop("RESEARCHCTL_POLICY", None)
        environment.pop("RESEARCHCTL_RUNTIME_CONTRACT", None)
        environment["RESEARCHCTL_ACTOR"] = "test-researcher"
        if (
            environment.get("COVERAGE_PROCESS_START")
            or environment.get("COVERAGE_PROCESS_CONFIG")
        ):
            environment.setdefault("COVERAGE_FILE", str(ROOT / ".coverage"))
        return environment

    def run_ctl(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(RESEARCHCTL), *arguments],
            cwd=self.project,
            env=self.environment(),
            check=False,
            capture_output=True,
            encoding="utf-8",
        )

    @property
    def state_path(self) -> Path:
        return self.project / ".research/state.json"

    def load_state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def write_state(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def artifact_entry(self, role_reference: str, artifact_id: str) -> dict[str, Any]:
        stage, role = role_reference.split(".", 1)
        return self.load_state()["artifacts"][stage][role][artifact_id]

    def register(
        self,
        role_reference: str,
        artifact_id: str | None = None,
        *,
        path: Path | None = None,
        content: str | None = None,
    ) -> tuple[str, Path, subprocess.CompletedProcess[str]]:
        stage, role = role_reference.split(".", 1)
        artifact_id = artifact_id or f"{stage}-{role}".upper().replace("_", "-")
        source = path or self.project / "work" / stage / f"{role}.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        if content is not None or not source.exists():
            source.write_text(
                content if content is not None else f"# {artifact_id}\n",
                encoding="utf-8",
            )
        result = self.run_ctl(
            "artifact",
            "register",
            role,
            "--stage",
            stage,
            "--path",
            str(source),
            "--artifact-id",
            artifact_id,
        )
        self.sources[role_reference] = source
        return artifact_id, source, result

    def required_roles(
        self,
        gate: str,
        release_target: str | None = None,
        *,
        approval_mode: str | None = None,
    ) -> list[str]:
        roles, _optional = artifact_role_contract_for_gate(
            load_policy(), gate, release_target, approval_mode
        )
        return list(roles)

    def register_gate_requirements(
        self,
        gate: str,
        *,
        release_target: str | None = None,
        approval_mode: str | None = None,
        path_overrides: dict[str, Path] | None = None,
    ) -> None:
        overrides = path_overrides or {}
        state = self.load_state()
        for role_reference in self.required_roles(
            gate, release_target, approval_mode=approval_mode
        ):
            stage, role = role_reference.split(".", 1)
            bucket = state.get("artifacts", {}).get(stage, {}).get(role, {})
            if isinstance(bucket, dict) and bucket:
                continue
            artifact_id, _source, result = self.register(
                role_reference,
                path=overrides.get(role_reference),
            )
            self.assertEqual(result.returncode, 0, f"{artifact_id}: {result.stderr}")
            state = self.load_state()

    def gate(
        self,
        action: str,
        gate: str,
        *,
        selected_id: str | None = None,
        release_target: str | None = None,
        retrospective: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        arguments = [
            "gate",
            action,
            gate,
            "--reason",
            f"Explicit test owner decision for {gate} {action}.",
            "--supporting-evidence-id",
            f"EVID-{gate}-support",
            "--decision-condition",
            f"Stop or reopen {gate} when its registered boundary changes.",
        ]
        if selected_id is not None:
            arguments.extend(["--selected-id", selected_id])
        if release_target is not None:
            arguments.extend(["--target", release_target])
        if retrospective:
            arguments.append("--retrospective-revision-import")
        return self.run_ctl(*arguments)

    def approve_gate(
        self,
        gate: str,
        *,
        selected_id: str | None = None,
        release_target: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.register_gate_requirements(gate, release_target=release_target)
        if selected_id is None and gate == "idea_freeze":
            selected_id = "IDEA-003"
        if selected_id is None and gate == "method_experiment_approval":
            selected_id = "METHOD-002"
        return self.gate(
            "approve",
            gate,
            selected_id=selected_id,
            release_target=release_target,
        )

    def lifecycle(
        self,
        action: str,
        *,
        gate: str | None = None,
        target: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        arguments = [
            "lifecycle",
            action,
            "--reason",
            f"Explicit test owner lifecycle decision: {action}.",
            "--supporting-evidence-id",
            f"EVID-LIFECYCLE-{action}",
            "--decision-condition",
            f"Reassess the same mainline after lifecycle {action}.",
        ]
        if gate is not None:
            arguments.extend(["--gate", gate])
        if target is not None:
            arguments.extend(["--target", target])
        return self.run_ctl(*arguments)

    def advance_through_claim_freeze(self) -> None:
        for gate in ("idea_freeze", "method_experiment_approval", "claim_freeze"):
            result = self.approve_gate(gate)
            self.assertEqual(result.returncode, 0, result.stderr)
