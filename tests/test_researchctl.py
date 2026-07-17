from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from scripts.researchctl_core.manifest_commands import cmd_record_append
from scripts.researchctl_core import manifest_commands as manifest_commands_module
from scripts.researchctl_core.policy import load_policy
from scripts.researchctl_core import publish as publish_module
from scripts.researchctl_core.constants import ResearchCtlError
from scripts.researchctl_core.publish import cmd_publish_batch
from scripts.researchctl_core.store import state_mutation_lock

try:
    from .research_test_support import RESEARCHCTL, ResearchProjectTestCase
except ImportError:  # unittest discover -s tests
    from research_test_support import RESEARCHCTL, ResearchProjectTestCase


class ResearchCtlV2Test(ResearchProjectTestCase):
    def publication_item(
        self,
        source: Path,
        publish_path: str,
        role: str,
        artifact_id: str,
    ) -> dict[str, object]:
        content_hash, size_bytes = publish_module.hash_file_with_size(source)
        return {
            "source_path": str(source),
            "publish_path": publish_path,
            "role": role,
            "artifact_id": artifact_id,
            "expected_content_hash": content_hash,
            "expected_size_bytes": size_bytes,
        }

    def artifact_ref(
        self, role_reference: str, artifact_id: str
    ) -> dict[str, object]:
        stage, role = role_reference.split(".", 1)
        revision = self.artifact_entry(role_reference, artifact_id)[
            "revisions"
        ][-1]
        return {
            "label": f"artifacts.{stage}.{role}.{artifact_id}",
            "artifact_id": artifact_id,
            **revision,
        }

    def gate_binding(
        self, gate: str, *, target: str | None = None
    ) -> dict[str, object]:
        state = self.load_state()
        record = state["gates"][gate]
        if target is not None:
            record = record["targets"][target]
        decision = record["history"][-1]
        gate_ref: dict[str, object] = {"gate": gate}
        if target is not None:
            gate_ref["target"] = target
        return {
            "gate_ref": gate_ref,
            "gate_decision_id": decision["decision_id"],
            "artifact_refs": decision["artifact_refs"],
        }

    def adapter_request(
        self,
        *,
        request_id: str,
        operation_kind: str,
        payload_ref: dict[str, object],
        gate_binding: dict[str, object] | None,
        effect_class: str = "low_risk",
        human_authorization: dict[str, object] | None = None,
        retry_mode: str = "reconcile_before_retry",
        max_attempts: int = 2,
    ) -> dict[str, object]:
        timestamp = self.load_state()["updated_at"]
        if human_authorization is None and effect_class != "low_risk":
            human_authorization = {
                "authorization_id": f"AUTH-{request_id}",
                "actor": "test-researcher",
                "authorized_at": timestamp,
                "scope": f"Authorize exactly {request_id}.",
            }
        input_refs = [payload_ref]
        if isinstance(gate_binding, dict):
            for reference in gate_binding.get("artifact_refs", []):
                if reference not in input_refs:
                    input_refs.append(reference)
        return {
            "request_id": request_id,
            "operation_kind": operation_kind,
            "created_at": timestamp,
            "gate_binding": gate_binding,
            "payload": {
                "artifact_ref": payload_ref,
                "locator": f"#{request_id.lower()}",
            },
            "input_artifact_refs": input_refs,
            "effect_class": effect_class,
            "human_authorization": human_authorization,
            "retry_policy": {
                "mode": retry_mode,
                "max_attempts": max_attempts,
                "idempotency_key": (
                    None if retry_mode == "never" else f"IDEMP-{request_id}"
                ),
            },
        }

    def adapter_receipt(
        self,
        *,
        receipt_id: str,
        request: dict[str, object],
        request_hash: str,
        attempt_id: str,
        status: str,
        retry_of_attempt_id: str | None = None,
        supersedes: str | None = None,
        external_id: str | None = "job-001",
        message: str | None = None,
        observed_at: str | None = None,
    ) -> dict[str, object]:
        return {
            "receipt_id": receipt_id,
            "request_id": request["request_id"],
            "request_hash": request_hash,
            "attempt_id": attempt_id,
            "retry_of_attempt_id": retry_of_attempt_id,
            "supersedes": supersedes,
            "adapter": {
                "adapter_id": "fake-async",
                "adapter_version": "1.0.0",
                "protocol_version": "1.0",
            },
            "status": status,
            "observed_at": observed_at or self.load_state()["updated_at"],
            "external_id": external_id,
            "output_artifact_refs": [],
            "log_artifact_refs": [],
            "message": message or f"Adapter reported {status}.",
        }

    def adapter_manifest(
        self,
        *,
        stage: str,
        requests: list[dict[str, object]],
        receipts: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "stage": stage,
            "requests": requests,
            "receipts": receipts or [],
        }

    def write_adapter_manifest(
        self, manifest: dict[str, object], *, stage: str
    ) -> tuple[Path, subprocess.CompletedProcess[str]]:
        path = self.project / "work" / stage / "adapter-exchange.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _identifier, _path, result = self.register(
            f"{stage}.adapter_exchange",
            f"{stage.upper()}-ADAPTER-EXCHANGE",
            path=path,
        )
        return path, result

    def record_manifest(
        self,
        *,
        stage: str,
        source_role: str,
        source_artifact_id: str,
        records: list[dict[str, object]],
    ) -> dict[str, object]:
        revision = self.artifact_entry(
            f"{stage}.{source_role}", source_artifact_id
        )["current_revision"]
        materialized: list[dict[str, object]] = []
        for record in records:
            candidate = dict(record)
            candidate.setdefault(
                "source",
                {
                    "artifact_role": source_role,
                    "artifact_id": source_artifact_id,
                    "revision": revision,
                    "locator": f"#{candidate['record_id'].lower()}",
                },
            )
            candidate.setdefault("supersedes", None)
            candidate.setdefault("relations", [])
            materialized.append(candidate)
        return {
            "schema_version": "1.0",
            "stage": stage,
            "records": materialized,
        }

    def test_init_creates_only_the_v2_local_contract_and_is_idempotent(self) -> None:
        state = self.load_state()
        self.assertEqual(state["schema_version"], "2.0")
        self.assertEqual(state["workflow_version"], "2.0.0")
        self.assertEqual(
            state["lifecycle"],
            {"status": "active", "latest_decision_id": None, "history": []},
        )
        self.assertEqual(state["activation_history"], [])
        self.assertEqual(state["artifacts"], {})
        self.assertTrue((self.project / ".research/artifacts").is_dir())
        self.assertTrue((self.project / ".research/snapshots").is_dir())
        before = self.state_path.read_bytes()

        again = self.run_ctl("init")

        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertIn("state already exists; left unchanged", again.stdout)
        exclude = (self.project / ".git/info/exclude").read_text(encoding="utf-8")
        self.assertEqual(exclude.count(".research/"), 1)

    def test_artifact_publish_batch_rejects_all_items_before_any_publish(self) -> None:
        _existing_id, _existing_path, registered = self.register(
            "idea.batch_conflict",
            "BATCH-CONFLICT-EXISTING",
            content="existing canonical artifact\n",
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        source_one = self.project / "batch-source-one.txt"
        source_two = self.project / "batch-source-two.txt"
        source_one.write_text("first\n", encoding="utf-8")
        source_two.write_text("second\n", encoding="utf-8")
        destination_one = (
            ".research/artifacts/idea/reference-stack/ATTEMPT-001/first.txt"
        )
        destination_two = (
            ".research/artifacts/idea/reference-stack/ATTEMPT-001/second.txt"
        )
        manifest_path = self.project / "batch-manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "publications": [
                        self.publication_item(
                            source_one,
                            destination_one,
                            "batch_output",
                            "BATCH-OUTPUT-001",
                        ),
                        self.publication_item(
                            source_two,
                            destination_two,
                            "batch_conflict",
                            "BATCH-CONFLICT-DIFFERENT",
                        ),
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        state_before = self.state_path.read_bytes()

        result = self.run_ctl(
            "artifact",
            "publish-batch",
            "--stage",
            "idea",
            "--attempt-id",
            "ATTEMPT-001",
            "--manifest",
            str(manifest_path),
            "--json",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("one canonical artifact", result.stderr)
        self.assertFalse((self.project / destination_one).exists())
        self.assertFalse((self.project / destination_two).exists())
        self.assertEqual(self.state_path.read_bytes(), state_before)

    def test_artifact_publish_batch_is_no_clobber_and_idempotent(self) -> None:
        source = self.project / "batch-source.txt"
        source.write_text("batch payload\n", encoding="utf-8")
        destination = ".research/artifacts/idea/reference-stack/ATTEMPT-OK/output.txt"
        manifest_path = self.project / "batch-manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "publications": [
                        self.publication_item(
                            source,
                            destination,
                            "batch_output",
                            "BATCH-OUTPUT-001",
                        )
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        first = self.run_ctl(
            "artifact",
            "publish-batch",
            "--stage",
            "idea",
            "--attempt-id",
            "ATTEMPT-OK",
            "--manifest",
            str(manifest_path),
            "--json",
        )

        self.assertEqual(first.returncode, 0, first.stderr)
        payload = json.loads(first.stdout)
        self.assertEqual(payload["publications"][0]["result"], "registered")
        self.assertEqual((self.project / destination).read_text(), "batch payload\n")
        entry = self.artifact_entry("idea.batch_output", "BATCH-OUTPUT-001")
        self.assertEqual(entry["current_revision"], 1)
        state_after_first = self.state_path.read_bytes()

        second = self.run_ctl(
            "artifact",
            "publish-batch",
            "--stage",
            "idea",
            "--attempt-id",
            "ATTEMPT-OK",
            "--manifest",
            str(manifest_path),
            "--json",
        )

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(
            json.loads(second.stdout)["publications"][0]["result"],
            "already_registered",
        )
        self.assertEqual(self.state_path.read_bytes(), state_after_first)

    def test_artifact_publish_batch_leaves_only_unregistered_attempt_orphans_on_partial_failure(
        self,
    ) -> None:
        sources = [self.project / "batch-one.txt", self.project / "batch-two.txt"]
        sources[0].write_text("one\n", encoding="utf-8")
        sources[1].write_text("two\n", encoding="utf-8")
        destinations = [
            ".research/artifacts/idea/reference-stack/ATTEMPT-ROLLBACK/one.txt",
            ".research/artifacts/idea/reference-stack/ATTEMPT-ROLLBACK/two.txt",
        ]
        manifest_path = self.project / "batch-rollback.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "publications": [
                        self.publication_item(
                            sources[index],
                            destinations[index],
                            f"batch_output_{index + 1}",
                            f"BATCH-ROLLBACK-{index + 1}",
                        )
                        for index in range(2)
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        state_before = self.state_path.read_bytes()
        snapshots_before = {
            path.relative_to(self.project).as_posix()
            for path in (self.project / ".research/snapshots").rglob("*")
            if path.is_file()
        }
        original_copy = publish_module._copy_to_destination
        calls = 0

        def fail_second(
            root: Path, publication: publish_module.Publication
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ResearchCtlError("injected second publication failure")
            original_copy(root, publication)

        with (
            mock.patch.object(
                publish_module, "_copy_to_destination", side_effect=fail_second
            ),
            self.assertRaisesRegex(
                ResearchCtlError, "injected second publication failure"
            ),
        ):
            cmd_publish_batch(
                self.project,
                load_policy(),
                Namespace(
                    stage="idea",
                    attempt_id="ATTEMPT-ROLLBACK",
                    manifest=str(manifest_path),
                    json=True,
                ),
            )

        self.assertEqual(self.state_path.read_bytes(), state_before)
        self.assertEqual((self.project / destinations[0]).read_text(), "one\n")
        self.assertFalse((self.project / destinations[1]).exists())
        snapshots_after = {
            path.relative_to(self.project).as_posix()
            for path in (self.project / ".research/snapshots").rglob("*")
            if path.is_file()
        }
        self.assertEqual(snapshots_after, snapshots_before)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO creation is POSIX-only")
    def test_artifact_publish_batch_rejects_a_fifo_manifest_without_blocking(
        self,
    ) -> None:
        fifo = self.project / "publication-manifest.fifo"
        os.mkfifo(fifo)

        result = subprocess.run(
            [
                sys.executable,
                str(RESEARCHCTL),
                "artifact",
                "publish-batch",
                "--stage",
                "idea",
                "--attempt-id",
                "ATTEMPT-FIFO-MANIFEST",
                "--manifest",
                str(fifo),
            ],
            cwd=self.project,
            env=self.environment(),
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("regular file", result.stderr)

    def test_artifact_publish_batch_preserves_commit_when_state_writer_raises_after_replace(
        self,
    ) -> None:
        source = self.project / "batch-commit-source.txt"
        source.write_text("committed payload\n", encoding="utf-8")
        destination = (
            ".research/artifacts/idea/reference-stack/ATTEMPT-COMMIT/output.txt"
        )
        manifest_path = self.project / "batch-commit-manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "publications": [
                        self.publication_item(
                            source,
                            destination,
                            "batch_commit_output",
                            "BATCH-COMMIT-001",
                        )
                    ],
                }
            ),
            encoding="utf-8",
        )
        original_writer = publish_module.write_mutated_state

        def commit_then_interrupt(root: Path, state: dict[str, object]) -> None:
            original_writer(root, state)
            raise KeyboardInterrupt("injected post-commit interrupt")

        with (
            mock.patch.object(
                publish_module,
                "write_mutated_state",
                side_effect=commit_then_interrupt,
            ),
            self.assertRaisesRegex(KeyboardInterrupt, "post-commit"),
        ):
            cmd_publish_batch(
                self.project,
                load_policy(),
                Namespace(
                    stage="idea",
                    attempt_id="ATTEMPT-COMMIT",
                    manifest=str(manifest_path),
                    json=True,
                ),
            )

        self.assertEqual((self.project / destination).read_text(), "committed payload\n")
        revision = self.artifact_entry(
            "idea.batch_commit_output", "BATCH-COMMIT-001"
        )["revisions"][0]
        self.assertTrue((self.project / revision["snapshot_path"]).is_file())
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

    def test_artifact_publish_batch_retains_unregistered_revision_two_orphans_before_state_replace(
        self,
    ) -> None:
        source = self.project / "batch-r2-source.txt"
        source.write_text("revision one\n", encoding="utf-8")

        def manifest(attempt: str, destination: str) -> Path:
            path = self.project / f"batch-{attempt}.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "publications": [
                            self.publication_item(
                                source,
                                destination,
                                "batch_revisioned_output",
                                "BATCH-REVISIONED-001",
                            )
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return path

        first_destination = (
            ".research/artifacts/idea/reference-stack/ATTEMPT-R1/output.txt"
        )
        first_manifest = manifest("ATTEMPT-R1", first_destination)
        first = self.run_ctl(
            "artifact",
            "publish-batch",
            "--stage",
            "idea",
            "--attempt-id",
            "ATTEMPT-R1",
            "--manifest",
            str(first_manifest),
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        baseline_state = self.state_path.read_bytes()
        baseline_snapshots = {
            path.relative_to(self.project).as_posix()
            for path in (self.project / ".research/snapshots").rglob("*")
            if path.is_file()
        }

        source.write_text("revision two\n", encoding="utf-8")
        second_destination = (
            ".research/artifacts/idea/reference-stack/ATTEMPT-R2/output.txt"
        )
        second_manifest = manifest("ATTEMPT-R2", second_destination)
        with (
            mock.patch.object(
                publish_module,
                "write_mutated_state",
                side_effect=ResearchCtlError("injected pre-replace failure"),
            ),
            self.assertRaisesRegex(ResearchCtlError, "pre-replace failure"),
        ):
            cmd_publish_batch(
                self.project,
                load_policy(),
                Namespace(
                    stage="idea",
                    attempt_id="ATTEMPT-R2",
                    manifest=str(second_manifest),
                    json=True,
                ),
            )

        self.assertEqual(self.state_path.read_bytes(), baseline_state)
        self.assertEqual(
            (self.project / second_destination).read_text(), "revision two\n"
        )
        snapshots_after = {
            path.relative_to(self.project).as_posix()
            for path in (self.project / ".research/snapshots").rglob("*")
            if path.is_file()
        }
        self.assertTrue(baseline_snapshots < snapshots_after)
        self.assertEqual(len(snapshots_after), len(baseline_snapshots) + 1)
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

    def test_artifact_publish_batch_does_not_remove_a_matching_snapshot_it_did_not_create(
        self,
    ) -> None:
        source = self.project / "batch-snapshot-race.txt"
        source.write_text("matching snapshot\n", encoding="utf-8")
        destination = (
            ".research/artifacts/idea/reference-stack/ATTEMPT-SNAPSHOT-RACE/output.txt"
        )
        manifest_path = self.project / "batch-snapshot-race.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "publications": [
                        self.publication_item(
                            source,
                            destination,
                            "batch_snapshot_race",
                            "BATCH-SNAPSHOT-RACE-001",
                        )
                    ],
                }
            ),
            encoding="utf-8",
        )
        original_snapshot = publish_module.create_revision_snapshot_result
        retained_snapshot: Path | None = None
        state_before = self.state_path.read_bytes()

        def competing_snapshot(*args: object, **kwargs: object):
            nonlocal retained_snapshot
            result = original_snapshot(*args, **kwargs)
            retained_snapshot = self.project / result.stored_path
            return type(result)(
                stored_path=result.stored_path,
                created=False,
                identity=None,
            )

        with (
            mock.patch.object(
                publish_module,
                "create_revision_snapshot_result",
                side_effect=competing_snapshot,
            ),
            mock.patch.object(
                publish_module,
                "write_mutated_state",
                side_effect=ResearchCtlError("injected pre-state failure"),
            ),
            self.assertRaisesRegex(ResearchCtlError, "injected pre-state failure"),
        ):
            cmd_publish_batch(
                self.project,
                load_policy(),
                Namespace(
                    stage="idea",
                    attempt_id="ATTEMPT-SNAPSHOT-RACE",
                    manifest=str(manifest_path),
                    json=True,
                ),
            )

        self.assertIsNotNone(retained_snapshot)
        assert retained_snapshot is not None
        self.assertEqual(retained_snapshot.read_text(), "matching snapshot\n")
        self.assertEqual((self.project / destination).read_text(), "matching snapshot\n")
        self.assertEqual(self.state_path.read_bytes(), state_before)

    @unittest.skipIf(sys.platform == "win32", "symlink semantics differ on Windows")
    def test_artifact_publish_batch_rejects_a_symlinked_source_parent(self) -> None:
        real_parent = self.project / "real-source-parent"
        real_parent.mkdir()
        source = real_parent / "output.txt"
        source.write_text("must not publish\n", encoding="utf-8")
        linked_parent = self.project / "linked-source-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        destination = (
            ".research/artifacts/idea/reference-stack/ATTEMPT-SOURCE-LINK/output.txt"
        )
        manifest_path = self.project / "batch-source-link.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "publications": [
                        self.publication_item(
                            linked_parent / "output.txt",
                            destination,
                            "batch_source_link",
                            "BATCH-SOURCE-LINK-001",
                        )
                    ],
                }
            ),
            encoding="utf-8",
        )
        before = self.state_path.read_bytes()

        result = self.run_ctl(
            "artifact",
            "publish-batch",
            "--stage",
            "idea",
            "--attempt-id",
            "ATTEMPT-SOURCE-LINK",
            "--manifest",
            str(manifest_path),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot traverse a symlink", result.stderr)
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertFalse((self.project / destination).exists())

    @unittest.skipIf(sys.platform == "win32", "dir_fd semantics differ on Windows")
    def test_direct_publication_never_adopts_or_removes_a_replacement_file(
        self,
    ) -> None:
        source_path = self.project / "publication-identity-source.txt"
        source_path.write_text("owned publication\n", encoding="utf-8")
        source, source_identity, source_parents = publish_module._source_path(
            self.project, str(source_path)
        )
        content_hash, size_bytes = publish_module.hash_file_with_size(source)
        destination = self.project / (
            ".research/artifacts/idea/reference-stack/"
            "ATTEMPT-IDENTITY-RACE/output.txt"
        )
        publication = publish_module.Publication(
            source=source,
            source_identity=source_identity,
            source_parent_identities=source_parents,
            publish_path=destination.relative_to(self.project).as_posix(),
            destination=destination,
            role="identity_race_output",
            artifact_id="IDENTITY-RACE-OUTPUT-001",
            content_hash=content_hash,
            size_bytes=size_bytes,
            existing_revision=None,
            entry=None,
            next_revision=1,
        )
        original_verify = publish_module._verify_source_topology
        calls = 0

        def replace_after_copy(item: publish_module.Publication) -> None:
            nonlocal calls
            original_verify(item)
            calls += 1
            if calls == 2:
                item.destination.unlink()
                item.destination.write_text(
                    "unrelated replacement\n", encoding="utf-8"
                )

        try:
            with (
                mock.patch.object(
                    publish_module,
                    "_verify_source_topology",
                    side_effect=replace_after_copy,
                ),
                self.assertRaisesRegex(ResearchCtlError, "identity changed"),
            ):
                publish_module._copy_to_destination(self.project, publication)
        finally:
            publish_module._cleanup([publication])

        self.assertEqual(
            destination.read_text(encoding="utf-8"), "unrelated replacement\n"
        )

    def test_adapter_request_is_registered_then_verified_without_adapter_state(
        self,
    ) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        payload_id, _payload, registered = self.register(
            "experiment_results.experiment_request",
            "EXPERIMENT-REQUEST-001",
            content='{"command":["python3","train.py"]}\n',
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        request = self.adapter_request(
            request_id="REQUEST-EXPERIMENT-001",
            operation_kind="experiment_execution",
            payload_ref=self.artifact_ref(
                "experiment_results.experiment_request", payload_id
            ),
            gate_binding=self.gate_binding("method_experiment_approval"),
            effect_class="costly_compute",
        )
        _path, exchange = self.write_adapter_manifest(
            self.adapter_manifest(
                stage="experiment_results", requests=[request]
            ),
            stage="experiment_results",
        )
        self.assertEqual(exchange.returncode, 0, exchange.stderr)

        verified = self.run_ctl(
            "adapter",
            "verify",
            "REQUEST-EXPERIMENT-001",
            "--attempt-id",
            "ATTEMPT-EXPERIMENT-001",
        )

        self.assertEqual(verified.returncode, 0, verified.stderr)
        envelope = json.loads(verified.stdout)
        self.assertEqual(envelope["verification"], "accepted")
        self.assertEqual(envelope["request"]["request_id"], request["request_id"])
        self.assertRegex(envelope["request_hash"], r"^sha256:[0-9a-f]{64}$")
        state = self.load_state()
        self.assertNotIn("adapters", state)
        self.assertNotIn("operations", state)
        self.assertIn(
            "adapter_exchange", state["artifacts"]["experiment_results"]
        )

    def test_stale_gate_blocks_dispatch_but_not_late_unknown_receipt(self) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        payload_id, _payload, registered = self.register(
            "experiment_results.experiment_request",
            "EXPERIMENT-REQUEST-STALE",
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        request = self.adapter_request(
            request_id="REQUEST-EXPERIMENT-STALE",
            operation_kind="experiment_execution",
            payload_ref=self.artifact_ref(
                "experiment_results.experiment_request", payload_id
            ),
            gate_binding=self.gate_binding("method_experiment_approval"),
        )
        manifest = self.adapter_manifest(
            stage="experiment_results", requests=[request]
        )
        path, exchange = self.write_adapter_manifest(
            manifest, stage="experiment_results"
        )
        self.assertEqual(exchange.returncode, 0, exchange.stderr)
        first_verify = self.run_ctl(
            "adapter",
            "verify",
            request["request_id"],
            "--attempt-id",
            "ATTEMPT-STALE-001",
        )
        self.assertEqual(first_verify.returncode, 0, first_verify.stderr)
        request_hash = json.loads(first_verify.stdout)["request_hash"]

        accepted = self.adapter_receipt(
            receipt_id="RECEIPT-STALE-ACCEPTED",
            request=request,
            request_hash=request_hash,
            attempt_id="ATTEMPT-STALE-001",
            status="accepted",
            message="Attempt journal persisted before the external side effect.",
        )
        manifest["receipts"] = [accepted]
        path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        _identifier, _path, journaled = self.register(
            "experiment_results.adapter_exchange",
            "EXPERIMENT_RESULTS-ADAPTER-EXCHANGE",
            path=path,
        )
        self.assertEqual(journaled.returncode, 0, journaled.stderr)

        reopened = self.gate("reopen", "method_experiment_approval")
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        blocked = self.run_ctl(
            "adapter",
            "verify",
            request["request_id"],
            "--attempt-id",
            "ATTEMPT-STALE-002",
        )
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("current approved Gate binding", blocked.stderr)

        manifest["receipts"].append(
            self.adapter_receipt(
                receipt_id="RECEIPT-STALE-UNKNOWN",
                request=request,
                request_hash=request_hash,
                attempt_id="ATTEMPT-STALE-001",
                status="unknown",
                supersedes="RECEIPT-STALE-ACCEPTED",
                message="Dispatch outcome is unknown after transport loss.",
            )
        )
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _identifier, _path, imported = self.register(
            "experiment_results.adapter_exchange",
            "EXPERIMENT_RESULTS-ADAPTER-EXCHANGE",
            path=path,
        )
        self.assertEqual(imported.returncode, 0, imported.stderr)
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

    def test_unknown_reconcile_policy_blocks_blind_retry(self) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        payload_id, _payload, registered = self.register(
            "experiment_results.experiment_request", "EXPERIMENT-REQUEST-UNKNOWN"
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        request = self.adapter_request(
            request_id="REQUEST-EXPERIMENT-UNKNOWN",
            operation_kind="experiment_execution",
            payload_ref=self.artifact_ref(
                "experiment_results.experiment_request", payload_id
            ),
            gate_binding=self.gate_binding("method_experiment_approval"),
        )
        manifest = self.adapter_manifest(
            stage="experiment_results", requests=[request]
        )
        path, exchange = self.write_adapter_manifest(
            manifest, stage="experiment_results"
        )
        self.assertEqual(exchange.returncode, 0, exchange.stderr)
        verified = self.run_ctl(
            "adapter",
            "verify",
            request["request_id"],
            "--attempt-id",
            "ATTEMPT-UNKNOWN-001",
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        request_hash = json.loads(verified.stdout)["request_hash"]
        manifest["receipts"] = [
            self.adapter_receipt(
                receipt_id="RECEIPT-UNKNOWN-ACCEPTED",
                request=request,
                request_hash=request_hash,
                attempt_id="ATTEMPT-UNKNOWN-001",
                status="accepted",
                external_id="job-unknown",
                message="Attempt journal persisted before the external side effect.",
            )
        ]
        path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        _identifier, _path, journaled = self.register(
            "experiment_results.adapter_exchange",
            "EXPERIMENT_RESULTS-ADAPTER-EXCHANGE",
            path=path,
        )
        self.assertEqual(journaled.returncode, 0, journaled.stderr)
        manifest["receipts"].append(
            self.adapter_receipt(
                receipt_id="RECEIPT-UNKNOWN-OBSERVED",
                request=request,
                request_hash=request_hash,
                attempt_id="ATTEMPT-UNKNOWN-001",
                status="unknown",
                supersedes="RECEIPT-UNKNOWN-ACCEPTED",
                external_id="job-unknown",
                message="Reconcile this attempt before any retry.",
            )
        )
        path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        _identifier, _path, imported = self.register(
            "experiment_results.adapter_exchange",
            "EXPERIMENT_RESULTS-ADAPTER-EXCHANGE",
            path=path,
        )
        self.assertEqual(imported.returncode, 0, imported.stderr)

        retry = self.run_ctl(
            "adapter",
            "verify",
            request["request_id"],
            "--attempt-id",
            "ATTEMPT-UNKNOWN-002",
            "--retry-of-attempt-id",
            "ATTEMPT-UNKNOWN-001",
        )

        self.assertEqual(retry.returncode, 2)
        self.assertIn("reconcile", retry.stderr)

    def test_dispatch_journal_requires_current_gate_before_side_effect(self) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        payload_id, _payload, registered = self.register(
            "experiment_results.experiment_request", "EXPERIMENT-REQUEST-JOURNAL"
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        request = self.adapter_request(
            request_id="REQUEST-EXPERIMENT-JOURNAL",
            operation_kind="experiment_execution",
            payload_ref=self.artifact_ref(
                "experiment_results.experiment_request", payload_id
            ),
            gate_binding=self.gate_binding("method_experiment_approval"),
        )
        manifest = self.adapter_manifest(
            stage="experiment_results", requests=[request]
        )
        path, exchange = self.write_adapter_manifest(
            manifest, stage="experiment_results"
        )
        self.assertEqual(exchange.returncode, 0, exchange.stderr)
        verified = self.run_ctl(
            "adapter",
            "verify",
            request["request_id"],
            "--attempt-id",
            "ATTEMPT-JOURNAL-001",
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        request_hash = json.loads(verified.stdout)["request_hash"]
        self.assertEqual(
            self.gate("reopen", "method_experiment_approval").returncode, 0
        )
        state_before = self.state_path.read_bytes()
        manifest["receipts"] = [
            self.adapter_receipt(
                receipt_id="RECEIPT-JOURNAL-ACCEPTED",
                request=request,
                request_hash=request_hash,
                attempt_id="ATTEMPT-JOURNAL-001",
                status="accepted",
            )
        ]
        path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

        _identifier, _path, rejected = self.register(
            "experiment_results.adapter_exchange",
            "EXPERIMENT_RESULTS-ADAPTER-EXCHANGE",
            path=path,
        )

        self.assertEqual(rejected.returncode, 2)
        self.assertIn("current approved Gate binding", rejected.stderr)
        self.assertEqual(self.state_path.read_bytes(), state_before)

    def test_dispatch_journal_is_durable_before_later_attempt_receipts(self) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        payload_id, _payload, registered = self.register(
            "experiment_results.experiment_request", "EXPERIMENT-REQUEST-ORDER"
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        request = self.adapter_request(
            request_id="REQUEST-EXPERIMENT-ORDER",
            operation_kind="experiment_execution",
            payload_ref=self.artifact_ref(
                "experiment_results.experiment_request", payload_id
            ),
            gate_binding=self.gate_binding("method_experiment_approval"),
        )
        manifest = self.adapter_manifest(
            stage="experiment_results", requests=[request]
        )
        path, exchange = self.write_adapter_manifest(
            manifest, stage="experiment_results"
        )
        self.assertEqual(exchange.returncode, 0, exchange.stderr)
        verified = self.run_ctl(
            "adapter",
            "verify",
            request["request_id"],
            "--attempt-id",
            "ATTEMPT-ORDER-001",
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        request_hash = json.loads(verified.stdout)["request_hash"]
        accepted = self.adapter_receipt(
            receipt_id="RECEIPT-ORDER-ACCEPTED",
            request=request,
            request_hash=request_hash,
            attempt_id="ATTEMPT-ORDER-001",
            status="accepted",
        )
        succeeded = self.adapter_receipt(
            receipt_id="RECEIPT-ORDER-SUCCEEDED-EARLY",
            request=request,
            request_hash=request_hash,
            attempt_id="ATTEMPT-ORDER-001",
            status="succeeded",
            supersedes="RECEIPT-ORDER-ACCEPTED",
        )
        manifest["receipts"] = [accepted, succeeded]
        path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        state_before = self.state_path.read_bytes()

        _identifier, _path, combined = self.register(
            "experiment_results.adapter_exchange",
            "EXPERIMENT_RESULTS-ADAPTER-EXCHANGE",
            path=path,
        )

        self.assertEqual(combined.returncode, 2)
        self.assertIn("before appending any later observation", combined.stderr)
        self.assertEqual(self.state_path.read_bytes(), state_before)

        manifest["receipts"] = [accepted]
        path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        _identifier, _path, journaled = self.register(
            "experiment_results.adapter_exchange",
            "EXPERIMENT_RESULTS-ADAPTER-EXCHANGE",
            path=path,
        )
        self.assertEqual(journaled.returncode, 0, journaled.stderr)
        succeeded = self.adapter_receipt(
            receipt_id="RECEIPT-ORDER-SUCCEEDED",
            request=request,
            request_hash=request_hash,
            attempt_id="ATTEMPT-ORDER-001",
            status="succeeded",
            supersedes="RECEIPT-ORDER-ACCEPTED",
        )
        manifest["receipts"].append(succeeded)
        path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        _identifier, _path, completed = self.register(
            "experiment_results.adapter_exchange",
            "EXPERIMENT_RESULTS-ADAPTER-EXCHANGE",
            path=path,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_doctor_replays_historical_dispatch_journal_authority(self) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        payload_id, _payload, registered = self.register(
            "experiment_results.experiment_request", "EXPERIMENT-REQUEST-AUDIT"
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        request = self.adapter_request(
            request_id="REQUEST-EXPERIMENT-AUDIT",
            operation_kind="experiment_execution",
            payload_ref=self.artifact_ref(
                "experiment_results.experiment_request", payload_id
            ),
            gate_binding=self.gate_binding("method_experiment_approval"),
        )
        manifest = self.adapter_manifest(
            stage="experiment_results", requests=[request]
        )
        path, exchange = self.write_adapter_manifest(
            manifest, stage="experiment_results"
        )
        self.assertEqual(exchange.returncode, 0, exchange.stderr)
        verified = self.run_ctl(
            "adapter",
            "verify",
            request["request_id"],
            "--attempt-id",
            "ATTEMPT-AUDIT-001",
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        manifest["receipts"] = [
            self.adapter_receipt(
                receipt_id="RECEIPT-AUDIT-ACCEPTED",
                request=request,
                request_hash=json.loads(verified.stdout)["request_hash"],
                attempt_id="ATTEMPT-AUDIT-001",
                status="accepted",
            )
        ]
        path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        _identifier, _path, journaled = self.register(
            "experiment_results.adapter_exchange",
            "EXPERIMENT_RESULTS-ADAPTER-EXCHANGE",
            path=path,
        )
        self.assertEqual(journaled.returncode, 0, journaled.stderr)
        self.assertEqual(
            self.gate("reopen", "method_experiment_approval").returncode, 0
        )

        state = self.load_state()
        revisions = state["artifacts"]["experiment_results"][
            "adapter_exchange"
        ]["EXPERIMENT_RESULTS-ADAPTER-EXCHANGE"]["revisions"]
        revisions[1]["registered_at"] = state["updated_at"]
        self.write_state(state)

        doctor = self.run_ctl("doctor")

        self.assertEqual(doctor.returncode, 1)
        self.assertIn("when first registered", doctor.stdout)

    def test_doctor_rejects_historical_same_revision_dispatch_and_result(
        self,
    ) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        payload_id, _payload, registered = self.register(
            "experiment_results.experiment_request", "EXPERIMENT-REQUEST-FORGED"
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        request = self.adapter_request(
            request_id="REQUEST-EXPERIMENT-FORGED",
            operation_kind="experiment_execution",
            payload_ref=self.artifact_ref(
                "experiment_results.experiment_request", payload_id
            ),
            gate_binding=self.gate_binding("method_experiment_approval"),
        )
        manifest = self.adapter_manifest(
            stage="experiment_results", requests=[request]
        )
        path, exchange = self.write_adapter_manifest(
            manifest, stage="experiment_results"
        )
        self.assertEqual(exchange.returncode, 0, exchange.stderr)
        verified = self.run_ctl(
            "adapter",
            "verify",
            request["request_id"],
            "--attempt-id",
            "ATTEMPT-FORGED-001",
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        request_hash = json.loads(verified.stdout)["request_hash"]
        accepted = self.adapter_receipt(
            receipt_id="RECEIPT-FORGED-ACCEPTED",
            request=request,
            request_hash=request_hash,
            attempt_id="ATTEMPT-FORGED-001",
            status="accepted",
        )
        manifest["receipts"] = [accepted]
        path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        _identifier, _path, journaled = self.register(
            "experiment_results.adapter_exchange",
            "EXPERIMENT_RESULTS-ADAPTER-EXCHANGE",
            path=path,
        )
        self.assertEqual(journaled.returncode, 0, journaled.stderr)

        manifest["receipts"].append(
            self.adapter_receipt(
                receipt_id="RECEIPT-FORGED-SUCCEEDED",
                request=request,
                request_hash=request_hash,
                attempt_id="ATTEMPT-FORGED-001",
                status="succeeded",
                supersedes="RECEIPT-FORGED-ACCEPTED",
            )
        )
        forged = (
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        ).encode()
        state = self.load_state()
        revisions = state["artifacts"]["experiment_results"][
            "adapter_exchange"
        ]["EXPERIMENT_RESULTS-ADAPTER-EXCHANGE"]["revisions"]
        journal_revision = revisions[1]
        path.write_bytes(forged)
        (self.project / journal_revision["snapshot_path"]).write_bytes(forged)
        journal_revision["content_hash"] = (
            "sha256:" + hashlib.sha256(forged).hexdigest()
        )
        journal_revision["size_bytes"] = len(forged)
        self.write_state(state)

        doctor = self.run_ctl("doctor")

        self.assertEqual(doctor.returncode, 1)
        self.assertIn("before appending any later observation", doctor.stdout)

    def test_late_nonconforming_fact_is_preserved_without_dispatch_authority(
        self,
    ) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        payload_id, _payload, registered = self.register(
            "experiment_results.experiment_request", "EXPERIMENT-REQUEST-LATE"
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        request = self.adapter_request(
            request_id="REQUEST-EXPERIMENT-LATE",
            operation_kind="experiment_execution",
            payload_ref=self.artifact_ref(
                "experiment_results.experiment_request", payload_id
            ),
            gate_binding=self.gate_binding("method_experiment_approval"),
        )
        manifest = self.adapter_manifest(
            stage="experiment_results", requests=[request]
        )
        path, exchange = self.write_adapter_manifest(
            manifest, stage="experiment_results"
        )
        self.assertEqual(exchange.returncode, 0, exchange.stderr)
        verified = self.run_ctl(
            "adapter",
            "verify",
            request["request_id"],
            "--attempt-id",
            "ATTEMPT-LATE-001",
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(
            self.gate("reopen", "method_experiment_approval").returncode, 0
        )
        manifest["receipts"] = [
            self.adapter_receipt(
                receipt_id="RECEIPT-LATE-UNKNOWN",
                request=request,
                request_hash=json.loads(verified.stdout)["request_hash"],
                attempt_id="ATTEMPT-LATE-001",
                status="unknown",
                message="Observed off-contract work after losing transport state.",
            )
        ]
        path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

        _identifier, _path, imported = self.register(
            "experiment_results.adapter_exchange",
            "EXPERIMENT_RESULTS-ADAPTER-EXCHANGE",
            path=path,
        )

        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertIn("nonconforming fact import", imported.stderr)
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        self.assertIn("nonconforming fact import", doctor.stdout)

    def test_gate_artifacts_are_operational_inputs_and_release_payload_is_exact(
        self,
    ) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        payload_id, _payload, registered = self.register(
            "experiment_results.experiment_request", "EXPERIMENT-REQUEST-INPUTS"
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        request = self.adapter_request(
            request_id="REQUEST-EXPERIMENT-INPUTS",
            operation_kind="experiment_execution",
            payload_ref=self.artifact_ref(
                "experiment_results.experiment_request", payload_id
            ),
            gate_binding=self.gate_binding("method_experiment_approval"),
        )
        request["input_artifact_refs"] = [request["payload"]["artifact_ref"]]
        _path, rejected_inputs = self.write_adapter_manifest(
            self.adapter_manifest(stage="experiment_results", requests=[request]),
            stage="experiment_results",
        )
        self.assertEqual(rejected_inputs.returncode, 2)
        self.assertIn("must include approved Gate", rejected_inputs.stderr)

        self.assertEqual(self.approve_gate("claim_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate(
                "release", release_target="initial_submission"
            ).returncode,
            0,
        )
        unrelated_id, _unrelated, unrelated_registered = self.register(
            "revision.release_request", "UNAPPROVED-RELEASE-PAYLOAD"
        )
        self.assertEqual(
            unrelated_registered.returncode, 0, unrelated_registered.stderr
        )
        release_binding = self.gate_binding(
            "release", target="initial_submission"
        )
        release_request = self.adapter_request(
            request_id="REQUEST-RELEASE-UNBOUND",
            operation_kind="external_release",
            payload_ref=self.artifact_ref(
                "revision.release_request", unrelated_id
            ),
            gate_binding=release_binding,
            effect_class="external_release",
        )
        _path, rejected_payload = self.write_adapter_manifest(
            self.adapter_manifest(stage="revision", requests=[release_request]),
            stage="revision",
        )
        self.assertEqual(rejected_payload.returncode, 2)
        self.assertIn("approved release package", rejected_payload.stderr)

        exact_payload_request = self.adapter_request(
            request_id="REQUEST-RELEASE-EXTRA-INPUT",
            operation_kind="external_release",
            payload_ref=release_binding["artifact_refs"][0],
            gate_binding=release_binding,
            effect_class="external_release",
        )
        exact_payload_request["input_artifact_refs"].append(
            self.artifact_ref("revision.release_request", unrelated_id)
        )
        _path, rejected_extra = self.write_adapter_manifest(
            self.adapter_manifest(
                stage="revision", requests=[exact_payload_request]
            ),
            stage="revision",
        )
        self.assertEqual(rejected_extra.returncode, 2)
        self.assertIn("without extra artifacts", rejected_extra.stderr)

    def test_adapter_exchange_is_append_only_and_writing_uses_claim_gate(self) -> None:
        self.advance_through_claim_freeze()
        payload_id, _payload, registered = self.register(
            "paper.build_request", "PAPER-BUILD-REQUEST-001"
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        request = self.adapter_request(
            request_id="REQUEST-PAPER-001",
            operation_kind="paper_production",
            payload_ref=self.artifact_ref("paper.build_request", payload_id),
            gate_binding=self.gate_binding("claim_freeze"),
        )
        wrong_request = json.loads(json.dumps(request))
        wrong_request["gate_binding"] = self.gate_binding(
            "method_experiment_approval"
        )
        _wrong_path, wrong_gate = self.write_adapter_manifest(
            self.adapter_manifest(stage="paper", requests=[wrong_request]),
            stage="paper",
        )
        self.assertEqual(wrong_gate.returncode, 2)
        self.assertIn("policy-required GateRef", wrong_gate.stderr)

        manifest = self.adapter_manifest(stage="paper", requests=[request])
        path, exchange = self.write_adapter_manifest(manifest, stage="paper")
        self.assertEqual(exchange.returncode, 0, exchange.stderr)

        request["payload"]["locator"] = "#silently-rewritten"
        path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        _identifier, _path, rewritten = self.register(
            "paper.adapter_exchange", "PAPER-ADAPTER-EXCHANGE", path=path
        )

        self.assertEqual(rewritten.returncode, 2)
        self.assertIn("append-only", rewritten.stderr)

    def test_external_release_requires_action_specific_human_authorization(self) -> None:
        self.advance_through_claim_freeze()
        released = self.approve_gate(
            "release", release_target="initial_submission"
        )
        self.assertEqual(released.returncode, 0, released.stderr)
        release_binding = self.gate_binding(
            "release", target="initial_submission"
        )
        request = self.adapter_request(
            request_id="REQUEST-RELEASE-001",
            operation_kind="external_release",
            payload_ref=release_binding["artifact_refs"][0],
            gate_binding=release_binding,
            effect_class="external_release",
        )
        request["human_authorization"] = None
        _path, rejected = self.write_adapter_manifest(
            self.adapter_manifest(stage="revision", requests=[request]),
            stage="revision",
        )

        self.assertEqual(rejected.returncode, 2)
        self.assertIn("human_authorization", rejected.stderr)

    def test_adapter_timestamps_cannot_postdate_first_registration(self) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        payload_id, _payload, registered = self.register(
            "experiment_results.experiment_request", "EXPERIMENT-REQUEST-TIME"
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        binding = self.gate_binding("method_experiment_approval")
        request = self.adapter_request(
            request_id="REQUEST-EXPERIMENT-FUTURE-CREATED",
            operation_kind="experiment_execution",
            payload_ref=self.artifact_ref(
                "experiment_results.experiment_request", payload_id
            ),
            gate_binding=binding,
        )
        request["created_at"] = "2999-01-01T00:00:00Z"
        state_before = self.state_path.read_bytes()
        _path, future_created = self.write_adapter_manifest(
            self.adapter_manifest(stage="experiment_results", requests=[request]),
            stage="experiment_results",
        )
        self.assertEqual(future_created.returncode, 2)
        self.assertIn("created_at cannot follow", future_created.stderr)
        self.assertEqual(self.state_path.read_bytes(), state_before)

        request = self.adapter_request(
            request_id="REQUEST-EXPERIMENT-FUTURE-AUTH",
            operation_kind="experiment_execution",
            payload_ref=self.artifact_ref(
                "experiment_results.experiment_request", payload_id
            ),
            gate_binding=binding,
            effect_class="costly_compute",
        )
        request["human_authorization"]["authorized_at"] = "2999-01-01T00:00:00Z"
        _path, future_authorized = self.write_adapter_manifest(
            self.adapter_manifest(stage="experiment_results", requests=[request]),
            stage="experiment_results",
        )
        self.assertEqual(future_authorized.returncode, 2)
        self.assertIn("authorized_at cannot follow", future_authorized.stderr)
        self.assertEqual(self.state_path.read_bytes(), state_before)

        request = self.adapter_request(
            request_id="REQUEST-EXPERIMENT-FUTURE-RECEIPT",
            operation_kind="experiment_execution",
            payload_ref=self.artifact_ref(
                "experiment_results.experiment_request", payload_id
            ),
            gate_binding=binding,
        )
        manifest = self.adapter_manifest(
            stage="experiment_results", requests=[request]
        )
        path, exchange = self.write_adapter_manifest(
            manifest, stage="experiment_results"
        )
        self.assertEqual(exchange.returncode, 0, exchange.stderr)
        verified = self.run_ctl(
            "adapter",
            "verify",
            request["request_id"],
            "--attempt-id",
            "ATTEMPT-FUTURE-RECEIPT",
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        manifest["receipts"] = [
            self.adapter_receipt(
                receipt_id="RECEIPT-FUTURE-ACCEPTED",
                request=request,
                request_hash=json.loads(verified.stdout)["request_hash"],
                attempt_id="ATTEMPT-FUTURE-RECEIPT",
                status="accepted",
                observed_at="2999-01-01T00:00:00Z",
            )
        ]
        path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        state_before_receipt = self.state_path.read_bytes()
        _identifier, _path, future_observed = self.register(
            "experiment_results.adapter_exchange",
            "EXPERIMENT_RESULTS-ADAPTER-EXCHANGE",
            path=path,
        )
        self.assertEqual(future_observed.returncode, 2)
        self.assertIn("observed_at cannot follow", future_observed.stderr)
        self.assertEqual(self.state_path.read_bytes(), state_before_receipt)

    def test_record_manifest_binds_records_without_creating_second_state(self) -> None:
        source_id, _source, registered_source = self.register(
            "idea.idea_card", "IDEA-PORTFOLIO-001"
        )
        self.assertEqual(registered_source.returncode, 0, registered_source.stderr)
        manifest = self.record_manifest(
            stage="idea",
            source_role="idea_card",
            source_artifact_id=source_id,
            records=[{"record_id": "IDEA-001", "record_kind": "candidate"}],
        )
        manifest_path = self.project / "work/idea/record-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        _identifier, _path, registered_manifest = self.register(
            "idea.record_manifest", "IDEA-RECORDS-001", path=manifest_path
        )

        self.assertEqual(
            registered_manifest.returncode, 0, registered_manifest.stderr
        )
        state = self.load_state()
        self.assertNotIn("records", state)
        self.assertIn("record_manifest", state["artifacts"]["idea"])
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

    def test_record_manifest_rejects_invalid_kind_and_unregistered_source(self) -> None:
        invalid = {
            "schema_version": "1.0",
            "stage": "idea",
            "records": [
                {
                    "record_id": "IDEA-001",
                    "record_kind": "unsupported_kind",
                    "source": {
                        "artifact_role": "idea_card",
                        "artifact_id": "MISSING-PORTFOLIO",
                        "revision": 1,
                        "locator": "#idea-001",
                    },
                    "supersedes": None,
                    "relations": [],
                }
            ],
        }
        manifest_path = self.project / "work/idea/invalid-record-manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(invalid, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        before = self.state_path.read_bytes()

        _identifier, _path, rejected = self.register(
            "idea.record_manifest", "IDEA-RECORDS-INVALID", path=manifest_path
        )

        self.assertEqual(rejected.returncode, 2)
        self.assertIn("record manifest", rejected.stderr)
        self.assertIn("record_kind", rejected.stderr)
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertFalse(
            (self.project / ".research/snapshots/idea/record_manifest").exists()
        )

    def test_record_manifest_revisions_are_append_only(self) -> None:
        source_id, _source, registered_source = self.register(
            "idea.idea_card", "IDEA-PORTFOLIO-APPEND"
        )
        self.assertEqual(registered_source.returncode, 0, registered_source.stderr)
        manifest = self.record_manifest(
            stage="idea",
            source_role="idea_card",
            source_artifact_id=source_id,
            records=[{"record_id": "IDEA-001", "record_kind": "candidate"}],
        )
        manifest_path = self.project / "work/idea/append-only-records.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest_id, _path, first = self.register(
            "idea.record_manifest", "IDEA-RECORDS-APPEND", path=manifest_path
        )
        self.assertEqual(first.returncode, 0, first.stderr)

        manifest["records"][0]["source"]["locator"] = "#silently-rewritten"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _identifier, _path, rewritten = self.register(
            "idea.record_manifest", manifest_id, path=manifest_path
        )
        self.assertEqual(rewritten.returncode, 2)
        self.assertIn("append-only", rewritten.stderr)
        self.assertEqual(
            self.artifact_entry("idea.record_manifest", manifest_id)[
                "current_revision"
            ],
            1,
        )

        manifest["records"][0]["source"]["locator"] = "#idea-001"
        manifest["records"].append(
            {
                "record_id": "IDEA-002",
                "record_kind": "candidate",
                "source": manifest["records"][0]["source"],
                "supersedes": "IDEA-001",
                "relations": [
                    {"relation": "derived_from", "target_id": "IDEA-001"}
                ],
            }
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _identifier, _path, appended = self.register(
            "idea.record_manifest", manifest_id, path=manifest_path
        )
        self.assertEqual(appended.returncode, 0, appended.stderr)
        self.assertEqual(
            self.artifact_entry("idea.record_manifest", manifest_id)[
                "current_revision"
            ],
            2,
        )

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO creation is POSIX-only")
    def test_manifest_append_rejects_fifo_item_and_target_without_blocking(
        self,
    ) -> None:
        item_fifo = self.project / "record-item.fifo"
        os.mkfifo(item_fifo)
        item_target = self.project / "work/idea/fifo-item-manifest.json"
        item_result = subprocess.run(
            [
                sys.executable,
                str(RESEARCHCTL),
                "record",
                "append",
                "--stage",
                "idea",
                "--path",
                str(item_target),
                "--artifact-id",
                "FIFO-ITEM-MANIFEST",
                "--record",
                str(item_fifo),
            ],
            cwd=self.project,
            env=self.environment(),
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        self.assertEqual(item_result.returncode, 2, item_result.stderr)
        self.assertIn("regular file", item_result.stderr)

        target_fifo = self.project / "work/idea/record-target.fifo"
        target_fifo.parent.mkdir(parents=True, exist_ok=True)
        os.mkfifo(target_fifo)
        record = self.project / "record-item.json"
        record.write_text("{}\n", encoding="utf-8")
        target_result = subprocess.run(
            [
                sys.executable,
                str(RESEARCHCTL),
                "record",
                "append",
                "--stage",
                "idea",
                "--path",
                str(target_fifo),
                "--artifact-id",
                "FIFO-TARGET-MANIFEST",
                "--record",
                str(record),
            ],
            cwd=self.project,
            env=self.environment(),
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        self.assertEqual(target_result.returncode, 2, target_result.stderr)
        self.assertIn("regular file", target_result.stderr)

    def test_manifest_append_failure_never_removes_or_restores_a_replacement(
        self,
    ) -> None:
        source_id, _source, registered_source = self.register(
            "idea.idea_card", "IDEA-MANIFEST-CLEANUP-SOURCE"
        )
        self.assertEqual(registered_source.returncode, 0, registered_source.stderr)
        source_ref = self.artifact_ref("idea.idea_card", source_id)

        def write_record(path: Path, record_id: str) -> None:
            path.write_text(
                json.dumps(
                    {
                        "record_id": record_id,
                        "record_kind": "candidate",
                        "source": {
                            "artifact_role": "idea_card",
                            "artifact_id": source_id,
                            "revision": source_ref["revision"],
                            "locator": f"#{record_id.lower()}",
                        },
                        "supersedes": None,
                        "relations": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

        new_target = self.project / "work/idea/new-cleanup-manifest.json"
        new_item = self.project / "work/idea/new-cleanup-record.json"
        write_record(new_item, "IDEA-CLEANUP-NEW-001")
        new_arguments = Namespace(
            stage="idea",
            path=str(new_target),
            artifact_id="IDEA-CLEANUP-NEW",
            record=str(new_item),
            json=True,
            record_action="append",
        )
        state_before_new = self.state_path.read_bytes()

        def replace_new_then_fail(*_args: object, **_kwargs: object) -> int:
            new_target.unlink()
            new_target.write_text("unrelated new replacement\n", encoding="utf-8")
            raise ResearchCtlError("injected new-target registration failure")

        with (
            state_mutation_lock(self.project, create=False),
            mock.patch.object(
                manifest_commands_module,
                "cmd_artifact",
                side_effect=replace_new_then_fail,
            ),
            self.assertRaisesRegex(ResearchCtlError, "new-target"),
        ):
            cmd_record_append(self.project, load_policy(), new_arguments)

        self.assertEqual(
            new_target.read_text(encoding="utf-8"),
            "unrelated new replacement\n",
        )
        self.assertEqual(self.state_path.read_bytes(), state_before_new)

        existing_target = self.project / "work/idea/existing-cleanup-manifest.json"
        first_item = self.project / "work/idea/existing-cleanup-record-1.json"
        write_record(first_item, "IDEA-CLEANUP-EXISTING-001")
        first = self.run_ctl(
            "record",
            "append",
            "--stage",
            "idea",
            "--path",
            str(existing_target),
            "--artifact-id",
            "IDEA-CLEANUP-EXISTING",
            "--record",
            str(first_item),
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        second_item = self.project / "work/idea/existing-cleanup-record-2.json"
        write_record(second_item, "IDEA-CLEANUP-EXISTING-002")
        existing_arguments = Namespace(
            stage="idea",
            path=str(existing_target),
            artifact_id="IDEA-CLEANUP-EXISTING",
            record=str(second_item),
            json=True,
            record_action="append",
        )
        state_before_existing = self.state_path.read_bytes()

        def replace_existing_then_fail(*_args: object, **_kwargs: object) -> int:
            existing_target.unlink()
            existing_target.write_text(
                "unrelated existing replacement\n", encoding="utf-8"
            )
            raise ResearchCtlError("injected existing-target registration failure")

        with (
            state_mutation_lock(self.project, create=False),
            mock.patch.object(
                manifest_commands_module,
                "cmd_artifact",
                side_effect=replace_existing_then_fail,
            ),
            self.assertRaisesRegex(ResearchCtlError, "existing-target"),
        ):
            cmd_record_append(self.project, load_policy(), existing_arguments)

        self.assertEqual(
            existing_target.read_text(encoding="utf-8"),
            "unrelated existing replacement\n",
        )
        self.assertEqual(self.state_path.read_bytes(), state_before_existing)

    def test_record_append_atomically_registers_manifest_revisions(self) -> None:
        source_id, _source, registered_source = self.register(
            "idea.idea_card", "IDEA-PORTFOLIO-ATOMIC-APPEND"
        )
        self.assertEqual(registered_source.returncode, 0, registered_source.stderr)
        source_ref = self.artifact_ref("idea.idea_card", source_id)
        manifest_path = self.project / "work/idea/atomic-records.json"
        first_item = self.project / "work/idea/record-001.json"
        first_item.write_text(
            json.dumps(
                {
                    "record_id": "IDEA-ATOMIC-001",
                    "record_kind": "candidate",
                    "source": {
                        "artifact_role": "idea_card",
                        "artifact_id": source_id,
                        "revision": source_ref["revision"],
                        "locator": "#idea-atomic-001",
                    },
                    "supersedes": None,
                    "relations": [],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        first = self.run_ctl(
            "record",
            "append",
            "--stage",
            "idea",
            "--path",
            str(manifest_path),
            "--artifact-id",
            "IDEA-RECORDS-ATOMIC",
            "--record",
            str(first_item),
            "--json",
        )

        self.assertEqual(first.returncode, 0, first.stderr)
        first_result = json.loads(first.stdout)
        self.assertEqual(first_result["result"], "registered")
        self.assertEqual(first_result["artifact"]["revision"], 1)
        self.assertEqual(
            first_result["artifact_ref"]["label"],
            "artifacts.idea.record_manifest.IDEA-RECORDS-ATOMIC",
        )
        first_bytes = manifest_path.read_bytes()

        second_item = self.project / "work/idea/record-002.json"
        second_item.write_text(
            json.dumps(
                {
                    "record_id": "IDEA-ATOMIC-002",
                    "record_kind": "candidate",
                    "source": {
                        "artifact_role": "idea_card",
                        "artifact_id": source_id,
                        "revision": source_ref["revision"],
                        "locator": "#idea-atomic-002",
                    },
                    "supersedes": None,
                    "relations": [
                        {
                            "relation": "derived_from",
                            "target_id": "IDEA-ATOMIC-001",
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        second = self.run_ctl(
            "record",
            "append",
            "--stage",
            "idea",
            "--path",
            str(manifest_path),
            "--artifact-id",
            "IDEA-RECORDS-ATOMIC",
            "--record",
            str(second_item),
            "--json",
        )

        self.assertEqual(second.returncode, 0, second.stderr)
        second_result = json.loads(second.stdout)
        self.assertEqual(second_result["artifact"]["revision"], 2)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [record["record_id"] for record in manifest["records"]],
            ["IDEA-ATOMIC-001", "IDEA-ATOMIC-002"],
        )
        entry = self.artifact_entry("idea.record_manifest", "IDEA-RECORDS-ATOMIC")
        first_snapshot = self.project / entry["revisions"][0]["snapshot_path"]
        self.assertEqual(first_snapshot.read_bytes(), first_bytes)

    def test_manifest_append_preserves_committed_source_after_output_failure(self) -> None:
        source_id, _source, registered_source = self.register(
            "idea.idea_card", "IDEA-PORTFOLIO-POST-COMMIT"
        )
        self.assertEqual(registered_source.returncode, 0, registered_source.stderr)
        source_ref = self.artifact_ref("idea.idea_card", source_id)
        manifest_path = self.project / "work/idea/post-commit-records.json"
        item_path = self.project / "work/idea/post-commit-record.json"
        item_path.write_text(
            json.dumps(
                {
                    "record_id": "IDEA-POST-COMMIT-001",
                    "record_kind": "candidate",
                    "source": {
                        "artifact_role": "idea_card",
                        "artifact_id": source_id,
                        "revision": source_ref["revision"],
                        "locator": "#idea-post-commit-001",
                    },
                    "supersedes": None,
                    "relations": [],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        arguments = Namespace(
            stage="idea",
            path=str(manifest_path),
            artifact_id="IDEA-RECORDS-POST-COMMIT",
            record=str(item_path),
            json=True,
            record_action="append",
        )

        with (
            state_mutation_lock(self.project, create=False),
            mock.patch(
                "scripts.researchctl_core.commands._emit_artifact_result",
                side_effect=BrokenPipeError("consumer closed stdout"),
            ),
            self.assertRaises(BrokenPipeError),
        ):
            cmd_record_append(self.project, load_policy(), arguments)

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [record["record_id"] for record in manifest["records"]],
            ["IDEA-POST-COMMIT-001"],
        )
        entry = self.artifact_entry(
            "idea.record_manifest", "IDEA-RECORDS-POST-COMMIT"
        )
        self.assertEqual(entry["current_revision"], 1)
        snapshot = self.project / entry["revisions"][0]["snapshot_path"]
        self.assertEqual(snapshot.read_bytes(), manifest_path.read_bytes())

    def test_adapter_append_registers_request_and_accepted_receipt(self) -> None:
        self.assertEqual(self.approve_gate("idea_freeze").returncode, 0)
        self.assertEqual(
            self.approve_gate("method_experiment_approval").returncode, 0
        )
        payload_id, _payload, registered = self.register(
            "experiment_results.experiment_request",
            "EXPERIMENT-REQUEST-ATOMIC",
            content='{"command":["python3","train.py"]}\n',
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        request = self.adapter_request(
            request_id="REQUEST-EXPERIMENT-ATOMIC",
            operation_kind="experiment_execution",
            payload_ref=self.artifact_ref(
                "experiment_results.experiment_request", payload_id
            ),
            gate_binding=self.gate_binding("method_experiment_approval"),
            effect_class="costly_compute",
        )
        exchange_path = self.project / "work/experiment_results/atomic-exchange.json"
        request_path = self.project / "work/experiment_results/request.json"
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        appended_request = self.run_ctl(
            "adapter",
            "request-append",
            "--stage",
            "experiment_results",
            "--path",
            str(exchange_path),
            "--artifact-id",
            "EXPERIMENT-ADAPTER-ATOMIC",
            "--request",
            str(request_path),
            "--json",
        )
        self.assertEqual(appended_request.returncode, 0, appended_request.stderr)
        self.assertEqual(
            json.loads(appended_request.stdout)["artifact"]["revision"], 1
        )

        verified = self.run_ctl(
            "adapter",
            "verify",
            request["request_id"],
            "--attempt-id",
            "ATTEMPT-EXPERIMENT-ATOMIC",
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        request_hash = json.loads(verified.stdout)["request_hash"]
        receipt = self.adapter_receipt(
            receipt_id="RECEIPT-EXPERIMENT-ATOMIC-ACCEPTED",
            request=request,
            request_hash=request_hash,
            attempt_id="ATTEMPT-EXPERIMENT-ATOMIC",
            status="accepted",
            message="Attempt journal persisted before external execution.",
        )
        receipt_path = self.project / "work/experiment_results/receipt.json"
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        appended_receipt = self.run_ctl(
            "adapter",
            "receipt-append",
            "--stage",
            "experiment_results",
            "--path",
            str(exchange_path),
            "--artifact-id",
            "EXPERIMENT-ADAPTER-ATOMIC",
            "--receipt",
            str(receipt_path),
            "--json",
        )

        self.assertEqual(appended_receipt.returncode, 0, appended_receipt.stderr)
        self.assertEqual(
            json.loads(appended_receipt.stdout)["artifact"]["revision"], 2
        )
        exchange = json.loads(exchange_path.read_text(encoding="utf-8"))
        self.assertEqual(len(exchange["requests"]), 1)
        self.assertEqual(len(exchange["receipts"]), 1)
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

    def test_artifact_append_rejects_semantically_valid_tampered_history(self) -> None:
        source_id, _source, registered_source = self.register(
            "idea.idea_card", "IDEA-PORTFOLIO-TAMPERED-HISTORY"
        )
        self.assertEqual(registered_source.returncode, 0, registered_source.stderr)
        manifest = self.record_manifest(
            stage="idea",
            source_role="idea_card",
            source_artifact_id=source_id,
            records=[{"record_id": "IDEA-TAMPER-001", "record_kind": "candidate"}],
        )
        manifest_path = self.project / "work/idea/tampered-history.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest_id, _path, first = self.register(
            "idea.record_manifest", "IDEA-RECORDS-TAMPERED", path=manifest_path
        )
        self.assertEqual(first.returncode, 0, first.stderr)

        entry = self.artifact_entry("idea.record_manifest", manifest_id)
        historical_snapshot = self.project / entry["revisions"][0]["snapshot_path"]
        forged_prefix = json.loads(historical_snapshot.read_text(encoding="utf-8"))
        forged_prefix["records"][0]["source"]["locator"] = "#forged-but-valid"
        historical_snapshot.write_text(
            json.dumps(forged_prefix, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        forged_prefix["records"].append(
            {
                "record_id": "IDEA-TAMPER-002",
                "record_kind": "candidate",
                "source": dict(forged_prefix["records"][0]["source"]),
                "supersedes": "IDEA-TAMPER-001",
                "relations": [
                    {"relation": "derived_from", "target_id": "IDEA-TAMPER-001"}
                ],
            }
        )
        manifest_path.write_text(
            json.dumps(forged_prefix, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        before = self.state_path.read_bytes()

        _identifier, _path, rejected = self.register(
            "idea.record_manifest", manifest_id, path=manifest_path
        )

        self.assertEqual(rejected.returncode, 2)
        self.assertIn("snapshot mismatch", rejected.stderr)
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertEqual(
            self.artifact_entry("idea.record_manifest", manifest_id)[
                "current_revision"
            ],
            1,
        )

    def test_record_ids_are_project_unique_and_relations_use_typed_vocabulary(
        self,
    ) -> None:
        idea_source_id, _source, idea_source = self.register(
            "idea.idea_card", "IDEA-PORTFOLIO-UNIQUE"
        )
        self.assertEqual(idea_source.returncode, 0, idea_source.stderr)
        idea_manifest = self.record_manifest(
            stage="idea",
            source_role="idea_card",
            source_artifact_id=idea_source_id,
            records=[{"record_id": "IDEA-001", "record_kind": "candidate"}],
        )
        idea_path = self.project / "work/idea/unique-records.json"
        idea_path.write_text(
            json.dumps(idea_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _identifier, _path, idea_registered = self.register(
            "idea.record_manifest", "IDEA-RECORDS-UNIQUE", path=idea_path
        )
        self.assertEqual(idea_registered.returncode, 0, idea_registered.stderr)

        method_source_id, _source, method_source = self.register(
            "method.approval_package", "METHOD-PORTFOLIO-UNIQUE"
        )
        self.assertEqual(method_source.returncode, 0, method_source.stderr)
        method_manifest = self.record_manifest(
            stage="method",
            source_role="approval_package",
            source_artifact_id=method_source_id,
            records=[{"record_id": "IDEA-001", "record_kind": "candidate"}],
        )
        method_path = self.project / "work/method/unique-records.json"
        method_path.write_text(
            json.dumps(method_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _identifier, _path, duplicate = self.register(
            "method.record_manifest", "METHOD-RECORDS-UNIQUE", path=method_path
        )
        self.assertEqual(duplicate.returncode, 2)
        self.assertIn("duplicates project record", duplicate.stderr)

        method_manifest["records"][0]["record_id"] = "METHOD-001"
        method_manifest["records"][0]["relations"] = [
            {"relation": "guarantees", "target_id": "IDEA-001"}
        ]
        method_path.write_text(
            json.dumps(method_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _identifier, _path, unsupported_relation = self.register(
            "method.record_manifest", "METHOD-RECORDS-UNIQUE", path=method_path
        )
        self.assertEqual(unsupported_relation.returncode, 2)
        self.assertIn("relation 'guarantees' is unsupported", unsupported_relation.stderr)

    def test_record_relations_require_existing_type_compatible_targets(self) -> None:
        idea_source_id, _source, idea_source = self.register(
            "idea.idea_card", "IDEA-PORTFOLIO-RELATIONS"
        )
        self.assertEqual(idea_source.returncode, 0, idea_source.stderr)
        idea_manifest = self.record_manifest(
            stage="idea",
            source_role="idea_card",
            source_artifact_id=idea_source_id,
            records=[{"record_id": "IDEA-001", "record_kind": "candidate"}],
        )
        idea_path = self.project / "work/idea/relation-records.json"
        idea_path.write_text(
            json.dumps(idea_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _identifier, _path, idea_registered = self.register(
            "idea.record_manifest", "IDEA-RECORDS-RELATIONS", path=idea_path
        )
        self.assertEqual(idea_registered.returncode, 0, idea_registered.stderr)

        claim_source_id, _source, claim_source = self.register(
            "experiment_results.claim_ledger", "CLAIM-LEDGER-RELATIONS"
        )
        self.assertEqual(claim_source.returncode, 0, claim_source.stderr)
        claim_manifest = self.record_manifest(
            stage="experiment_results",
            source_role="claim_ledger",
            source_artifact_id=claim_source_id,
            records=[
                {
                    "record_id": "CLAIM-001",
                    "record_kind": "claim",
                    "relations": [
                        {"relation": "derived_from", "target_id": "IDEA-001"}
                    ],
                }
            ],
        )
        claim_path = self.project / "work/experiment-results/relation-records.json"
        claim_path.parent.mkdir(parents=True, exist_ok=True)
        claim_path.write_text(
            json.dumps(claim_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _identifier, _path, claim_registered = self.register(
            "experiment_results.record_manifest",
            "CLAIM-RECORDS-RELATIONS",
            path=claim_path,
        )
        self.assertEqual(claim_registered.returncode, 0, claim_registered.stderr)

        paper_source_id, _source, paper_source = self.register(
            "paper.manuscript", "PAPER-MANUSCRIPT-RELATIONS"
        )
        self.assertEqual(paper_source.returncode, 0, paper_source.stderr)
        paper_manifest = self.record_manifest(
            stage="paper",
            source_role="manuscript",
            source_artifact_id=paper_source_id,
            records=[
                {
                    "record_id": "PAPER-LOCATION-001",
                    "record_kind": "paper_location",
                    "relations": [
                        {"relation": "attempt_of", "target_id": "CLAIM-001"}
                    ],
                }
            ],
        )
        paper_path = self.project / "work/paper/relation-records.json"
        paper_path.parent.mkdir(parents=True, exist_ok=True)
        paper_path.write_text(
            json.dumps(paper_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        _identifier, _path, incompatible = self.register(
            "paper.record_manifest", "PAPER-RECORDS-RELATIONS", path=paper_path
        )
        self.assertEqual(incompatible.returncode, 2)
        self.assertIn("does not allow paper_location -> claim", incompatible.stderr)

        paper_manifest["records"][0]["relations"] = [
            {"relation": "expresses", "target_id": "MISSING-CLAIM"}
        ]
        paper_path.write_text(
            json.dumps(paper_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _identifier, _path, dangling = self.register(
            "paper.record_manifest", "PAPER-RECORDS-RELATIONS", path=paper_path
        )
        self.assertEqual(dangling.returncode, 2)
        self.assertIn("references unknown record 'MISSING-CLAIM'", dangling.stderr)

        paper_manifest["records"][0]["relations"] = [
            {"relation": "expresses", "target_id": "CLAIM-001"}
        ]
        paper_path.write_text(
            json.dumps(paper_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _identifier, _path, valid = self.register(
            "paper.record_manifest", "PAPER-RECORDS-RELATIONS", path=paper_path
        )
        self.assertEqual(valid.returncode, 0, valid.stderr)

        paper_manifest["records"][0]["relations"] = [
            {"relation": "expresses", "target_id": "MISSING-CLAIM"}
        ]
        forged = (
            json.dumps(paper_manifest, ensure_ascii=False, indent=2) + "\n"
        ).encode()
        state = self.load_state()
        revision = state["artifacts"]["paper"]["record_manifest"][
            "PAPER-RECORDS-RELATIONS"
        ]["revisions"][0]
        paper_path.write_bytes(forged)
        (self.project / revision["snapshot_path"]).write_bytes(forged)
        revision["content_hash"] = "sha256:" + hashlib.sha256(forged).hexdigest()
        revision["size_bytes"] = len(forged)
        self.write_state(state)

        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 1)
        self.assertIn("references unknown record 'MISSING-CLAIM'", doctor.stdout)

    def test_doctor_semantically_revalidates_record_manifest_snapshots(self) -> None:
        source_id, _source, registered_source = self.register(
            "idea.idea_card", "IDEA-PORTFOLIO-DOCTOR"
        )
        self.assertEqual(registered_source.returncode, 0, registered_source.stderr)
        manifest = self.record_manifest(
            stage="idea",
            source_role="idea_card",
            source_artifact_id=source_id,
            records=[{"record_id": "IDEA-001", "record_kind": "candidate"}],
        )
        manifest_path = self.project / "work/idea/doctor-records.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest_id, _path, registered_manifest = self.register(
            "idea.record_manifest", "IDEA-RECORDS-DOCTOR", path=manifest_path
        )
        self.assertEqual(
            registered_manifest.returncode, 0, registered_manifest.stderr
        )

        manifest["records"][0]["record_kind"] = "forged_kind"
        forged = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
        state = self.load_state()
        revision = state["artifacts"]["idea"]["record_manifest"][manifest_id][
            "revisions"
        ][0]
        manifest_path.write_bytes(forged)
        snapshot = self.project / revision["snapshot_path"]
        snapshot.write_bytes(forged)
        revision["content_hash"] = "sha256:" + hashlib.sha256(forged).hexdigest()
        revision["size_bytes"] = len(forged)
        self.write_state(state)

        doctor = self.run_ctl("doctor")

        self.assertEqual(doctor.returncode, 1)
        self.assertIn("record_kind", doctor.stdout)
        self.assertIn("forged_kind", doctor.stdout)

    def test_terminate_records_a_structured_project_decision(self) -> None:
        artifact_id, _source, registered = self.register(
            "idea.idea_card", "TERMINATION-EVIDENCE"
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)

        terminated = self.run_ctl(
            "lifecycle",
            "terminate",
            "--reason",
            "Closest work removes the intended contribution.",
            "--supporting-evidence-id",
            "EVID-CLOSEST-WORK-001",
            "--opposing-evidence-id",
            "EVID-DIFFERENCE-001",
            "--unresolved-risk",
            "The closest-work interpretation may change.",
            "--decision-condition",
            "Reopen only if new evidence restores the same core contribution.",
        )

        self.assertEqual(terminated.returncode, 0, terminated.stderr)
        state = self.load_state()
        self.assertEqual(state["current_stage"], "idea")
        lifecycle = state["lifecycle"]
        self.assertEqual(lifecycle["status"], "terminated")
        self.assertEqual(len(lifecycle["history"]), 1)
        decision = lifecycle["history"][0]
        self.assertEqual(lifecycle["latest_decision_id"], decision["decision_id"])
        self.assertEqual(decision["action"], "terminate")
        self.assertEqual(decision["previous_status"], "active")
        self.assertEqual(decision["new_status"], "terminated")
        self.assertEqual(decision["stage"], "idea")
        self.assertEqual(
            decision["supporting_evidence_ids"], ["EVID-CLOSEST-WORK-001"]
        )
        self.assertEqual(
            decision["opposing_evidence_ids"], ["EVID-DIFFERENCE-001"]
        )
        self.assertEqual(
            decision["unresolved_risks"],
            ["The closest-work interpretation may change."],
        )
        self.assertEqual(
            decision["decision_conditions"],
            ["Reopen only if new evidence restores the same core contribution."],
        )
        self.assertEqual(
            [reference["artifact_id"] for reference in decision["artifact_refs"]],
            [artifact_id],
        )
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

    def test_fresh_workspace_can_terminate_without_registered_artifacts(self) -> None:
        self.assertEqual(self.load_state()["artifacts"], {})

        terminated = self.lifecycle("terminate")

        self.assertEqual(terminated.returncode, 0, terminated.stderr)
        state = self.load_state()
        self.assertEqual(state["current_stage"], "idea")
        self.assertEqual(state["lifecycle"]["status"], "terminated")
        decision = state["lifecycle"]["history"][-1]
        self.assertEqual(decision["action"], "terminate")
        self.assertEqual(decision["artifact_refs"], [])
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

    def test_doctor_rejects_malformed_lifecycle_decisions(self) -> None:
        _artifact_id, _source, registered = self.register(
            "idea.idea_card", "LIFECYCLE-AUDIT"
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        terminated = self.run_ctl(
            "lifecycle",
            "terminate",
            "--reason",
            "The mainline is no longer viable.",
            "--supporting-evidence-id",
            "EVID-STOP-001",
            "--decision-condition",
            "Reopen only for new evidence on the same mainline.",
        )
        self.assertEqual(terminated.returncode, 0, terminated.stderr)
        valid = self.load_state()

        malformed = json.loads(json.dumps(valid))
        malformed["lifecycle"]["history"][0]["supporting_evidence_ids"] = []
        self.write_state(malformed)
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 1)
        self.assertIn("supporting_evidence_ids must not be empty", doctor.stdout)

        malformed = json.loads(json.dumps(valid))
        malformed["lifecycle"]["history"][0]["artifact_refs"][0][
            "content_hash"
        ] = "sha256:" + "0" * 64
        self.write_state(malformed)
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 1)
        self.assertIn("retained artifact registry revision", doctor.stdout)

        malformed = json.loads(json.dumps(valid))
        malformed["lifecycle"]["status"] = "completed"
        malformed["lifecycle"]["history"][0]["action"] = "complete"
        malformed["lifecycle"]["history"][0]["new_status"] = "completed"
        self.write_state(malformed)
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 1)
        self.assertIn("complete requires prior approved Gate release/initial_submission", doctor.stdout)

    def test_gate_requires_and_persists_the_shared_decision_contract(self) -> None:
        self.register_gate_requirements("idea_freeze")
        before = self.state_path.read_bytes()
        missing = self.run_ctl(
            "gate",
            "approve",
            "idea_freeze",
            "--selected-id",
            "IDEA-001",
            "--reason",
            "Incomplete structured decision.",
        )
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(self.state_path.read_bytes(), before)

        approved = self.run_ctl(
            "gate",
            "approve",
            "idea_freeze",
            "--selected-id",
            "IDEA-001",
            "--reason",
            "The evidence supports committing to this candidate.",
            "--supporting-evidence-id",
            "EVID-SUPPORT-001",
            "--opposing-evidence-id",
            "EVID-OPPOSE-001",
            "--unresolved-risk",
            "Feasibility is not yet experimentally verified.",
            "--decision-condition",
            "Reopen if closer work removes the contribution boundary.",
        )
        self.assertEqual(approved.returncode, 0, approved.stderr)
        decision = self.load_state()["gates"]["idea_freeze"]["history"][-1]
        self.assertEqual(decision["supporting_evidence_ids"], ["EVID-SUPPORT-001"])
        self.assertEqual(decision["opposing_evidence_ids"], ["EVID-OPPOSE-001"])
        self.assertEqual(
            decision["unresolved_risks"],
            ["Feasibility is not yet experimentally verified."],
        )
        self.assertEqual(
            decision["decision_conditions"],
            ["Reopen if closer work removes the contribution boundary."],
        )
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

        state = self.load_state()
        state["gates"]["idea_freeze"]["history"][-1]["decision_conditions"] = []
        self.write_state(state)
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 1)
        self.assertIn("decision_conditions must not be empty", doctor.stdout)

    def test_complete_requires_release_and_reopen_rolls_back_the_affected_gate(self) -> None:
        self.register("idea.idea_card", "COMPLETE-EVIDENCE")
        before = self.state_path.read_bytes()
        premature = self.lifecycle("complete")
        self.assertEqual(premature.returncode, 2)
        self.assertIn("release/initial_submission", premature.stderr)
        self.assertEqual(self.state_path.read_bytes(), before)

        self.advance_through_claim_freeze()
        self.register_gate_requirements(
            "release", release_target="initial_submission"
        )
        released = self.gate(
            "approve", "release", release_target="initial_submission"
        )
        self.assertEqual(released.returncode, 0, released.stderr)
        completed = self.lifecycle("complete")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.load_state()["lifecycle"]["status"], "completed")

        missing_gate = self.lifecycle("reopen")
        self.assertEqual(missing_gate.returncode, 2)
        self.assertIn("completed lifecycle reopen requires --gate", missing_gate.stderr)

        self.sources["experiment_results.claim_ledger"].write_text(
            "Claim boundary changed after completion.\n", encoding="utf-8"
        )
        reopened = self.lifecycle("reopen", gate="claim_freeze")
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        state = self.load_state()
        self.assertEqual(state["lifecycle"]["status"], "active")
        self.assertEqual(state["current_stage"], "experiment_results")
        self.assertEqual(state["gates"]["claim_freeze"]["status"], "reopened")
        self.assertEqual(
            state["gates"]["release"]["targets"]["initial_submission"]["status"],
            "reopened",
        )
        lifecycle_decision = state["lifecycle"]["history"][-1]
        gate_decision = state["gates"]["claim_freeze"]["history"][-1]
        self.assertEqual(
            lifecycle_decision["gate_ref"], {"gate": "claim_freeze"}
        )
        self.assertEqual(
            lifecycle_decision["gate_decision_id"], gate_decision["decision_id"]
        )
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

    def test_terminal_lifecycle_blocks_research_writes_but_can_reopen(self) -> None:
        artifact_id, source, registered = self.register(
            "idea.idea_card", "TERMINAL-GUARD"
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        terminated = self.lifecycle("terminate")
        self.assertEqual(terminated.returncode, 0, terminated.stderr)
        terminal_state = self.state_path.read_bytes()

        source.write_text("attempted terminal mutation\n", encoding="utf-8")
        blocked_commands = (
            (
                "artifact",
                "register",
                "idea_card",
                "--stage",
                "idea",
                "--path",
                str(source),
                "--artifact-id",
                artifact_id,
            ),
            (
                "gate",
                "approve",
                "idea_freeze",
                "--reason",
                "Blocked terminal Gate.",
                "--supporting-evidence-id",
                "EVID-BLOCKED",
                "--decision-condition",
                "Remain stopped.",
                "--selected-id",
                "IDEA-001",
            ),
            ("checkpoint", "--summary", "Blocked terminal checkpoint."),
        )
        for command in blocked_commands:
            blocked = self.run_ctl(*command)
            self.assertEqual(blocked.returncode, 2, command)
            self.assertIn("reopen it before research mutations", blocked.stderr)
            self.assertEqual(self.state_path.read_bytes(), terminal_state)

        for command in (("status",), ("doctor",), ("dashboard",)):
            allowed = self.run_ctl(*command)
            self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)

        reopened = self.lifecycle("reopen")
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        state = self.load_state()
        self.assertEqual(state["lifecycle"]["status"], "active")
        self.assertEqual(state["current_stage"], "idea")
        self.assertEqual(state["gates"]["idea_freeze"]["status"], "pending")

    def test_disable_requires_reason_and_preserves_the_lifecycle(self) -> None:
        self.register("idea.idea_card", "DISABLE-AUDIT")
        terminated = self.lifecycle("terminate")
        self.assertEqual(terminated.returncode, 0, terminated.stderr)
        before = self.load_state()
        before_bytes = self.state_path.read_bytes()

        missing_reason = self.run_ctl("disable")
        self.assertEqual(missing_reason.returncode, 2)
        self.assertEqual(self.state_path.read_bytes(), before_bytes)

        disabled = self.run_ctl(
            "disable", "--reason", "Temporarily operating outside the Plugin."
        )
        self.assertEqual(disabled.returncode, 0, disabled.stderr)
        state = self.load_state()
        self.assertFalse(state["enabled"])
        self.assertEqual(state["lifecycle"], before["lifecycle"])
        self.assertEqual(state["current_stage"], before["current_stage"])
        self.assertEqual(state["gates"], before["gates"])
        self.assertEqual(
            state["activation_history"][0]["reason"],
            "Temporarily operating outside the Plugin.",
        )

        enabled = self.run_ctl(
            "enable", "--reason", "Resume Plugin supervision on the same state."
        )
        self.assertEqual(enabled.returncode, 0, enabled.stderr)
        state = self.load_state()
        self.assertTrue(state["enabled"])
        self.assertEqual(
            [event["action"] for event in state["activation_history"]],
            ["disable", "enable"],
        )
        self.assertEqual(state["lifecycle"], before["lifecycle"])
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

        state["activation_history"][-1]["reason"] = ""
        self.write_state(state)
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 1)
        self.assertIn("activation_history[1] reason must be non-empty", doctor.stdout)

    def test_v1_state_is_rejected_without_mutation_or_automatic_migration(self) -> None:
        state = self.load_state()
        state["schema_version"] = "1.0"
        state["workflow_version"] = "1.1.0"
        self.write_state(state)
        lock = self.project / ".research/state.lock"
        lock.unlink(missing_ok=True)

        def tree_snapshot() -> tuple[list[str], dict[str, bytes]]:
            research = self.project / ".research"
            entries = sorted(str(path.relative_to(research)) for path in research.rglob("*"))
            files = {
                str(path.relative_to(research)): path.read_bytes()
                for path in research.rglob("*")
                if path.is_file()
            }
            return entries, files

        before = tree_snapshot()

        result = self.run_ctl("init")

        self.assertEqual(result.returncode, 2)
        self.assertIn("unsupported state schema_version", result.stderr)
        self.assertIn("no automatic migration", result.stderr)
        self.assertEqual(tree_snapshot(), before)
        self.assertFalse(lock.exists())

    def test_register_uses_stable_source_and_auto_increments_full_revisions(self) -> None:
        artifact_id, source, first = self.register(
            "idea.idea_card", "IDEA-PORTFOLIO", content="# portfolio r1\n"
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        entry = self.artifact_entry("idea.idea_card", artifact_id)
        self.assertEqual(entry["current_revision"], 1)
        self.assertEqual([item["revision"] for item in entry["revisions"]], [1])
        first_snapshot = self.project / entry["revisions"][0]["snapshot_path"]
        self.assertEqual(first_snapshot.read_text(encoding="utf-8"), "# portfolio r1\n")
        state_before_idempotent = self.state_path.read_bytes()

        _artifact_id, _source, repeated = self.register(
            "idea.idea_card", artifact_id, path=source
        )
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertIn("already registered", repeated.stdout)
        self.assertEqual(self.state_path.read_bytes(), state_before_idempotent)

        for number in (2, 3):
            source.write_text(f"# portfolio r{number}\n", encoding="utf-8")
            _artifact_id, _source, result = self.register(
                "idea.idea_card", artifact_id, path=source
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        entry = self.artifact_entry("idea.idea_card", artifact_id)
        self.assertEqual(entry["current_revision"], 3)
        self.assertEqual([item["revision"] for item in entry["revisions"]], [1, 2, 3])
        self.assertEqual(len({item["snapshot_path"] for item in entry["revisions"]}), 3)
        self.assertEqual(first_snapshot.read_text(encoding="utf-8"), "# portfolio r1\n")
        self.assertTrue(all(item["source_path"] == entry["revisions"][0]["source_path"] for item in entry["revisions"]))

    def test_two_concurrent_identical_registrations_serialize_to_one_next_revision(self) -> None:
        artifact_id, source, first = self.register("idea.idea_card", "CONCURRENT")
        self.assertEqual(first.returncode, 0, first.stderr)
        source.write_text("second stable revision\n", encoding="utf-8")
        command = [
            sys.executable,
            str(RESEARCHCTL),
            "artifact",
            "register",
            "idea_card",
            "--stage",
            "idea",
            "--path",
            str(source),
            "--artifact-id",
            artifact_id,
        ]
        processes = [
            subprocess.Popen(
                command,
                cwd=self.project,
                env=self.environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        results = [process.communicate(timeout=30) + (process.returncode,) for process in processes]
        self.assertTrue(all(code == 0 for _stdout, _stderr, code in results), results)
        entry = self.artifact_entry("idea.idea_card", artifact_id)
        self.assertEqual(entry["current_revision"], 2)
        self.assertEqual([item["revision"] for item in entry["revisions"]], [1, 2])
        combined = "\n".join(stdout for stdout, _stderr, _code in results)
        self.assertIn("registered artifact", combined)
        self.assertIn("already registered", combined)

    def test_two_concurrent_distinct_sources_append_contiguous_revisions(self) -> None:
        artifact_id, _source, first = self.register("idea.idea_card", "CONCURRENT-DISTINCT")
        self.assertEqual(first.returncode, 0, first.stderr)
        sources = [self.project / "work/source-a.md", self.project / "work/source-b.md"]
        contents = [b"concurrent source A\n", b"concurrent source B\n"]
        for source, content in zip(sources, contents):
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(content)
        commands = [
            [
                sys.executable,
                str(RESEARCHCTL),
                "artifact",
                "register",
                "idea_card",
                "--stage",
                "idea",
                "--path",
                str(source),
                "--artifact-id",
                artifact_id,
            ]
            for source in sources
        ]
        processes = [
            subprocess.Popen(
                command,
                cwd=self.project,
                env=self.environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for command in commands
        ]
        results = [process.communicate(timeout=30) + (process.returncode,) for process in processes]
        self.assertTrue(all(code == 0 for _stdout, _stderr, code in results), results)

        entry = self.artifact_entry("idea.idea_card", artifact_id)
        revisions = entry["revisions"]
        self.assertEqual(entry["current_revision"], 3)
        self.assertEqual([revision["revision"] for revision in revisions], [1, 2, 3])
        appended = revisions[1:]
        self.assertEqual(
            {str((self.project / revision["source_path"]).resolve()) for revision in appended},
            {str(source.resolve()) for source in sources},
        )
        self.assertEqual(len({revision["content_hash"] for revision in appended}), 2)
        self.assertEqual(len({revision["snapshot_path"] for revision in revisions}), 3)
        expected_bytes = {str(source.resolve()): content for source, content in zip(sources, contents)}
        for revision in appended:
            source_path = str((self.project / revision["source_path"]).resolve())
            self.assertEqual(
                (self.project / revision["snapshot_path"]).read_bytes(),
                expected_bytes[source_path],
            )
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

    def test_large_outputs_require_a_small_manifest(self) -> None:
        large = self.project / "work/large.bin"
        large.parent.mkdir(parents=True, exist_ok=True)
        with large.open("wb") as stream:
            stream.seek(64 * 1024 * 1024)
            stream.write(b"x")
        rejected = self.run_ctl(
            "artifact",
            "register",
            "artifact_manifest",
            "--stage",
            "experiment_results",
            "--path",
            str(large),
            "--artifact-id",
            "LARGE-OUTPUT",
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("snapshot limit", rejected.stderr)
        self.assertIn("Register a small manifest", rejected.stderr)

        manifest = self.project / "work/manifest.json"
        manifest.write_text(
            json.dumps({"files": [{"id": "LARGE-OUTPUT", "path": str(large), "sha256": "external-checksum"}]}) + "\n",
            encoding="utf-8",
        )
        _artifact_id, _source, accepted = self.register(
            "experiment_results.artifact_manifest", "RUN-MANIFEST", path=manifest
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)

    def test_legacy_manual_version_and_status_flags_are_removed(self) -> None:
        source = self.project / "idea.md"
        source.write_text("idea\n", encoding="utf-8")
        result = self.run_ctl(
            "artifact",
            "register",
            "idea_card",
            "--stage",
            "idea",
            "--path",
            str(source),
            "--artifact-id",
            "IDEA",
            "--version",
            "2",
            "--status",
            "approval-ready",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments", result.stderr)

    def test_selection_is_required_and_records_an_opaque_candidate_id(self) -> None:
        self.register_gate_requirements("idea_freeze")
        missing = self.gate("approve", "idea_freeze")
        self.assertEqual(missing.returncode, 2)
        self.assertIn("requires --selected-id", missing.stderr)

        selected = self.gate("approve", "idea_freeze", selected_id="IDEA-NOT-PARSED")
        self.assertEqual(selected.returncode, 0, selected.stderr)
        decision = self.load_state()["gates"]["idea_freeze"]["history"][-1]
        self.assertEqual(decision["selection"]["selected_id"], "IDEA-NOT-PARSED")
        self.assertEqual(
            decision["selection"]["artifact_ref"],
            next(
                ref
                for ref in decision["artifact_refs"]
                if ref["label"].startswith("artifacts.idea.idea_card.")
            ),
        )
        self.assertNotEqual(
            decision["selection"]["selected_id"],
            decision["selection"]["artifact_ref"]["artifact_id"],
        )

        unsupported = self.gate(
            "approve", "claim_freeze", selected_id="CLAIM-CANDIDATE"
        )
        self.assertEqual(unsupported.returncode, 2)
        self.assertIn("valid only when approving a Gate with selection_artifact_role", unsupported.stderr)

    def test_selection_portfolio_role_has_exactly_one_artifact(self) -> None:
        _identifier, _source, first = self.register(
            "idea.idea_card", "PORTFOLIO-A", content="# PORTFOLIO-A\n"
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        before = self.state_path.read_bytes()

        _identifier, _source, second = self.register(
            "idea.idea_card", "PORTFOLIO-B", content="# PORTFOLIO-B\n"
        )

        self.assertEqual(second.returncode, 2)
        self.assertIn("already has its one canonical artifact", second.stderr)
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_release_target_is_explicit_exact_and_rejected_elsewhere(self) -> None:
        before = self.state_path.read_bytes()
        missing = self.gate("approve", "release")
        self.assertEqual(missing.returncode, 2)
        self.assertIn("requires --target", missing.stderr)

        unknown = self.gate(
            "approve", "release", release_target="camera_ready"
        )
        self.assertEqual(unknown.returncode, 2)
        self.assertIn("initial_submission, revision_rebuttal", unknown.stderr)

        untargeted = self.gate(
            "approve", "idea_freeze", selected_id="IDEA-003",
            release_target="initial_submission",
        )
        self.assertEqual(untargeted.returncode, 2)
        self.assertIn("valid only for a targeted Gate", untargeted.stderr)
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_claim_freeze_records_normal_mode_and_rejects_mode_misuse(self) -> None:
        retrospective_reopen = self.gate(
            "reopen", "claim_freeze", retrospective=True
        )
        self.assertEqual(retrospective_reopen.returncode, 2)
        self.assertIn("valid only with `gate approve claim_freeze`", retrospective_reopen.stderr)

        retrospective_wrong_gate = self.gate(
            "approve", "idea_freeze", selected_id="IDEA-003", retrospective=True
        )
        self.assertEqual(retrospective_wrong_gate.returncode, 2)
        self.assertIn("valid only with `gate approve claim_freeze`", retrospective_wrong_gate.stderr)

        for gate in ("idea_freeze", "method_experiment_approval"):
            approved = self.approve_gate(gate)
            self.assertEqual(approved.returncode, 0, approved.stderr)
        self.register_gate_requirements("claim_freeze")
        normal = self.gate("approve", "claim_freeze")
        self.assertEqual(normal.returncode, 0, normal.stderr)
        decision = self.load_state()["gates"]["claim_freeze"]["history"][-1]
        self.assertEqual(decision["approval_mode"], "normal")
        self.assertNotIn("waived_artifact_roles", decision)

        state = self.load_state()
        del state["gates"]["claim_freeze"]["history"][-1]["approval_mode"]
        self.write_state(state)
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 1)
        self.assertIn("must name a configured approval_mode", doctor.stdout)

    def test_retrospective_mode_refuses_to_waive_nothing(self) -> None:
        for gate in ("idea_freeze", "method_experiment_approval"):
            approved = self.approve_gate(gate)
            self.assertEqual(approved.returncode, 0, approved.stderr)
        self.register_gate_requirements("claim_freeze")
        self.register_gate_requirements(
            "claim_freeze", approval_mode="retrospective_revision_import"
        )
        before = self.state_path.read_bytes()

        result = self.gate("approve", "claim_freeze", retrospective=True)

        self.assertEqual(result.returncode, 2)
        self.assertIn("has no unavailable historical roles", result.stderr)
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_dirty_or_missing_current_source_rejects_approval(self) -> None:
        self.register_gate_requirements("idea_freeze")
        idea = self.sources["idea.idea_card"]
        evidence = self.sources["literature.evidence_base"]
        original_idea = idea.read_bytes()
        original_evidence = evidence.read_bytes()

        idea.write_text("dirty after registration\n", encoding="utf-8")
        dirty = self.gate("approve", "idea_freeze", selected_id="IDEA-003")
        self.assertEqual(dirty.returncode, 2)
        self.assertIn("source mismatch", dirty.stderr)
        idea.write_bytes(original_idea)

        evidence.unlink()
        missing = self.gate("approve", "idea_freeze", selected_id="IDEA-003")
        self.assertEqual(missing.returncode, 2)
        self.assertIn("cannot be resolved", missing.stderr)
        evidence.write_bytes(original_evidence)

        approved = self.gate("approve", "idea_freeze", selected_id="IDEA-003")
        self.assertEqual(approved.returncode, 0, approved.stderr)

    def test_approved_gate_allows_lossless_return_across_its_unchanged_binding(self) -> None:
        approved = self.approve_gate("idea_freeze")
        self.assertEqual(approved.returncode, 0, approved.stderr)
        approved_gate = self.load_state()["gates"]["idea_freeze"]
        self.assertEqual(self.load_state()["current_stage"], "method")

        retreated = self.run_ctl(
            "checkpoint",
            "--stage",
            "literature",
            "--summary",
            "Revisit the approved literature boundary.",
        )
        self.assertEqual(retreated.returncode, 0, retreated.stderr)

        returned = self.run_ctl(
            "checkpoint",
            "--stage",
            "method",
            "--summary",
            "Resume the unchanged approved method boundary.",
        )

        self.assertEqual(returned.returncode, 0, returned.stderr)
        state = self.load_state()
        self.assertEqual(state["current_stage"], "method")
        self.assertEqual(state["gates"]["idea_freeze"], approved_gate)
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

    def test_approved_gate_does_not_allow_return_after_bound_evidence_drift(self) -> None:
        approved = self.approve_gate("idea_freeze")
        self.assertEqual(approved.returncode, 0, approved.stderr)
        retreated = self.run_ctl(
            "checkpoint",
            "--stage",
            "literature",
            "--summary",
            "Revisit the approved literature boundary.",
        )
        self.assertEqual(retreated.returncode, 0, retreated.stderr)
        before = self.state_path.read_bytes()
        self.sources["literature.evidence_base"].write_text(
            "Bound evidence changed after approval.\n",
            encoding="utf-8",
        )

        returned = self.run_ctl(
            "checkpoint",
            "--stage",
            "method",
            "--summary",
            "Do not bypass the stale approved binding.",
        )

        self.assertEqual(returned.returncode, 2)
        self.assertIn("source mismatch", returned.stderr)
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_approval_reopen_revision_reapproval_preserves_every_snapshot(self) -> None:
        approved = self.approve_gate("idea_freeze", selected_id="IDEA-001")
        self.assertEqual(approved.returncode, 0, approved.stderr)
        entry_before = self.artifact_entry("idea.idea_card", "IDEA-IDEA-CARD")
        snapshot_r1 = self.project / entry_before["revisions"][0]["snapshot_path"]
        r1_bytes = snapshot_r1.read_bytes()

        blocked_source = self.sources["idea.idea_card"]
        blocked_source.write_text("# changed portfolio\n", encoding="utf-8")
        _id, _source, blocked = self.register(
            "idea.idea_card", "IDEA-IDEA-CARD", path=blocked_source
        )
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("reopen", blocked.stderr)

        reopened = self.gate("reopen", "idea_freeze")
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        _id, _source, revision = self.register(
            "idea.idea_card", "IDEA-IDEA-CARD", path=blocked_source
        )
        self.assertEqual(revision.returncode, 0, revision.stderr)
        reapproved = self.gate("approve", "idea_freeze", selected_id="IDEA-002")
        self.assertEqual(reapproved.returncode, 0, reapproved.stderr)

        state = self.load_state()
        entry = state["artifacts"]["idea"]["idea_card"]["IDEA-IDEA-CARD"]
        self.assertEqual(entry["current_revision"], 2)
        self.assertEqual([item["revision"] for item in entry["revisions"]], [1, 2])
        self.assertEqual(snapshot_r1.read_bytes(), r1_bytes)
        history = state["gates"]["idea_freeze"]["history"]
        self.assertEqual([item["action"] for item in history], ["approve", "reopen", "approve"])
        self.assertEqual(history[0]["artifact_refs"][0]["revision"], 1)
        self.assertEqual(history[-1]["artifact_refs"][0]["revision"], 2)
        self.assertEqual(history[0]["selection"]["selected_id"], "IDEA-001")
        self.assertEqual(history[-1]["selection"]["selected_id"], "IDEA-002")

    def test_snapshot_tampering_and_current_source_drift_fail_doctor(self) -> None:
        artifact_id, source, registered = self.register("idea.idea_card", "AUDIT")
        self.assertEqual(registered.returncode, 0, registered.stderr)
        entry = self.artifact_entry("idea.idea_card", artifact_id)
        snapshot = self.project / entry["revisions"][0]["snapshot_path"]

        snapshot.write_text("tampered snapshot\n", encoding="utf-8")
        tampered = self.run_ctl("doctor")
        self.assertEqual(tampered.returncode, 1)
        self.assertIn("snapshot mismatch", tampered.stdout)

        snapshot.write_bytes(source.read_bytes())
        source.write_text("dirty source\n", encoding="utf-8")
        dirty = self.run_ctl("doctor")
        self.assertEqual(dirty.returncode, 0)
        self.assertIn("[WARNING]", dirty.stdout)
        self.assertIn("source mismatch", dirty.stdout)

        source.write_text("# AUDIT\n", encoding="utf-8")
        self.register_gate_requirements("idea_freeze")
        approved = self.gate("approve", "idea_freeze", selected_id="IDEA-003")
        self.assertEqual(approved.returncode, 0, approved.stderr)
        source.write_text("active Gate drift\n", encoding="utf-8")
        active_drift = self.run_ctl("doctor")
        self.assertEqual(active_drift.returncode, 1)
        self.assertIn("source mismatch", active_drift.stdout)

    def test_same_main_tex_can_accumulate_paper_and_revision_snapshots(self) -> None:
        self.advance_through_claim_freeze()
        main_tex = self.project / "main.tex"
        main_tex.write_text("initial manuscript\n", encoding="utf-8")
        self.register_gate_requirements(
            "release",
            release_target="initial_submission",
            path_overrides={"paper.manuscript": main_tex},
        )
        initial = self.gate(
            "approve", "release", release_target="initial_submission"
        )
        self.assertEqual(initial.returncode, 0, initial.stderr)

        main_tex.write_text("revised manuscript\n", encoding="utf-8")
        self.register_gate_requirements(
            "release",
            release_target="revision_rebuttal",
            path_overrides={"revision.revised_manuscript": main_tex},
        )
        revision = self.gate(
            "approve", "release", release_target="revision_rebuttal"
        )
        self.assertEqual(revision.returncode, 0, revision.stderr)

        state = self.load_state()
        paper_entry = next(iter(state["artifacts"]["paper"]["manuscript"].values()))
        revision_entry = next(iter(state["artifacts"]["revision"]["revised_manuscript"].values()))
        self.assertEqual(paper_entry["revisions"][0]["source_path"], "main.tex")
        self.assertEqual(revision_entry["revisions"][0]["source_path"], "main.tex")
        self.assertNotEqual(
            paper_entry["revisions"][0]["snapshot_path"],
            revision_entry["revisions"][0]["snapshot_path"],
        )
        release_targets = state["gates"]["release"]["targets"]
        self.assertEqual(release_targets["initial_submission"]["status"], "approved")
        self.assertEqual(release_targets["revision_rebuttal"]["status"], "approved")
        self.assertEqual(len(release_targets["initial_submission"]["history"]), 1)
        self.assertEqual(len(release_targets["revision_rebuttal"]["history"]), 1)

    def test_release_targets_reopen_independently_then_cascade_by_exact_ref(self) -> None:
        self.advance_through_claim_freeze()
        for target in ("initial_submission", "revision_rebuttal"):
            approved = self.approve_gate("release", release_target=target)
            self.assertEqual(approved.returncode, 0, approved.stderr)

        direct = self.gate(
            "reopen", "release", release_target="revision_rebuttal"
        )
        self.assertEqual(direct.returncode, 0, direct.stderr)
        targets = self.load_state()["gates"]["release"]["targets"]
        self.assertEqual(targets["initial_submission"]["status"], "approved")
        self.assertEqual(targets["revision_rebuttal"]["status"], "reopened")
        self.assertNotIn("cascade", targets["revision_rebuttal"]["history"][-1])

        reapproved = self.gate(
            "approve", "release", release_target="revision_rebuttal"
        )
        self.assertEqual(reapproved.returncode, 0, reapproved.stderr)
        root = self.gate(
            "reopen", "release", release_target="initial_submission"
        )
        self.assertEqual(root.returncode, 0, root.stderr)

        state = self.load_state()
        targets = state["gates"]["release"]["targets"]
        self.assertEqual(targets["initial_submission"]["status"], "reopened")
        self.assertEqual(targets["revision_rebuttal"]["status"], "reopened")
        upstream = targets["initial_submission"]["history"][-1]
        cascade = targets["revision_rebuttal"]["history"][-1]
        self.assertNotIn("release_target", upstream)
        self.assertNotIn("release_target", cascade)
        self.assertEqual(
            cascade["cascade"]["upstream_gate_ref"],
            {"gate": "release", "target": "initial_submission"},
        )
        self.assertEqual(
            cascade["cascade"]["upstream_decision_id"],
            upstream["decision_id"],
        )
        self.assertEqual(
            cascade["cascade"]["upstream_reason"], upstream["reason"]
        )
        self.assertLess(cascade["decided_at"], upstream["decided_at"])
        self.assertEqual(state["current_stage"], "paper")

        cascade["cascade"]["upstream_gate_ref"]["target"] = "revision_rebuttal"
        self.write_state(state)
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 1)
        self.assertIn("upstream_gate_ref does not match", doctor.stdout)

    def test_upstream_reopen_cascades_audited_downstream_invalidation(self) -> None:
        self.advance_through_claim_freeze()
        result = self.gate("reopen", "idea_freeze")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.load_state()
        self.assertEqual(state["current_stage"], "idea")
        self.assertTrue(
            all(state["gates"][gate]["status"] == "reopened" for gate in (
                "idea_freeze", "method_experiment_approval", "claim_freeze"
            ))
        )
        self.assertEqual(
            [state["gates"][gate]["history"][-1]["action"] for gate in (
                "claim_freeze", "method_experiment_approval", "idea_freeze"
            )],
            ["reopen", "reopen", "reopen"],
        )
        self.assertTrue(
            all(state["gates"][gate]["history"][-1]["artifact_refs"] for gate in (
                "claim_freeze", "method_experiment_approval", "idea_freeze"
            ))
        )

    def test_release_after_upstream_reapproval_uses_revision_target(self) -> None:
        self.advance_through_claim_freeze()
        self.register_gate_requirements(
            "release", release_target="initial_submission"
        )
        initial = self.gate(
            "approve", "release", release_target="initial_submission"
        )
        self.assertEqual(initial.returncode, 0, initial.stderr)

        reopened = self.gate("reopen", "idea_freeze")
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        for gate, selected_id in (
            ("idea_freeze", "IDEA-003"),
            ("method_experiment_approval", "METHOD-002"),
            ("claim_freeze", None),
        ):
            approved = self.gate("approve", gate, selected_id=selected_id)
            self.assertEqual(approved.returncode, 0, approved.stderr)

        premature = self.gate(
            "approve", "release", release_target="revision_rebuttal"
        )
        self.assertEqual(premature.returncode, 2)
        self.assertIn("requires approved Gate release/initial_submission", premature.stderr)

        initial_again = self.gate(
            "approve", "release", release_target="initial_submission"
        )
        self.assertEqual(initial_again.returncode, 0, initial_again.stderr)
        self.register_gate_requirements("release", release_target="revision_rebuttal")
        revision = self.gate(
            "approve", "release", release_target="revision_rebuttal"
        )
        self.assertEqual(revision.returncode, 0, revision.stderr)

        state = self.load_state()
        release_targets = state["gates"]["release"]["targets"]
        initial_history = release_targets["initial_submission"]["history"]
        revision_history = release_targets["revision_rebuttal"]["history"]
        self.assertEqual(
            [decision["action"] for decision in initial_history],
            ["approve", "reopen", "approve"],
        )
        self.assertEqual([decision["action"] for decision in revision_history], ["approve"])
        self.assertIn("cascade", initial_history[1])
        self.assertEqual(
            initial_history[1]["cascade"]["upstream_gate_ref"],
            {"gate": "idea_freeze"},
        )
        doctor = self.run_ctl("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

    def test_forged_selection_or_gate_ref_is_rejected_by_doctor(self) -> None:
        approved = self.approve_gate("idea_freeze")
        self.assertEqual(approved.returncode, 0, approved.stderr)
        state = self.load_state()
        decision = state["gates"]["idea_freeze"]["history"][-1]
        decision["selection"]["artifact_ref"]["revision"] = 999
        self.write_state(state)

        result = self.run_ctl("doctor")

        self.assertEqual(result.returncode, 1)
        self.assertIn("selection.artifact_ref", result.stdout)

    def test_status_json_and_checkpoint_remain_small_navigation_surfaces(self) -> None:
        checkpoint = self.run_ctl(
            "checkpoint", "--summary", "Resume from the selected idea portfolio."
        )
        self.assertEqual(checkpoint.returncode, 0, checkpoint.stderr)
        status = self.run_ctl("status", "--json")
        self.assertEqual(status.returncode, 0, status.stderr)
        state = json.loads(status.stdout)
        self.assertEqual(
            state["last_checkpoint"]["summary"],
            "Resume from the selected idea portfolio.",
        )
        self.assertNotIn("overview", state)
        self.assertNotIn("round_id", state)

    def test_doctor_enforces_exact_checkpoint_and_transition_shapes(self) -> None:
        checkpoint = self.run_ctl(
            "checkpoint", "--summary", "Exact runtime shape."
        )
        self.assertEqual(checkpoint.returncode, 0, checkpoint.stderr)
        state = self.load_state()
        state["last_checkpoint"]["extra"] = True
        self.write_state(state)
        invalid_checkpoint = self.run_ctl("doctor")
        self.assertEqual(invalid_checkpoint.returncode, 1)
        self.assertIn("last_checkpoint fields must be", invalid_checkpoint.stdout)

        del state["last_checkpoint"]["extra"]
        state["stage_history"] = [
            {
                "from_stage": "idea",
                "to_stage": "literature",
                "trigger": "checkpoint",
                "timestamp": state["updated_at"],
                "extra": True,
            }
        ]
        state["current_stage"] = "literature"
        self.write_state(state)
        invalid_transition = self.run_ctl("doctor")
        self.assertEqual(invalid_transition.returncode, 1)
        self.assertIn("stage_history[0] has unknown fields", invalid_transition.stdout)


if __name__ == "__main__":
    unittest.main()
