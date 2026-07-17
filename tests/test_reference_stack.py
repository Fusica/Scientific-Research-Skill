from __future__ import annotations

import copy
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from scripts import reference_stack as reference_stack_module

try:
    from .research_test_support import ROOT, ResearchProjectTestCase
except ImportError:  # unittest discover -s tests
    from research_test_support import ROOT, ResearchProjectTestCase


REFERENCE_STACK = ROOT / "scripts/reference_stack.py"


class ReferenceStackTest(ResearchProjectTestCase):
    def artifact_ref(
        self, role_reference: str, artifact_id: str
    ) -> dict[str, Any]:
        stage, role = role_reference.split(".", 1)
        revision = self.artifact_entry(role_reference, artifact_id)["revisions"][-1]
        return {
            "label": f"artifacts.{stage}.{role}.{artifact_id}",
            "artifact_id": artifact_id,
            **revision,
        }

    def gate_binding(self, gate: str) -> dict[str, Any]:
        decision = self.load_state()["gates"][gate]["history"][-1]
        return {
            "gate_ref": {"gate": gate},
            "gate_decision_id": decision["decision_id"],
            "artifact_refs": decision["artifact_refs"],
        }

    def test_publish_batch_recovers_exact_historical_revision_after_output_failure(
        self,
    ) -> None:
        sandbox = self.project / "reference-recovery-sandbox"
        sandbox.mkdir()
        source = sandbox / "output.json"
        source.write_text('{"result":"retained"}\n', encoding="utf-8")
        content_hash, size_bytes = reference_stack_module._hash_file(source)
        spec = {
            "publish_path": (
                ".research/artifacts/idea/reference-stack/ATTEMPT-RECOVERY/output.json"
            ),
            "role": "reference_recovery_output",
            "artifact_id": "REFERENCE-RECOVERY-001",
        }
        exact_revision = {
            "revision": 1,
            "source_path": spec["publish_path"],
            "snapshot_path": ".research/snapshots/recovery-r1.json",
            "content_hash": content_hash,
            "size_bytes": size_bytes,
            "registered_at": "2026-07-16T00:00:00Z",
        }
        later_revision = {
            "revision": 2,
            "source_path": (
                ".research/artifacts/idea/reference-stack/ATTEMPT-LATER/output.json"
            ),
            "snapshot_path": ".research/snapshots/recovery-r2.json",
            "content_hash": "sha256:" + "f" * 64,
            "size_bytes": 99,
            "registered_at": "2026-07-16T00:01:00Z",
        }
        state = {
            "artifacts": {
                "idea": {
                    spec["role"]: {
                        spec["artifact_id"]: {
                            "current_revision": 2,
                            "revisions": [exact_revision, later_revision],
                        }
                    }
                }
            }
        }
        publish_result = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout='{"output_schema_version":',
            stderr="injected post-commit output failure",
        )
        status_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(state),
            stderr="",
        )

        with mock.patch.object(
            reference_stack_module,
            "_run_researchctl",
            side_effect=[publish_result, status_result],
        ):
            references = reference_stack_module._publish_batch(
                ROOT / "scripts/researchctl.py",
                self.project,
                "idea",
                "ATTEMPT-RECOVERY",
                sandbox,
                [(spec, source, content_hash, size_bytes)],
            )

        self.assertEqual(len(references), 1)
        self.assertEqual(references[0]["revision"], 1)
        self.assertEqual(references[0]["content_hash"], content_hash)
        self.assertEqual(references[0]["source_path"], spec["publish_path"])

    def test_publish_batch_binds_the_first_observed_output_bytes(self) -> None:
        control_root = self.project / "reference-observation-binding"
        control_root.mkdir()
        source = control_root / "output.json"
        source.write_text('{"value":"observed"}\n', encoding="utf-8")
        observed_hash, observed_size = reference_stack_module._hash_file(source)
        source.write_text('{"value":"mutated"}\n', encoding="utf-8")
        spec = {
            "publish_path": (
                ".research/artifacts/idea/reference-stack/"
                "ATTEMPT-OBSERVED/output.json"
            ),
            "role": "observed_output",
            "artifact_id": "OBSERVED-OUTPUT-001",
        }

        with (
            mock.patch.object(reference_stack_module, "_run_researchctl") as writer,
            self.assertRaisesRegex(
                reference_stack_module.ReferenceStackError,
                "changed after observation",
            ),
        ):
            reference_stack_module._publish_batch(
                ROOT / "scripts/researchctl.py",
                self.project,
                "idea",
                "ATTEMPT-OBSERVED",
                control_root,
                [(spec, source, observed_hash, observed_size)],
            )

        writer.assert_not_called()

    def test_writer_rejects_a_change_after_the_adapter_precheck(self) -> None:
        control_root = self.project / "reference-writer-binding"
        control_root.mkdir()
        source = control_root / "output.json"
        source.write_text('{"value":"observed"}\n', encoding="utf-8")
        observed_hash, observed_size = reference_stack_module._hash_file(source)
        publish_path = (
            ".research/artifacts/idea/reference-stack/"
            "ATTEMPT-WRITER-BINDING/output.json"
        )
        spec = {
            "publish_path": publish_path,
            "role": "writer_bound_output",
            "artifact_id": "WRITER-BOUND-OUTPUT-001",
        }
        state_before = self.state_path.read_bytes()
        original_runner = reference_stack_module._run_researchctl
        publish_calls = 0

        def mutate_at_writer_boundary(*args: object, **kwargs: object):
            nonlocal publish_calls
            arguments = args[2]
            if "publish-batch" in arguments and publish_calls == 0:
                publish_calls += 1
                source.write_text('{"value":"mutated after check"}\n', encoding="utf-8")
            return original_runner(*args, **kwargs)

        with (
            mock.patch.object(
                reference_stack_module,
                "_run_researchctl",
                side_effect=mutate_at_writer_boundary,
            ),
            self.assertRaisesRegex(
                reference_stack_module.ReferenceStackError,
                "failed without an exact committed revision",
            ),
        ):
            reference_stack_module._publish_batch(
                ROOT / "scripts/researchctl.py",
                self.project,
                "idea",
                "ATTEMPT-WRITER-BINDING",
                control_root,
                [(spec, source, observed_hash, observed_size)],
            )

        self.assertEqual(self.state_path.read_bytes(), state_before)
        self.assertFalse((self.project / publish_path).exists())

    @unittest.skipIf(os.name == "nt", "POSIX process groups are required")
    def test_successful_command_cannot_leave_a_background_descendant(self) -> None:
        sandbox = self.project / "background-descendant-sandbox"
        sandbox.mkdir()
        program = sandbox / "fork_child.py"
        program.write_text(
            """import os
import pathlib
import time

child = os.fork()
if child:
    pathlib.Path("child.pid").write_text(str(child), encoding="utf-8")
    raise SystemExit(0)
time.sleep(0.6)
pathlib.Path("late-write.txt").write_text("escaped", encoding="utf-8")
""",
            encoding="utf-8",
        )
        with tempfile.TemporaryFile(mode="w+b") as spool:
            observation = reference_stack_module._run_command(
                [sys.executable, str(program)],
                cwd=sandbox,
                environment={"PATH": os.environ.get("PATH", "")},
                timeout_seconds=5,
                log_stream=reference_stack_module._OwnedLog(spool),
            )

        self.assertEqual(observation["returncode"], 0)
        self.assertFalse(observation["timed_out"])
        child_pid = int((sandbox / "child.pid").read_text(encoding="utf-8"))
        time.sleep(0.8)
        self.assertFalse((sandbox / "late-write.txt").exists())
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            pass
        else:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.fail("background descendant remained alive after successful step")

    def prepare_request(
        self,
        *,
        stage: str,
        operation_kind: str,
        gate: str,
        program: str,
        steps: list[dict[str, Any]],
        suffix: str,
        output_publish_path: str | None = None,
        output_source_path: str = "workspace/output/result.json",
        invalid_result_path: str | None = None,
    ) -> tuple[str, str, Path, dict[str, Any]]:
        program_id, _source, registered_program = self.register(
            f"{stage}.reference_program",
            f"REFERENCE-PROGRAM-{suffix}",
            content=program,
        )
        self.assertEqual(
            registered_program.returncode, 0, registered_program.stderr
        )
        program_ref = self.artifact_ref(f"{stage}.reference_program", program_id)
        binding = self.gate_binding(gate)
        operational_refs = [program_ref]
        for reference in binding["artifact_refs"]:
            if reference not in operational_refs:
                operational_refs.append(reference)
        attempt_id = f"REFERENCE-ATTEMPT-{suffix}"
        publication_root = (
            f".research/artifacts/{stage}/reference-stack/{attempt_id}"
        )
        config = {
            "schema_version": "1.0",
            "adapter_kind": "isolated_command",
            "operation_kind": operation_kind,
            "working_directory": "workspace",
            "environment": {
                "inherit": ["PATH"],
                "set": {"PYTHONHASHSEED": "0"},
            },
            "network": "declared_disabled",
            "materials": [
                {
                    "artifact_ref": reference,
                    "destination": (
                        "workspace/program.py"
                        if reference == program_ref
                        else (
                            "workspace/gate-inputs/"
                            f"{index:03d}-{reference['artifact_id']}.bin"
                        )
                    ),
                }
                for index, reference in enumerate(operational_refs)
            ],
            "tool_probes": [
                {
                    "tool_id": "python",
                    "argv": [sys.executable, "--version"],
                    "timeout_seconds": 10,
                }
            ],
            "steps": steps,
            "expected_outputs": [
                {
                    "source_path": output_source_path,
                    "publish_path": (
                        output_publish_path
                        or f"{publication_root}/output.json"
                    ),
                    "role": "reference_output",
                    "artifact_id": f"REFERENCE-OUTPUT-{suffix}",
                    "classification": "output",
                }
            ],
            "log_artifact": {
                "publish_path": f"{publication_root}/execution.log",
                "role": "reference_execution_log",
                "artifact_id": f"REFERENCE-LOG-{suffix}",
            },
            "result_artifact": {
                "publish_path": (
                    invalid_result_path
                    or f"{publication_root}/result.json"
                ),
                "role": "reference_execution_result",
                "artifact_id": f"REFERENCE-RESULT-{suffix}",
            },
        }
        config_path = self.project / f"work/{stage}/reference-config-{suffix}.json"
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        config_id, _source, registered_config = self.register(
            f"{stage}.reference_execution_request",
            f"REFERENCE-CONFIG-{suffix}",
            path=config_path,
        )
        self.assertEqual(registered_config.returncode, 0, registered_config.stderr)
        config_ref = self.artifact_ref(
            f"{stage}.reference_execution_request", config_id
        )
        input_refs = [
            config_ref,
            *(material["artifact_ref"] for material in config["materials"]),
        ]
        request_id = f"REFERENCE-REQUEST-{suffix}"
        request = {
            "request_id": request_id,
            "operation_kind": operation_kind,
            "created_at": self.load_state()["updated_at"],
            "gate_binding": binding,
            "payload": {
                "artifact_ref": config_ref,
                "locator": "#reference-stack-v1",
            },
            "input_artifact_refs": input_refs,
            "effect_class": "low_risk",
            "human_authorization": None,
            "retry_policy": {
                "mode": "reconcile_before_retry",
                "max_attempts": 2,
                "idempotency_key": f"REFERENCE-IDEMPOTENCY-{suffix}",
            },
        }
        request_path = self.project / f"work/{stage}/adapter-request-{suffix}.json"
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        exchange_relative = f"work/{stage}/adapter-exchange-{suffix}.json"
        exchange_id = f"REFERENCE-EXCHANGE-{suffix}"
        appended = self.run_ctl(
            "adapter",
            "request-append",
            "--stage",
            stage,
            "--path",
            exchange_relative,
            "--artifact-id",
            exchange_id,
            "--request",
            str(request_path),
            "--json",
        )
        self.assertEqual(appended.returncode, 0, appended.stderr)
        return request_id, exchange_id, self.project / exchange_relative, config

    def run_stack(
        self,
        *,
        stage: str,
        request_id: str,
        exchange_id: str,
        exchange_path: Path,
        attempt_id: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(REFERENCE_STACK),
                "run",
                "--request-id",
                request_id,
                "--attempt-id",
                attempt_id,
                "--stage",
                stage,
                "--exchange-path",
                str(exchange_path.relative_to(self.project)),
                "--exchange-artifact-id",
                exchange_id,
                "--project-root",
                str(self.project),
            ],
            cwd=self.project,
            env=self.environment(),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_experiment_reference_stack_closes_isolated_success_path(self) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        (self.project / "project-only.txt").write_text("not materialized\n")
        program = (
            "import json\n"
            "from pathlib import Path\n"
            "Path('output').mkdir()\n"
            "Path('output/result.json').write_text(json.dumps({"
            "'project_sentinel_visible': Path('project-only.txt').exists(), "
            "'result': 7}) + '\\n')\n"
        )
        request_id, exchange_id, exchange_path, config = self.prepare_request(
            stage="experiment_results",
            operation_kind="experiment_execution",
            gate="method_experiment_approval",
            program=program,
            steps=[
                {
                    "step_id": "execute",
                    "argv": [sys.executable, "program.py"],
                    "timeout_seconds": 30,
                }
            ],
            suffix="EXPERIMENT-SUCCESS",
        )

        result = self.run_stack(
            stage="experiment_results",
            request_id=request_id,
            exchange_id=exchange_id,
            exchange_path=exchange_path,
            attempt_id="REFERENCE-ATTEMPT-EXPERIMENT-SUCCESS",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "succeeded")
        output_path = self.project / config["expected_outputs"][0]["publish_path"]
        self.assertFalse(json.loads(output_path.read_text())["project_sentinel_visible"])
        result_manifest = self.project / config["result_artifact"]["publish_path"]
        report = json.loads(result_manifest.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "succeeded")
        self.assertTrue(report["mechanical_checks"]["materialized_inputs_unchanged"])
        self.assertEqual(report["semantic_certifications"], [])
        exchange = json.loads(exchange_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [receipt["status"] for receipt in exchange["receipts"]],
            ["accepted", "succeeded"],
        )
        self.assertGreater(
            exchange["receipts"][0]["observed_at"],
            exchange["requests"][0]["created_at"],
        )
        entry = self.artifact_entry(
            "experiment_results.adapter_exchange", exchange_id
        )
        self.assertEqual(entry["current_revision"], 3)
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

    def test_materials_cover_every_non_payload_request_input_exactly_once(
        self,
    ) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        request_id, _exchange_id, exchange_path, config = self.prepare_request(
            stage="experiment_results",
            operation_kind="experiment_execution",
            gate="method_experiment_approval",
            program="raise SystemExit(0)\n",
            steps=[
                {
                    "step_id": "execute",
                    "argv": [sys.executable, "program.py"],
                    "timeout_seconds": 30,
                }
            ],
            suffix="EXACT-MATERIALS",
        )
        exchange = json.loads(exchange_path.read_text(encoding="utf-8"))
        request = next(
            item
            for item in exchange["requests"]
            if item["request_id"] == request_id
        )

        omitted = copy.deepcopy(config)
        omitted["materials"].pop()
        with self.assertRaisesRegex(
            reference_stack_module.ReferenceStackError,
            "cover every non-payload request input",
        ):
            reference_stack_module._validate_config(
                omitted, request, self.project
            )

        duplicated = copy.deepcopy(config)
        duplicate = copy.deepcopy(duplicated["materials"][0])
        duplicate["destination"] = "workspace/duplicate-input.bin"
        duplicated["materials"].append(duplicate)
        with self.assertRaisesRegex(
            reference_stack_module.ReferenceStackError,
            "artifact references must be unique",
        ):
            reference_stack_module._validate_config(
                duplicated, request, self.project
            )

    def test_declared_command_cannot_replace_the_parent_owned_execution_log(
        self,
    ) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        program = (
            "from pathlib import Path\n"
            "Path('output').mkdir()\n"
            "Path('output/result.json').write_text('honest output\\n')\n"
            "candidate = Path('../reference-stack.log')\n"
            "candidate.unlink(missing_ok=True)\n"
            "candidate.write_text('FORGED LOG\\n')\n"
        )
        request_id, exchange_id, exchange_path, config = self.prepare_request(
            stage="experiment_results",
            operation_kind="experiment_execution",
            gate="method_experiment_approval",
            program=program,
            steps=[
                {
                    "step_id": "execute",
                    "argv": [sys.executable, "program.py"],
                    "timeout_seconds": 30,
                }
            ],
            suffix="LOG-OWNERSHIP",
        )

        result = self.run_stack(
            stage="experiment_results",
            request_id=request_id,
            exchange_id=exchange_id,
            exchange_path=exchange_path,
            attempt_id="REFERENCE-ATTEMPT-LOG-OWNERSHIP",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        retained_log = self.project / config["log_artifact"]["publish_path"]
        retained = retained_log.read_text(encoding="utf-8")
        self.assertIn("Reference Stack mechanical log", retained)
        self.assertNotEqual(retained, "FORGED LOG\n")

    def test_adapter_control_log_cannot_satisfy_a_declared_expected_output(
        self,
    ) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        request_id, exchange_id, exchange_path, _config = self.prepare_request(
            stage="experiment_results",
            operation_kind="experiment_execution",
            gate="method_experiment_approval",
            program="raise SystemExit(0)\n",
            steps=[
                {
                    "step_id": "execute",
                    "argv": [sys.executable, "program.py"],
                    "timeout_seconds": 30,
                }
            ],
            suffix="CONTROL-NOT-OUTPUT",
            output_source_path="reference-stack.log",
        )

        result = self.run_stack(
            stage="experiment_results",
            request_id=request_id,
            exchange_id=exchange_id,
            exchange_path=exchange_path,
            attempt_id="REFERENCE-ATTEMPT-CONTROL-NOT-OUTPUT",
        )

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "failed")

    @unittest.skipIf(os.name == "nt", "process-group interruption is POSIX-only")
    def test_interrupted_declared_command_is_killed_before_unwinding(self) -> None:
        class InterruptingProcess:
            pid = 4242

            def __init__(self) -> None:
                self.returncode: int | None = None
                self.killed = False
                self.wait_calls = 0
                self.stdout = io.BytesIO()

            def poll(self) -> int | None:
                return self.returncode

            def wait(self, timeout: int | None = None) -> int:
                self.wait_calls += 1
                if self.wait_calls == 1:
                    raise KeyboardInterrupt
                self.returncode = -9
                return self.returncode

            def kill(self) -> None:
                self.killed = True
                self.returncode = -9

        process = InterruptingProcess()
        with (
            mock.patch.object(
                reference_stack_module.subprocess,
                "Popen",
                return_value=process,
            ),
            mock.patch.object(
                reference_stack_module.os,
                "killpg",
                side_effect=OSError("process group unavailable"),
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            reference_stack_module._run_command(
                ["example"],
                cwd=self.project,
                environment={},
                timeout_seconds=30,
                log_stream=reference_stack_module._OwnedLog(io.BytesIO()),
            )

        self.assertTrue(process.killed)
        self.assertEqual(process.returncode, -9)

    def test_failed_command_is_preserved_as_a_terminal_receipt(self) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        program = (
            "from pathlib import Path\n"
            "Path('output').mkdir()\n"
            "Path('output/result.json').write_text('{\"partial\":true}\\n')\n"
            "raise SystemExit(7)\n"
        )
        request_id, exchange_id, exchange_path, config = self.prepare_request(
            stage="experiment_results",
            operation_kind="experiment_execution",
            gate="method_experiment_approval",
            program=program,
            steps=[
                {
                    "step_id": "execute",
                    "argv": [sys.executable, "program.py"],
                    "timeout_seconds": 30,
                }
            ],
            suffix="EXPERIMENT-FAILED",
        )

        result = self.run_stack(
            stage="experiment_results",
            request_id=request_id,
            exchange_id=exchange_id,
            exchange_path=exchange_path,
            attempt_id="REFERENCE-ATTEMPT-EXPERIMENT-FAILED",
        )

        self.assertEqual(result.returncode, 1, result.stderr)
        exchange = json.loads(exchange_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [receipt["status"] for receipt in exchange["receipts"]],
            ["accepted", "failed"],
        )
        report = json.loads(
            (self.project / config["result_artifact"]["publish_path"]).read_text()
        )
        self.assertFalse(report["mechanical_checks"]["steps_passed"])
        self.assertEqual(report["steps"][0]["returncode"], 7)
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

    def test_paper_reference_stack_runs_declared_clean_and_build_steps(self) -> None:
        self.advance_through_claim_freeze()
        program = (
            "import json, sys\n"
            "from pathlib import Path\n"
            "output = Path('output')\n"
            "if sys.argv[1] == 'clean':\n"
            "    assert not output.exists()\n"
            "elif sys.argv[1] == 'build':\n"
            "    output.mkdir()\n"
            "    (output / 'result.json').write_text(json.dumps({"
            "'built': True}) + '\\n')\n"
        )
        request_id, exchange_id, exchange_path, config = self.prepare_request(
            stage="paper",
            operation_kind="paper_production",
            gate="claim_freeze",
            program=program,
            steps=[
                {
                    "step_id": "clean",
                    "argv": [sys.executable, "program.py", "clean"],
                    "timeout_seconds": 30,
                },
                {
                    "step_id": "build",
                    "argv": [sys.executable, "program.py", "build"],
                    "timeout_seconds": 30,
                },
            ],
            suffix="PAPER-BUILD",
        )

        result = self.run_stack(
            stage="paper",
            request_id=request_id,
            exchange_id=exchange_id,
            exchange_path=exchange_path,
            attempt_id="REFERENCE-ATTEMPT-PAPER-BUILD",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(
            (self.project / config["result_artifact"]["publish_path"]).read_text()
        )
        self.assertEqual(
            [step["step_id"] for step in report["steps"]], ["clean", "build"]
        )
        self.assertTrue(report["mechanical_checks"]["steps_passed"])
        self.assertIn(
            "does_not_certify_researcher_review_or_venue_facts",
            report["limitations"],
        )

    def test_control_metadata_publish_path_is_rejected_before_acceptance(self) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        request_id, exchange_id, exchange_path, _config = self.prepare_request(
            stage="experiment_results",
            operation_kind="experiment_execution",
            gate="method_experiment_approval",
            program="raise SystemExit('must not run')\n",
            steps=[
                {
                    "step_id": "execute",
                    "argv": [sys.executable, "program.py"],
                    "timeout_seconds": 30,
                }
            ],
            suffix="CONTROL-PATH",
            invalid_result_path=".research/state.json",
        )
        state_before = self.state_path.read_bytes()

        result = self.run_stack(
            stage="experiment_results",
            request_id=request_id,
            exchange_id=exchange_id,
            exchange_path=exchange_path,
            attempt_id="REFERENCE-ATTEMPT-CONTROL-PATH",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("research control metadata", result.stderr)
        self.assertEqual(self.state_path.read_bytes(), state_before)
        exchange = json.loads(exchange_path.read_text(encoding="utf-8"))
        self.assertEqual(exchange["receipts"], [])

    def test_publish_paths_must_match_the_exact_attempt_before_acceptance(self) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        request_id, exchange_id, exchange_path, config = self.prepare_request(
            stage="experiment_results",
            operation_kind="experiment_execution",
            gate="method_experiment_approval",
            program="raise SystemExit('must not run')\n",
            steps=[
                {
                    "step_id": "execute",
                    "argv": [sys.executable, "program.py"],
                    "timeout_seconds": 30,
                }
            ],
            suffix="ATTEMPT-SCOPE",
        )
        state_before = self.state_path.read_bytes()

        result = self.run_stack(
            stage="experiment_results",
            request_id=request_id,
            exchange_id=exchange_id,
            exchange_path=exchange_path,
            attempt_id="DIFFERENT-ATTEMPT",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("fresh and attempt-scoped", result.stderr)
        self.assertEqual(self.state_path.read_bytes(), state_before)
        self.assertFalse(
            (self.project / config["expected_outputs"][0]["publish_path"]).exists()
        )
        exchange = json.loads(exchange_path.read_text(encoding="utf-8"))
        self.assertEqual(exchange["receipts"], [])

    @unittest.skipIf(sys.platform == "win32", "symlink semantics differ on Windows")
    def test_symlinked_sandbox_output_is_never_published(self) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        program = (
            "from pathlib import Path\n"
            "Path('output').mkdir()\n"
            "Path('output/result.json').symlink_to('../program.py')\n"
        )
        request_id, exchange_id, exchange_path, config = self.prepare_request(
            stage="experiment_results",
            operation_kind="experiment_execution",
            gate="method_experiment_approval",
            program=program,
            steps=[
                {
                    "step_id": "execute",
                    "argv": [sys.executable, "program.py"],
                    "timeout_seconds": 30,
                }
            ],
            suffix="SYMLINK-OUTPUT",
        )

        result = self.run_stack(
            stage="experiment_results",
            request_id=request_id,
            exchange_id=exchange_id,
            exchange_path=exchange_path,
            attempt_id="REFERENCE-ATTEMPT-SYMLINK-OUTPUT",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot traverse a symlink", result.stderr)
        self.assertFalse(
            (self.project / config["expected_outputs"][0]["publish_path"]).exists()
        )

    def test_registered_input_source_cannot_be_an_output_publish_path(self) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        protected_relative = "work/experiment_results/reference_program.md"
        request_id, exchange_id, exchange_path, _config = self.prepare_request(
            stage="experiment_results",
            operation_kind="experiment_execution",
            gate="method_experiment_approval",
            program=(
                "from pathlib import Path\n"
                "Path('output').mkdir()\n"
                "Path('output/result.json').write_text('{}\\n')\n"
            ),
            steps=[
                {
                    "step_id": "execute",
                    "argv": [sys.executable, "program.py"],
                    "timeout_seconds": 30,
                }
            ],
            suffix="PROTECTED-INPUT",
            output_publish_path=protected_relative,
        )
        exchange = json.loads(exchange_path.read_text(encoding="utf-8"))
        program_ref = next(
            reference
            for reference in exchange["requests"][0]["input_artifact_refs"]
            if reference["artifact_id"] == "REFERENCE-PROGRAM-PROTECTED-INPUT"
        )
        actual_source = Path(program_ref["source_path"])
        if not actual_source.is_absolute():
            actual_source = self.project / actual_source
        self.assertEqual(
            actual_source.relative_to(self.project).as_posix(), protected_relative
        )
        source_before = actual_source.read_bytes()
        state_before = self.state_path.read_bytes()
        exchange_before = exchange_path.read_bytes()

        result = self.run_stack(
            stage="experiment_results",
            request_id=request_id,
            exchange_id=exchange_id,
            exchange_path=exchange_path,
            attempt_id="REFERENCE-ATTEMPT-PROTECTED-INPUT",
        )

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("cannot overwrite a registered input", result.stderr)
        self.assertEqual(actual_source.read_bytes(), source_before)
        self.assertEqual(self.state_path.read_bytes(), state_before)
        self.assertEqual(exchange_path.read_bytes(), exchange_before)

    def test_unregistered_existing_project_file_cannot_be_a_publish_path(self) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        publish_relative = "work/experiment_results/unrelated-existing.json"
        request_id, exchange_id, exchange_path, _config = self.prepare_request(
            stage="experiment_results",
            operation_kind="experiment_execution",
            gate="method_experiment_approval",
            program=(
                "from pathlib import Path\n"
                "Path('output').mkdir()\n"
                "Path('output/result.json').write_text('new output\\n')\n"
            ),
            steps=[
                {
                    "step_id": "execute",
                    "argv": [sys.executable, "program.py"],
                    "timeout_seconds": 30,
                }
            ],
            suffix="UNRELATED-EXISTING",
            output_publish_path=publish_relative,
        )
        publish_path = self.project / publish_relative
        publish_path.parent.mkdir(parents=True, exist_ok=True)
        publish_path.write_text("must remain unchanged\n", encoding="utf-8")
        source_before = publish_path.read_bytes()
        state_before = self.state_path.read_bytes()
        exchange_before = exchange_path.read_bytes()

        result = self.run_stack(
            stage="experiment_results",
            request_id=request_id,
            exchange_id=exchange_id,
            exchange_path=exchange_path,
            attempt_id="REFERENCE-ATTEMPT-UNRELATED-EXISTING",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot overwrite an unrelated existing project file", result.stderr)
        self.assertEqual(publish_path.read_bytes(), source_before)
        self.assertEqual(self.state_path.read_bytes(), state_before)
        self.assertEqual(exchange_path.read_bytes(), exchange_before)

    def test_publish_race_cannot_clobber_a_file_created_after_acceptance(self) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        request_id, exchange_id, exchange_path, config = self.prepare_request(
            stage="experiment_results",
            operation_kind="experiment_execution",
            gate="method_experiment_approval",
            program=(
                "import time\n"
                "from pathlib import Path\n"
                "time.sleep(1.0)\n"
                "Path('output').mkdir()\n"
                "Path('output/result.json').write_text('adapter output\\n')\n"
            ),
            steps=[
                {
                    "step_id": "execute",
                    "argv": [sys.executable, "program.py"],
                    "timeout_seconds": 30,
                }
            ],
            suffix="PUBLISH-RACE",
        )
        command = [
            sys.executable,
            str(REFERENCE_STACK),
            "run",
            "--request-id",
            request_id,
            "--attempt-id",
            "REFERENCE-ATTEMPT-PUBLISH-RACE",
            "--stage",
            "experiment_results",
            "--exchange-path",
            str(exchange_path.relative_to(self.project)),
            "--exchange-artifact-id",
            exchange_id,
            "--project-root",
            str(self.project),
        ]
        process = subprocess.Popen(
            command,
            cwd=self.project,
            env=self.environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            exchange = json.loads(exchange_path.read_text(encoding="utf-8"))
            if exchange["receipts"]:
                break
            time.sleep(0.05)
        else:
            process.kill()
            self.fail("Reference Stack did not record accepted before the deadline")
        publish_path = self.project / config["expected_outputs"][0]["publish_path"]
        publish_path.parent.mkdir(parents=True, exist_ok=True)
        publish_path.write_text("concurrent sentinel\n", encoding="utf-8")

        stdout, stderr = process.communicate(timeout=15)

        self.assertNotEqual(process.returncode, 0, stdout + stderr)
        self.assertEqual(
            publish_path.read_text(encoding="utf-8"), "concurrent sentinel\n"
        )

    def test_result_race_cannot_clobber_a_file_created_after_acceptance(self) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        request_id, exchange_id, exchange_path, config = self.prepare_request(
            stage="experiment_results",
            operation_kind="experiment_execution",
            gate="method_experiment_approval",
            program=(
                "import time\n"
                "from pathlib import Path\n"
                "time.sleep(1.0)\n"
                "Path('output').mkdir()\n"
                "Path('output/result.json').write_text('adapter output\\n')\n"
            ),
            steps=[
                {
                    "step_id": "execute",
                    "argv": [sys.executable, "program.py"],
                    "timeout_seconds": 30,
                }
            ],
            suffix="RESULT-RACE",
        )
        command = [
            sys.executable,
            str(REFERENCE_STACK),
            "run",
            "--request-id",
            request_id,
            "--attempt-id",
            "REFERENCE-ATTEMPT-RESULT-RACE",
            "--stage",
            "experiment_results",
            "--exchange-path",
            str(exchange_path.relative_to(self.project)),
            "--exchange-artifact-id",
            exchange_id,
            "--project-root",
            str(self.project),
        ]
        process = subprocess.Popen(
            command,
            cwd=self.project,
            env=self.environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            exchange = json.loads(exchange_path.read_text(encoding="utf-8"))
            if exchange["receipts"]:
                break
            time.sleep(0.05)
        else:
            process.kill()
            self.fail("Reference Stack did not record accepted before the deadline")
        result_path = self.project / config["result_artifact"]["publish_path"]
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text("concurrent result sentinel\n", encoding="utf-8")

        stdout, stderr = process.communicate(timeout=15)

        self.assertNotEqual(process.returncode, 0, stdout + stderr)
        self.assertEqual(
            result_path.read_text(encoding="utf-8"),
            "concurrent result sentinel\n",
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
