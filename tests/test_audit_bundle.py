from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.researchctl_core.audit_bundle import export_bundle, verify_bundle
from scripts.researchctl_core.constants import ResearchCtlError
from scripts.researchctl_core.policy import load_policy

try:
    from .research_test_support import ResearchProjectTestCase
except ImportError:  # unittest discover -s tests
    from research_test_support import ResearchProjectTestCase


MANIFEST_PATH = "audit-manifest.json"


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _content_hash(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _member_bytes(archive: Path) -> dict[str, bytes]:
    with tarfile.open(archive, mode="r:") as bundle:
        return {
            member.name: bundle.extractfile(member).read()
            for member in bundle.getmembers()
            if member.isfile()
        }


def _write_archive(
    destination: Path,
    members: list[tuple[tarfile.TarInfo, bytes]],
) -> None:
    with tarfile.open(destination, mode="w", format=tarfile.GNU_FORMAT) as bundle:
        for original, content in members:
            member = tarfile.TarInfo(original.name)
            member.type = original.type
            member.linkname = original.linkname
            member.mode = original.mode
            member.uid = original.uid
            member.gid = original.gid
            member.uname = original.uname
            member.gname = original.gname
            member.mtime = original.mtime
            member.size = len(content) if member.isfile() else 0
            bundle.addfile(member, io.BytesIO(content) if member.isfile() else None)


def _read_members(archive: Path) -> list[tuple[tarfile.TarInfo, bytes]]:
    with tarfile.open(archive, mode="r:") as bundle:
        return [
            (
                member,
                bundle.extractfile(member).read() if member.isfile() else b"",
            )
            for member in bundle.getmembers()
        ]


class AuditBundleTest(ResearchProjectTestCase):
    def setUp(self) -> None:
        super().setUp()
        artifact_id, source, first = self.register(
            "idea.idea_card",
            "IDEA-AUDIT",
            content="first immutable revision\n",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        source.write_text("second immutable revision\n", encoding="utf-8")
        _artifact_id, _source, second = self.register(
            "idea.idea_card",
            artifact_id,
            path=source,
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.source = source

    def export(self, destination: Path) -> dict[str, object]:
        return export_bundle(
            self.project,
            self.load_state(),
            load_policy(),
            destination,
        )

    def test_rebuild_is_deterministic_offline_and_preserves_project_state(self) -> None:
        first = Path(self.temporary.name) / "first.audit.tar"
        second = Path(self.temporary.name) / "second.audit.tar"
        state_before = self.state_path.read_bytes()
        memory_before = (self.project / ".research/memory.md").read_bytes()

        first_descriptor = self.export(first)
        second_descriptor = self.export(second)

        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(
            first_descriptor["evidence_root"], second_descriptor["evidence_root"]
        )
        self.assertEqual(self.state_path.read_bytes(), state_before)
        self.assertEqual(
            (self.project / ".research/memory.md").read_bytes(), memory_before
        )

        with tarfile.open(first, mode="r:") as bundle:
            members = bundle.getmembers()
            self.assertEqual(members[0].name, MANIFEST_PATH)
            self.assertEqual(
                [member.name for member in members[1:]],
                sorted(member.name for member in members[1:]),
            )
            self.assertTrue(all(member.isfile() for member in members))
            self.assertTrue(all(member.mode == 0o644 for member in members))
            self.assertTrue(all(member.mtime == 0 for member in members))
            self.assertTrue(all(member.uid == member.gid == 0 for member in members))

        payload = _member_bytes(first)
        manifest = json.loads(payload[MANIFEST_PATH])
        snapshot_paths = [
            entry["path"]
            for entry in manifest["entries"]
            if entry["kind"] == "snapshot"
        ]
        self.assertEqual(len(snapshot_paths), 2)
        memory_entry = next(
            entry for entry in manifest["entries"] if entry["kind"] == "memory"
        )
        self.assertFalse(memory_entry["authoritative"])
        self.assertIn(
            "memory_is_non_authoritative_navigation_only", manifest["limitations"]
        )
        trace = json.loads(payload["projections/trace.json"])
        self.assertEqual(trace["status"], "available")
        self.assertEqual(trace["nodes"], [])
        self.assertEqual(trace["edges"], [])
        self.assertEqual(trace["summary"]["record_count"], 0)
        self.assertNotIn("trace_projection_unavailable", manifest["limitations"])

        self.source.unlink()
        report = verify_bundle(
            first, expected_root=first_descriptor["evidence_root"]
        )
        self.assertTrue(report["valid"], report)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["evidence_root"], first_descriptor["evidence_root"])

    def test_verification_uses_bundled_policy_and_runtime_contract(self) -> None:
        archive = Path(self.temporary.name) / "offline.audit.tar"
        descriptor = self.export(archive)
        invalid_policy = Path(self.temporary.name) / "invalid-policy.json"
        invalid_runtime = Path(self.temporary.name) / "invalid-runtime.json"
        invalid_policy.write_text("{}\n", encoding="utf-8")
        invalid_runtime.write_text("{}\n", encoding="utf-8")

        with mock.patch.dict(
            os.environ,
            {
                "RESEARCHCTL_POLICY": str(invalid_policy),
                "RESEARCHCTL_RUNTIME_CONTRACT": str(invalid_runtime),
            },
        ):
            report = verify_bundle(
                archive, expected_root=descriptor["evidence_root"]
            )

        self.assertTrue(report["valid"], report)

    def test_self_consistent_forged_trace_is_rejected(self) -> None:
        original = Path(self.temporary.name) / "trace-original.audit.tar"
        self.export(original)
        members = _read_members(original)
        manifest = json.loads(
            next(
                content
                for member, content in members
                if member.name == MANIFEST_PATH
            )
        )
        forged_trace = _canonical_json(
            {
                "schema_version": "1.0",
                "status": "available",
                "nodes": [{"record_id": "FABRICATED"}],
                "edges": [],
                "summary": {"record_count": 1},
            }
        )
        trace_entry = next(
            entry
            for entry in manifest["entries"]
            if entry["kind"] == "trace_projection"
        )
        trace_entry["content_hash"] = _content_hash(forged_trace)
        trace_entry["size_bytes"] = len(forged_trace)
        root_material = {
            "bundle_schema_version": manifest["bundle_schema_version"],
            "versions": manifest["versions"],
            "entries": manifest["entries"],
            "limitations": manifest["limitations"],
        }
        manifest["evidence_root"] = _content_hash(_canonical_json(root_material))
        forged_manifest = _canonical_json(manifest)
        forged = Path(self.temporary.name) / "trace-forged.audit.tar"
        _write_archive(
            forged,
            [
                (
                    member,
                    forged_manifest
                    if member.name == MANIFEST_PATH
                    else forged_trace
                    if member.name == "projections/trace.json"
                    else content,
                )
                for member, content in members
            ],
        )

        report = verify_bundle(
            forged, expected_root=manifest["evidence_root"]
        )

        self.assertFalse(report["valid"], report)
        self.assertTrue(
            any(
                "trace projection does not match bundled evidence" in error
                for error in report["errors"]
            ),
            report,
        )

    def test_export_destination_must_be_new_and_outside_the_project(self) -> None:
        state_before = self.state_path.read_bytes()
        source_before = self.source.read_bytes()

        for destination in (
            self.state_path,
            self.project / ".research/state.lock",
            self.source,
            self.project / "new-audit.tar",
        ):
            with self.subTest(destination=destination):
                with self.assertRaisesRegex(
                    ResearchCtlError, "outside the research project"
                ):
                    self.export(destination)

        self.assertEqual(self.state_path.read_bytes(), state_before)
        self.assertEqual(self.source.read_bytes(), source_before)

        existing = Path(self.temporary.name) / "existing.audit.tar"
        existing.write_bytes(b"unrelated bytes\n")
        with self.assertRaisesRegex(ResearchCtlError, "must not already exist"):
            self.export(existing)
        self.assertEqual(existing.read_bytes(), b"unrelated bytes\n")

    def test_terminal_cli_export_cannot_overwrite_a_live_source(self) -> None:
        terminated = self.lifecycle("terminate")
        self.assertEqual(terminated.returncode, 0, terminated.stderr)
        state_before = self.state_path.read_bytes()
        source_before = self.source.read_bytes()

        rejected = self.run_ctl(
            "audit",
            "export",
            "--output",
            str(self.source),
        )

        self.assertEqual(rejected.returncode, 2, rejected.stdout + rejected.stderr)
        self.assertIn("outside the research project", rejected.stderr)
        self.assertEqual(self.state_path.read_bytes(), state_before)
        self.assertEqual(self.source.read_bytes(), source_before)

    def test_tampered_missing_and_unregistered_entries_are_rejected(self) -> None:
        original = Path(self.temporary.name) / "original.audit.tar"
        descriptor = self.export(original)
        members = _read_members(original)
        snapshot_name = next(
            name
            for name, content in (
                (member.name, content) for member, content in members
            )
            if name.startswith("workspace/.research/snapshots/")
        )

        tampered = Path(self.temporary.name) / "tampered.audit.tar"
        _write_archive(
            tampered,
            [
                (member, b"tampered\n" if member.name == snapshot_name else content)
                for member, content in members
            ],
        )
        tampered_report = verify_bundle(
            tampered, expected_root=descriptor["evidence_root"]
        )
        self.assertFalse(tampered_report["valid"])
        self.assertTrue(
            any(
                "content hash mismatch" in error
                for error in tampered_report["errors"]
            ),
            tampered_report,
        )

        missing = Path(self.temporary.name) / "missing.audit.tar"
        _write_archive(
            missing,
            [
                (member, content)
                for member, content in members
                if member.name != snapshot_name
            ],
        )
        missing_report = verify_bundle(missing)
        self.assertFalse(missing_report["valid"])
        self.assertTrue(
            any(
                "missing registered entries" in error
                for error in missing_report["errors"]
            ),
            missing_report,
        )

        extra = Path(self.temporary.name) / "extra.audit.tar"
        extra_info = tarfile.TarInfo("workspace/unregistered.txt")
        extra_info.mode = 0o644
        extra_info.uid = extra_info.gid = 0
        extra_info.mtime = 0
        _write_archive(extra, [*members, (extra_info, b"extra\n")])
        extra_report = verify_bundle(extra)
        self.assertFalse(extra_report["valid"])
        self.assertTrue(
            any("unregistered entries" in error for error in extra_report["errors"]),
            extra_report,
        )

    def test_hostile_archive_paths_duplicates_case_collisions_and_links_are_rejected(
        self,
    ) -> None:
        original = Path(self.temporary.name) / "safe.audit.tar"
        self.export(original)
        members = _read_members(original)

        cases: list[tuple[str, tarfile.TarInfo, str]] = []
        traversal = tarfile.TarInfo("../escape.json")
        traversal.mode = 0o644
        traversal.uid = traversal.gid = 0
        traversal.mtime = 0
        cases.append(("traversal", traversal, "unsafe archive path"))

        duplicate = tarfile.TarInfo(members[1][0].name)
        duplicate.mode = 0o644
        duplicate.uid = duplicate.gid = 0
        duplicate.mtime = 0
        cases.append(("duplicate", duplicate, "duplicate archive path"))

        collision = tarfile.TarInfo(members[1][0].name.swapcase())
        collision.mode = 0o644
        collision.uid = collision.gid = 0
        collision.mtime = 0
        cases.append(("casefold", collision, "case-insensitive archive path collision"))

        symlink = tarfile.TarInfo("workspace/link")
        symlink.type = tarfile.SYMTYPE
        symlink.linkname = "../outside"
        symlink.mode = 0o777
        symlink.uid = symlink.gid = 0
        symlink.mtime = 0
        cases.append(("symlink", symlink, "non-regular archive entry"))

        for label, hostile, expected in cases:
            with self.subTest(label=label):
                archive = Path(self.temporary.name) / f"{label}.audit.tar"
                _write_archive(archive, [*members, (hostile, b"hostile\n")])
                report = verify_bundle(archive)
                self.assertFalse(report["valid"])
                self.assertTrue(
                    any(expected in error for error in report["errors"]), report
                )

    def test_expected_evidence_root_must_match(self) -> None:
        archive = Path(self.temporary.name) / "root.audit.tar"
        descriptor = self.export(archive)

        accepted = verify_bundle(
            archive, expected_root=descriptor["evidence_root"]
        )
        rejected = verify_bundle(
            archive, expected_root="sha256:" + "0" * 64
        )

        self.assertTrue(accepted["valid"], accepted)
        self.assertFalse(rejected["valid"])
        self.assertTrue(
            any("expected evidence root" in error for error in rejected["errors"]),
            rejected,
        )

    def test_cli_exports_and_verifies_against_an_external_evidence_root(self) -> None:
        archive = Path(self.temporary.name) / "cli.audit.tar"

        exported = self.run_ctl(
            "audit",
            "export",
            "--output",
            str(archive),
        )

        self.assertEqual(exported.returncode, 0, exported.stderr)
        descriptor = json.loads(exported.stdout)
        self.assertTrue(archive.is_file())
        verified = self.run_ctl(
            "audit",
            "verify",
            str(archive),
            "--expected-root",
            descriptor["evidence_root"],
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertTrue(json.loads(verified.stdout)["valid"])

        rejected = self.run_ctl(
            "audit",
            "verify",
            str(archive),
            "--expected-root",
            "sha256:" + "0" * 64,
        )
        self.assertEqual(rejected.returncode, 1, rejected.stderr)
        self.assertFalse(json.loads(rejected.stdout)["valid"])


if __name__ == "__main__":
    unittest.main()
