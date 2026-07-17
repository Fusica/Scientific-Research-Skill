from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

try:
    from .research_test_support import ResearchProjectTestCase
except ImportError:  # unittest discover -s tests
    from research_test_support import ResearchProjectTestCase

from scripts.researchctl_core.policy import load_policy
from scripts.researchctl_core.records import (
    PendingRecordManifest,
    inspect_record_manifests,
)
from scripts.researchctl_core.trace import build_trace_summary, query_trace


class TraceGraphTest(ResearchProjectTestCase):
    def _manifest(
        self,
        records: list[dict[str, Any]],
        *,
        source_role: str = "idea_card",
        source_artifact_id: str = "IDEA-TRACE-SOURCE",
    ) -> dict[str, Any]:
        revision = self.artifact_entry(
            f"idea.{source_role}", source_artifact_id
        )["current_revision"]
        materialized = []
        for record in records:
            candidate = copy.deepcopy(record)
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
            "stage": "idea",
            "records": materialized,
        }

    def _write_manifest(self, manifest: dict[str, Any], name: str) -> Path:
        path = self.project / "work" / "idea" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def _register_trace(self, records: list[dict[str, Any]]):
        _source_id, _source, source_result = self.register(
            "idea.idea_card",
            "IDEA-TRACE-SOURCE",
            content="# Trace source\n",
        )
        self.assertEqual(source_result.returncode, 0, source_result.stderr)
        manifest_path = self._write_manifest(
            self._manifest(records), "record-manifest.json"
        )
        _manifest_id, _path, manifest_result = self.register(
            "idea.record_manifest",
            "IDEA-TRACE-RECORDS",
            path=manifest_path,
        )
        self.assertEqual(manifest_result.returncode, 0, manifest_result.stderr)
        return inspect_record_manifests(
            self.project,
            self.load_state(),
            load_policy(),
        )

    @staticmethod
    def _chain_records() -> list[dict[str, Any]]:
        return [
            {
                "record_id": "CAND-003",
                "record_kind": "candidate",
                "relations": [
                    {"relation": "derived_from", "target_id": "CAND-002"}
                ],
            },
            {
                "record_id": "EVID-001",
                "record_kind": "passage_evidence",
                "relations": [
                    {"relation": "supports", "target_id": "CAND-001"}
                ],
            },
            {
                "record_id": "CAND-001",
                "record_kind": "candidate",
            },
            {
                "record_id": "CAND-002",
                "record_kind": "candidate",
                "relations": [
                    {"relation": "derived_from", "target_id": "CAND-001"}
                ],
            },
        ]

    def test_projection_and_summary_have_stable_order(self) -> None:
        inspection = self._register_trace(self._chain_records())

        self.assertEqual(inspection.errors, ())
        self.assertEqual(
            [node["record_id"] for node in inspection.nodes],
            ["CAND-001", "CAND-002", "CAND-003", "EVID-001"],
        )
        self.assertEqual(
            [
                (edge["source_id"], edge["relation"], edge["target_id"])
                for edge in inspection.edges
            ],
            [
                ("CAND-002", "derived_from", "CAND-001"),
                ("CAND-003", "derived_from", "CAND-002"),
                ("EVID-001", "supports", "CAND-001"),
            ],
        )
        summary = build_trace_summary(inspection)
        self.assertEqual(
            summary["records_by_kind"],
            {"candidate": 3, "passage_evidence": 1},
        )
        self.assertEqual(
            summary["relations_by_kind"],
            {"derived_from": 2, "supports": 1},
        )
        self.assertEqual(summary["diagnostics"]["orphans"], [])
        encoded = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        self.assertEqual(
            encoded,
            json.dumps(build_trace_summary(inspection), ensure_ascii=False, sort_keys=True),
        )

    def test_query_supports_both_directions_and_multiple_depths(self) -> None:
        inspection = self._register_trace(self._chain_records())

        upstream = query_trace(
            inspection, "CAND-001", direction="upstream", depth=2
        )
        self.assertEqual(
            {node["record_id"]: node["distance"] for node in upstream["nodes"]},
            {"CAND-001": 0, "CAND-002": 1, "CAND-003": 2, "EVID-001": 1},
        )
        downstream = query_trace(
            inspection, "CAND-003", direction="downstream", depth=2
        )
        self.assertEqual(
            {node["record_id"]: node["distance"] for node in downstream["nodes"]},
            {"CAND-001": 2, "CAND-002": 1, "CAND-003": 0},
        )
        both = query_trace(inspection, "CAND-002", direction="both", depth=1)
        self.assertEqual(
            [node["record_id"] for node in both["nodes"]],
            ["CAND-001", "CAND-002", "CAND-003"],
        )

    def test_cli_trace_and_doctor_json_expose_projection_without_mutation(self) -> None:
        self._register_trace(self._chain_records())
        state_before = self.state_path.read_bytes()

        traced = self.run_ctl(
            "trace",
            "CAND-001",
            "--direction",
            "upstream",
            "--depth",
            "2",
        )
        diagnosed = self.run_ctl("doctor", "--json")

        self.assertEqual(traced.returncode, 0, traced.stderr)
        payload = json.loads(traced.stdout)
        self.assertEqual(payload["output_schema_version"], "1.0")
        self.assertEqual(payload["query"]["record_id"], "CAND-001")
        self.assertEqual(payload["query"]["node_count"], 4)
        self.assertEqual(diagnosed.returncode, 0, diagnosed.stderr)
        diagnostic = json.loads(diagnosed.stdout)
        self.assertTrue(diagnostic["valid"])
        self.assertEqual(diagnostic["error_count"], 0)
        self.assertEqual(self.state_path.read_bytes(), state_before)

    def test_orphan_is_a_warning_and_not_an_error(self) -> None:
        inspection = self._register_trace(
            [{"record_id": "ORPHAN-001", "record_kind": "candidate"}]
        )

        self.assertEqual(inspection.errors, ())
        self.assertEqual(inspection.diagnostics["orphans"], ("ORPHAN-001",))
        self.assertTrue(any("structurally orphaned" in item for item in inspection.warnings))
        summary = build_trace_summary(inspection)
        self.assertEqual(summary["diagnostics"]["orphans"], ["ORPHAN-001"])

    def test_derived_from_cycle_is_an_error_and_inspection_is_read_only(self) -> None:
        _source_id, _source, source_result = self.register(
            "idea.idea_card",
            "IDEA-TRACE-SOURCE",
            content="# Cycle source\n",
        )
        self.assertEqual(source_result.returncode, 0, source_result.stderr)
        manifest = self._manifest(
            [
                {
                    "record_id": "CYCLE-A",
                    "record_kind": "candidate",
                    "relations": [
                        {"relation": "derived_from", "target_id": "CYCLE-B"}
                    ],
                },
                {
                    "record_id": "CYCLE-B",
                    "record_kind": "candidate",
                    "relations": [
                        {"relation": "derived_from", "target_id": "CYCLE-A"}
                    ],
                },
            ]
        )
        manifest_path = self._write_manifest(manifest, "pending-cycle.json")
        state = self.load_state()
        state_before = copy.deepcopy(state)
        bytes_before = self.state_path.read_bytes()

        inspection = inspect_record_manifests(
            self.project,
            state,
            load_policy(),
            pending=PendingRecordManifest(
                stage="idea",
                artifact_id="IDEA-CYCLE-RECORDS",
                path=manifest_path,
            ),
        )
        query = query_trace(inspection, "CYCLE-A", direction="both", depth=3)

        self.assertTrue(any("derived_from relation cycle" in item for item in inspection.errors))
        self.assertEqual(inspection.diagnostics["cycles"][0]["severity"], "error")
        self.assertEqual(query["node_count"], 2)
        self.assertEqual(state, state_before)
        self.assertEqual(self.state_path.read_bytes(), bytes_before)

    def test_general_relation_cycle_is_diagnostic_only(self) -> None:
        _source_id, _source, source_result = self.register(
            "idea.idea_card",
            "IDEA-TRACE-SOURCE",
            content="# General cycle source\n",
        )
        self.assertEqual(source_result.returncode, 0, source_result.stderr)
        manifest_path = self._write_manifest(
            self._manifest(
                [
                    {
                        "record_id": "GENERAL-A",
                        "record_kind": "candidate",
                        "relations": [
                            {"relation": "supports", "target_id": "GENERAL-B"}
                        ],
                    },
                    {
                        "record_id": "GENERAL-B",
                        "record_kind": "candidate",
                        "relations": [
                            {"relation": "supports", "target_id": "GENERAL-A"}
                        ],
                    },
                ]
            ),
            "pending-general-cycle.json",
        )
        policy = load_policy()
        signatures = dict(policy.runtime.scientific_record_relation_signatures)
        signatures["supports"] = (("candidate",), ("candidate",))
        policy = replace(
            policy,
            runtime=replace(
                policy.runtime,
                scientific_record_relation_signatures=signatures,
            ),
        )

        inspection = inspect_record_manifests(
            self.project,
            self.load_state(),
            policy,
            pending=PendingRecordManifest(
                stage="idea",
                artifact_id="IDEA-GENERAL-CYCLE-RECORDS",
                path=manifest_path,
            ),
        )

        self.assertFalse(any("relation cycle" in item for item in inspection.errors))
        self.assertTrue(any("relation cycle" in item for item in inspection.warnings))
        self.assertEqual(inspection.diagnostics["cycles"][0]["severity"], "warning")

    def test_invalid_graph_facts_remain_fail_closed_and_are_diagnosed(self) -> None:
        _source_id, _source, source_result = self.register(
            "idea.idea_card",
            "IDEA-TRACE-SOURCE",
            content="# Invalid graph source\n",
        )
        self.assertEqual(source_result.returncode, 0, source_result.stderr)
        manifest_path = self._write_manifest(
            self._manifest(
                [
                    {
                        "record_id": "INVALID-A",
                        "record_kind": "candidate",
                        "relations": [
                            {
                                "relation": "derived_from",
                                "target_id": "MISSING-RECORD",
                            }
                        ],
                    },
                    {
                        "record_id": "INVALID-B",
                        "record_kind": "candidate",
                        "supersedes": "NOT-EARLIER",
                    },
                    {
                        "record_id": "INVALID-A",
                        "record_kind": "candidate",
                    },
                ]
            ),
            "pending-invalid-graph.json",
        )

        inspection = inspect_record_manifests(
            self.project,
            self.load_state(),
            load_policy(),
            pending=PendingRecordManifest(
                stage="idea",
                artifact_id="IDEA-INVALID-GRAPH-RECORDS",
                path=manifest_path,
            ),
        )

        self.assertTrue(any("references unknown record" in item for item in inspection.errors))
        self.assertTrue(any("is duplicated" in item for item in inspection.errors))
        self.assertTrue(
            any(
                "supersedes must reference an earlier record" in item
                for item in inspection.errors
            )
        )
        self.assertTrue(inspection.diagnostics["dangling"])
        self.assertTrue(inspection.diagnostics["duplicates"])
        self.assertTrue(inspection.diagnostics["invalid_supersedes"])


if __name__ == "__main__":
    import unittest

    unittest.main()
