from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.capability_acceptance import (
    AcceptanceInputError,
    DIMENSION_IDS,
    EVIDENCE_LAYERS,
    REPRESENTATIVE_MIN_CASES,
    REPRESENTATIVE_REQUIRED_CASES,
    REPORT_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    evaluate_report,
    load_report,
    validate_report,
)
from scripts.acceptance_evidence import EvidencePack, EvidencePackError
from scripts.innovation_benchmark import InnovationRawError, recompute_raw_benchmark


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/validate_acceptance.py"

EXCLUSIONS = [
    "paper_quality",
    "publication_acceptance",
    "real_novelty",
    "scientific_correctness",
    "statistical_validity",
    "universal_external_action_interception",
]

DIMENSION_DECLARATIONS = {
    "workflow_governance": {
        "boundary": "Core",
        "target": "Very high",
        "authority": ["gate_decisions", "lifecycle_decisions"],
        "stack": None,
    },
    "project_audit": {
        "boundary": "Core",
        "target": "Very high",
        "authority": ["gate_decisions", "lifecycle_decisions"],
        "stack": None,
    },
    "experiment_execution": {
        "boundary": "Core + Reference Stack",
        "target": "End-to-end Very high",
        "authority": [
            "gate_decisions",
            "costly_compute",
            "destructive_operations",
            "safety_relevant_hardware",
        ],
        "stack": {
            "name": "maintainer experiment reference stack",
            "version": "2026.07",
            "components": ["runner adapter", "artifact importer"],
        },
    },
    "paper_production": {
        "boundary": "Core + Reference Stack",
        "target": "End-to-end Very high",
        "authority": ["gate_decisions", "external_submission"],
        "stack": {
            "name": "maintainer paper reference stack",
            "version": "2026.07",
            "components": ["venue profile", "render checker"],
        },
    },
    "knowledge_management": {
        "boundary": "Project-local Core",
        "target": "High",
        "authority": ["gate_decisions"],
        "stack": None,
    },
    "innovation_elicitation": {
        "boundary": "Track A: Core; Track A + B: declared native stack",
        "target": "Approaches EvoSkills / Approaches the Evo native ecosystem",
        "authority": ["idea_freeze", "scientific_selection"],
        "stack": None,
    },
}


def benchmark_metadata() -> dict[str, str]:
    return {
        "harness_version": "acceptance-1",
        "corpus_version": "frozen-corpus-1",
        "evaluation_date": "2026-07-16",
        "retained_report": "reports/acceptance-2026-07-16.json",
    }


def evidence_for(dimension_id: str) -> dict[str, list[dict[str, Any]]]:
    provenance = {
        "deterministic": {
            "kind": "deterministic",
            "tool": "unittest",
            "tool_version": "3.11",
            "command_ref": "reports/commands.json#unit",
        },
        "representative": {
            "kind": "representative",
            "corpus_id": f"{dimension_id}-corpus",
            "corpus_version": "1",
            "case_count": 3,
            "corpus_ref": f"reports/{dimension_id}-corpus-result.json",
        },
        "human": {
            "kind": "human",
            "review_kind": "blinded_panel",
            "protocol_ref": "reports/human-protocol.json",
            "reviewer_count": 3,
            "blinded": True,
            "conflicts_screened": True,
        },
        "venue_fact": {
            "kind": "venue_fact",
            "source_url": "https://example.org/venue/rules",
            "source_date": "2026-07-16",
            "venue_profile_ref": "reports/venue-profile.json",
        },
        "benchmark": {
            "kind": "benchmark",
            "harness_version": "acceptance-1",
            "corpus_version": "frozen-corpus-1",
            "comparison_report_ref": "reports/comparison.json",
        },
        "failure_recovery": {
            "kind": "failure_recovery",
            "failure_injection_ref": "reports/failures.json#injection",
            "recovery_check_ref": "reports/failures.json#recovery",
        },
        "offline_audit": {
            "kind": "offline_audit",
            "bundle_ref": "reports/audit.tar",
            "evidence_root": "sha256:" + "a" * 64,
        },
        "adversarial": {
            "kind": "adversarial",
            "protocol_ref": "reports/adversarial-protocol.json",
            "attack_case_count": 5,
        },
        "cross_stage": {
            "kind": "cross_stage",
            "start_stage": "idea",
            "end_stage": "paper",
            "trace_ref": "reports/cross-stage-trace.json",
        },
        "adapter": {
            "kind": "adapter",
            "adapter_id": "reference-isolated-command",
            "adapter_version": "1.0.0",
            "request_ref": "reports/adapter-exchange.json#request",
            "receipt_ref": "reports/adapter-exchange.json#receipt",
        },
    }
    return {
        layer: [
            {
                "evidence_id": f"{dimension_id}-{layer}-1",
                "evidence_class": layer,
                "passed": True,
                "scenario_ids": [f"{dimension_id}-scenario-1"],
                "report_ref": f"reports/{dimension_id}-{layer}.json#result",
                "content_hash": "sha256:"
                + hashlib.sha256(
                    f"{dimension_id}:{layer}".encode("utf-8")
                ).hexdigest(),
                "provenance": copy.deepcopy(provenance[layer]),
                "finding": f"Retained {layer} evidence passed.",
            }
        ]
        for layer in EVIDENCE_LAYERS
    }


def frozen_design() -> dict[str, Any]:
    return {
        "evoskills_commit": "29e2c67f12858829ad0900645432b340c3f77522",
        "evoscientist_commit": "01845f43110ad444b7e2a61b920effdf7e719029",
        "evoscientist_host_version": "0.2.2",
        "same_host": True,
        "same_primary_model": True,
        "same_total_token_budget": True,
        "same_tool_budget": True,
        "same_worker_limit": True,
        "same_resource_constraints": True,
        "evidence_pack_min_papers": 30,
        "evidence_pack_max_papers": 50,
        "calibration_queries": 30,
        "held_out_queries": 20,
        "disciplines": 4,
        "adversarial_controls": 15,
        "runs_per_query": 3,
        "domain_experts_blinded": True,
        "position_swapped": True,
        "closest_prior_work_verified": True,
        "query_clustered_paired_bootstrap": True,
        "confidence_level": 0.95,
        "track_a_host_memory_empty": True,
        "auxiliary_models_and_memory_workers_counted": True,
    }


def track_a_at_boundaries() -> dict[str, Any]:
    return {
        "novelty_lcb": -0.05,
        "composite_lcb": -0.05,
        "dimension_lcbs": {
            "novelty": -0.05,
            "feasibility": -0.10,
            "relevance": -0.10,
            "clarity": -0.10,
        },
        "valid_diverse_yield_ratio": 0.90,
        "duplicate_rate": 0.10,
        "false_novelty_rate": 0.05,
        "false_novelty_delta_percentage_points": 2.0,
        "flaw_recall": 0.85,
        "flaw_precision": 0.75,
        "repair_success": 0.70,
        "false_prune_rate": 0.05,
        "top1_in_expert_top3_rate": 0.80,
        "normalized_regret": 0.05,
        "kendall_tau": 0.70,
        "token_ratio_vs_evo": 1.25,
        "cost_ratio_vs_evo": 1.25,
        "pareto_observations": None,
    }


def track_b_at_boundaries() -> dict[str, Any]:
    return {
        "warm_cycle_count": 3,
        "cold_start_baseline_present": True,
        "project_local_same_mainline": True,
        "cross_workspace_memory_used": False,
        "confirmed_dead_end_recurrence_reduction": 0.50,
        "overall_idea_quality_delta": 0.0,
        "false_prune_rate": 0.05,
    }


def raw_track_a_fixture() -> dict[str, Any]:
    pairs: list[dict[str, Any]] = []
    costs: list[dict[str, Any]] = []
    held_out_query_ids = [f"HELD-OUT-{index + 1:03d}" for index in range(20)]
    calibration_query_ids = [
        f"CALIBRATION-{index + 1:03d}" for index in range(30)
    ]
    for query_index in range(20):
        query_id = f"HELD-OUT-{query_index + 1:03d}"
        discipline = f"discipline-{query_index % 4 + 1}"
        for run_index in range(3):
            run_id = f"{query_id}-RUN-{run_index + 1}"
            score = {
                "novelty": 0.8,
                "feasibility": 0.8,
                "relevance": 0.8,
                "clarity": 0.8,
            }
            binding = {
                "host_id": "HOST-001",
                "primary_model": "MODEL-001",
                "token_budget": 100000.0,
                "tool_budget": 100,
                "worker_limit": 4,
                "resource_constraints_hash": "sha256:" + "b" * 64,
                "host_memory_empty": True,
            }
            for reviewer_id in ("REVIEWER-001", "REVIEWER-002"):
                for position in ("scientific_left", "evo_left"):
                    pairs.append(
                        {
                            "query_id": query_id,
                            "run_id": run_id,
                            "discipline": discipline,
                            "reviewer_id": reviewer_id,
                            "position": position,
                            "reviewer_blinded": True,
                            "closest_prior_work_verified": True,
                            "scientific_binding": copy.deepcopy(binding),
                            "evo_binding": copy.deepcopy(binding),
                            "scientific": copy.deepcopy(score),
                            "evo": copy.deepcopy(score),
                        }
                    )
            for system in ("scientific-research-skill", "evo"):
                costs.append(
                    {
                        "run_id": run_id,
                        "system": system,
                        "token_cost": 100.0,
                        "monetary_cost": 1.0,
                    }
                )
    return {
        "schema_version": "1.0",
        "track_a": {
            "held_out_query_ids": held_out_query_ids,
            "preregistration_hash": "sha256:"
            + hashlib.sha256(
                json.dumps(
                    {
                        "calibration_query_ids": sorted(calibration_query_ids),
                        "held_out_query_ids": sorted(held_out_query_ids),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "query_evidence_packs": [
                {
                    "query_id": query_id,
                    "paper_ids": [
                        f"{query_id}-PAPER-{paper_index + 1:03d}"
                        for paper_index in range(30)
                    ],
                }
                for query_id in held_out_query_ids
            ],
            "calibration_query_ids": calibration_query_ids,
            "adversarial_control_ids": [
                f"CONTROL-{index + 1:03d}" for index in range(15)
            ],
            "paired_scores": pairs,
            "candidate_counts": {
                "valid_diverse": 90,
                "total_candidates": 100,
                "evo_valid_diverse": 100,
                "evo_total_candidates": 100,
                "duplicates": 10,
                "false_novelty": 5,
                "novelty_predictions": 100,
                "evo_false_novelty": 3,
                "evo_novelty_predictions": 100,
                "flaw_true_positive": 85,
                "flaw_false_positive": 28,
                "flaw_false_negative": 15,
                "repairs_succeeded": 70,
                "repairs_attempted": 100,
                "false_pruned": 5,
                "pruned_total": 100,
                "top1_in_expert_top3": 16,
                "ranking_queries": 20,
            },
            "ranking_observations": [
                {
                    "query_id": f"HELD-OUT-{index + 1:03d}",
                    "normalized_regret": 0.05,
                    "kendall_tau": 0.70,
                }
                for index in range(20)
            ],
            "cost_observations": costs,
            "pareto_quality_observations": [],
        },
        "track_b": None,
    }


def write_evidence_pack(
    root: Path,
    artifacts: dict[str, tuple[str, bytes]],
) -> dict[str, Any]:
    evidence_root = root / "evidence"
    evidence_root.mkdir()
    entries: list[dict[str, Any]] = []
    for index, (artifact_id, (media_type, content)) in enumerate(artifacts.items()):
        path = evidence_root / f"artifact-{index + 1:03d}.bin"
        path.write_bytes(content)
        entries.append(
            {
                "artifact_id": artifact_id,
                "path": path.relative_to(root).as_posix(),
                "media_type": media_type,
                "content_hash": "sha256:" + hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        )
    manifest = {
        "schema_version": "1.0",
        "artifacts": entries,
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    manifest_path = root / "evidence-pack.json"
    manifest_path.write_bytes(manifest_bytes)
    return {
        "manifest_ref": manifest_path.relative_to(root).as_posix(),
        "content_hash": "sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
        "size_bytes": len(manifest_bytes),
    }


def valid_report() -> dict[str, Any]:
    dimensions: dict[str, dict[str, Any]] = {}
    for dimension_id, declaration in DIMENSION_DECLARATIONS.items():
        dimensions[dimension_id] = {
            "boundary": declaration["boundary"],
            "status": "Target",
            "target": declaration["target"],
            "exclusions": list(EXCLUSIONS),
            "human_authority": list(declaration["authority"]),
            "reference_stack": copy.deepcopy(declaration["stack"]),
            "benchmark_metadata": (
                benchmark_metadata()
                if dimension_id == "innovation_elicitation"
                else None
            ),
            "evidence": evidence_for(dimension_id),
        }
    invariants = []
    for identifier, invariant_class in (
        ("human_gate_authority", "authority"),
        ("external_action_authority", "authority"),
        ("immutable_artifact_provenance", "provenance"),
        ("negative_outcome_retention", "provenance"),
        ("no_scientific_truth_certification", "authority"),
    ):
        invariants.append(
            {
                "invariant_id": identifier,
                "class": invariant_class,
                "passed": True,
                "evidence_refs": [f"reports/invariants.json#{identifier}"],
                "finding": "The retained audit found no violation.",
            }
        )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_id": "acceptance-boundary-fixture",
        "evaluated_at": "2026-07-16T12:00:00+08:00",
        "system_under_test": {
            "name": "scientific-research-skill",
            "version": "2.0.0",
            "commit": "0123456789abcdef",
        },
        "assessment_boundary": {
            "kind": "maintainer_semantic_acceptance",
            "workflow_state": False,
            "gate_authority": False,
            "scientific_truth": False,
        },
        "zero_tolerance_invariants": invariants,
        "dimensions": dimensions,
        "innovation_benchmark": {
            "comparison_claim": "approaches_evoskills",
            "design": frozen_design(),
            "track_a": track_a_at_boundaries(),
            "track_b": None,
            "raw_observations_ref": None,
        },
        "evidence_pack": None,
    }


def set_nested(mapping: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cursor = mapping
    for part in parts[:-1]:
        cursor = cursor[part]
    cursor[parts[-1]] = value


class CapabilityAcceptanceTest(unittest.TestCase):
    def test_frozen_representative_corpus_matches_the_public_asset(self) -> None:
        asset = json.loads(
            (
                ROOT
                / "skills/research/assets/capability-acceptance-corpus.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(asset["schema_version"], "1.0")
        self.assertEqual(
            {
                dimension: specification["minimum_cases"]
                for dimension, specification in asset["dimensions"].items()
            },
            REPRESENTATIVE_MIN_CASES,
        )
        self.assertEqual(
            {
                dimension: set(specification["required_cases"])
                for dimension, specification in asset["dimensions"].items()
            },
            REPRESENTATIVE_REQUIRED_CASES,
        )

    def test_boundary_report_passes_without_upgrading_target(self) -> None:
        result = evaluate_report(valid_report())

        self.assertTrue(result["passed"], result)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["result_schema_version"], RESULT_SCHEMA_VERSION)
        for dimension_id in DIMENSION_IDS:
            dimension = result["dimensions"][dimension_id]
            self.assertEqual(dimension["declared_status"], "Target")
            self.assertEqual(
                dimension["qualification"], "target_contract_valid"
            )
            self.assertFalse(dimension["capability_qualified"])
        self.assertFalse(result["capability_qualified"])
        self.assertIn(
            "Declared Target status is never promoted automatically.",
            result["limitations"],
        )

    def test_every_track_a_boundary_is_inclusive_and_violations_fail(self) -> None:
        self.assertTrue(evaluate_report(valid_report())["passed"])
        violations = {
            "novelty_lcb": -0.050001,
            "composite_lcb": -0.050001,
            "dimension_lcbs.feasibility": -0.100001,
            "valid_diverse_yield_ratio": 0.899999,
            "duplicate_rate": 0.100001,
            "false_novelty_rate": 0.050001,
            "false_novelty_delta_percentage_points": 2.000001,
            "flaw_recall": 0.849999,
            "flaw_precision": 0.749999,
            "repair_success": 0.699999,
            "false_prune_rate": 0.050001,
            "top1_in_expert_top3_rate": 0.799999,
            "normalized_regret": 0.050001,
            "kendall_tau": 0.699999,
            "token_ratio_vs_evo": 1.250001,
            "cost_ratio_vs_evo": 1.250001,
        }
        for metric_path, failing_value in violations.items():
            with self.subTest(metric=metric_path):
                report = valid_report()
                set_nested(
                    report["innovation_benchmark"]["track_a"],
                    metric_path,
                    failing_value,
                )
                result = evaluate_report(report)
                innovation = result["dimensions"]["innovation_elicitation"]
                self.assertFalse(innovation["passed"], innovation)
                self.assertTrue(
                    any(metric_path in error for error in innovation["errors"]),
                    innovation,
                )

    def test_track_a_thresholds_tolerate_only_machine_roundoff(self) -> None:
        report = valid_report()
        track = report["innovation_benchmark"]["track_a"]
        track["normalized_regret"] = 0.05 + 5e-13
        track["kendall_tau"] = 0.70 - 5e-13

        rounded = evaluate_report(report)

        self.assertTrue(
            rounded["innovation_benchmark"]["reported_track_a_thresholds_passed"]
        )

        track["normalized_regret"] = 0.05 + 2e-12
        outside_tolerance = evaluate_report(report)

        self.assertFalse(
            outside_tolerance["innovation_benchmark"][
                "reported_track_a_thresholds_passed"
            ]
        )

    def test_track_a_missing_metric_and_insufficient_design_fail(self) -> None:
        missing = valid_report()
        del missing["innovation_benchmark"]["track_a"]["flaw_recall"]
        missing_result = evaluate_report(missing)
        self.assertFalse(missing_result["passed"])
        self.assertTrue(
            any(
                "flaw_recall" in error
                for error in missing_result["innovation_benchmark"]["errors"]
            )
        )

        design_violations = {
            "evidence_pack_min_papers": 29,
            "evidence_pack_max_papers": 51,
            "calibration_queries": 29,
            "held_out_queries": 19,
            "disciplines": 3,
            "adversarial_controls": 14,
            "runs_per_query": 2,
            "confidence_level": 0.94,
        }
        for field, failing_value in design_violations.items():
            with self.subTest(field=field):
                report = valid_report()
                report["innovation_benchmark"]["design"][field] = failing_value
                result = evaluate_report(report)
                self.assertFalse(result["passed"])
                self.assertTrue(
                    any(
                        field in error
                        for error in result["innovation_benchmark"]["errors"]
                    ),
                    result["innovation_benchmark"],
                )

    def test_build_success_cannot_substitute_for_human_evidence(self) -> None:
        report = valid_report()
        paper = report["dimensions"]["paper_production"]
        paper["evidence"]["deterministic"][0]["finding"] = (
            "The document built successfully."
        )
        paper["evidence"]["human"] = []

        result = evaluate_report(report)
        paper_result = result["dimensions"]["paper_production"]
        self.assertFalse(paper_result["passed"])
        self.assertEqual(
            paper_result["evidence_layers"]["deterministic"],
            "declared_unverified",
        )
        self.assertEqual(paper_result["evidence_layers"]["human"], "missing")
        self.assertTrue(
            any(
                "human must declare structurally valid" in error
                for error in paper_result["errors"]
            ),
            paper_result,
        )

        relabeled = valid_report()
        paper = relabeled["dimensions"]["paper_production"]
        build = copy.deepcopy(paper["evidence"]["deterministic"][0])
        build["evidence_id"] = "paper-production-relabeled-build"
        build["evidence_class"] = "human"
        build["provenance"] = copy.deepcopy(
            paper["evidence"]["human"][0]["provenance"]
        )
        paper["evidence"]["human"] = [build]
        reused = evaluate_report(relabeled)["dimensions"]["paper_production"]
        self.assertFalse(reused["passed"], reused)
        self.assertTrue(
            any("incompatible evidence classes" in error for error in reused["errors"]),
            reused,
        )

        missing_venue = valid_report()
        missing_venue["dimensions"]["paper_production"]["evidence"][
            "venue_fact"
        ] = []
        venue_result = evaluate_report(missing_venue)["dimensions"][
            "paper_production"
        ]
        self.assertFalse(venue_result["passed"], venue_result)
        self.assertTrue(
            any(
                "venue_fact must declare structurally valid" in error
                for error in venue_result["errors"]
            ),
            venue_result,
        )

    def test_scenario_recovery_and_offline_layers_are_not_interchangeable(self) -> None:
        cases = (
            ("workflow_governance", "representative", "scenario_ids"),
            ("project_audit", "failure_recovery", "layer"),
            ("paper_production", "offline_audit", "layer"),
        )
        for dimension_id, layer, failure_kind in cases:
            with self.subTest(dimension=dimension_id, layer=layer):
                report = valid_report()
                evidence = report["dimensions"][dimension_id]["evidence"]
                if failure_kind == "scenario_ids":
                    evidence[layer][0]["scenario_ids"] = []
                else:
                    evidence[layer] = []
                result = evaluate_report(report)
                dimension = result["dimensions"][dimension_id]
                self.assertFalse(dimension["passed"], dimension)
                self.assertNotEqual(dimension["evidence_layers"][layer], "passed")
                self.assertTrue(
                    any(f"evidence.{layer}" in error for error in dimension["errors"]),
                    dimension,
                )

    def test_reported_failure_in_an_optional_layer_is_not_hidden(self) -> None:
        report = valid_report()
        knowledge = report["dimensions"]["knowledge_management"]
        knowledge["evidence"]["human"][0]["passed"] = False

        result = evaluate_report(report)
        knowledge_result = result["dimensions"]["knowledge_management"]
        self.assertFalse(knowledge_result["passed"])
        self.assertEqual(knowledge_result["evidence_layers"]["human"], "failed")
        self.assertTrue(
            any(
                "evidence.human contains" in error
                for error in knowledge_result["errors"]
            )
        )

    def test_zero_tolerance_failure_blocks_every_dimension(self) -> None:
        report = valid_report()
        report["zero_tolerance_invariants"][0]["passed"] = False

        result = evaluate_report(report)
        self.assertFalse(result["passed"])
        for dimension_id in DIMENSION_IDS:
            dimension = result["dimensions"][dimension_id]
            self.assertFalse(dimension["passed"])
            self.assertTrue(
                any(
                    "zero-tolerance invariant failure" in error
                    for error in dimension["errors"]
                ),
                dimension,
            )

    def test_track_b_passes_at_third_warm_cycle_boundary(self) -> None:
        report = valid_report()
        benchmark = report["innovation_benchmark"]
        benchmark["comparison_claim"] = "approaches_native_ecosystem"
        benchmark["track_b"] = track_b_at_boundaries()
        innovation = report["dimensions"]["innovation_elicitation"]
        innovation["status"] = "Target"
        innovation["reference_stack"] = {
            "name": "project-local native innovation stack",
            "version": "2026.07",
            "components": ["idea portfolio", "project-local lineage memory"],
        }

        result = evaluate_report(report)
        self.assertTrue(result["passed"], result)
        self.assertTrue(
            result["innovation_benchmark"]["reported_track_a_thresholds_passed"]
        )
        self.assertTrue(
            result["innovation_benchmark"]["reported_track_b_thresholds_passed"]
        )
        self.assertFalse(result["innovation_benchmark"]["track_a_passed"])
        self.assertFalse(result["innovation_benchmark"]["track_b_passed"])
        self.assertEqual(
            result["dimensions"]["innovation_elicitation"]["qualification"],
            "target_contract_valid",
        )

        for invalid_count in (2, 4):
            with self.subTest(warm_cycle_count=invalid_count):
                report["innovation_benchmark"]["track_b"][
                    "warm_cycle_count"
                ] = invalid_count
                failed = evaluate_report(report)
                self.assertFalse(failed["passed"])
                self.assertTrue(
                    any(
                        "third-cycle checkpoint" in error
                        for error in failed["innovation_benchmark"]["errors"]
                    )
                )

    def test_track_a_budget_exception_is_computed_from_same_run_pareto_data(self) -> None:
        report = valid_report()
        track_a = report["innovation_benchmark"]["track_a"]
        track_a["token_ratio_vs_evo"] = 1.30
        track_a["cost_ratio_vs_evo"] = 1.20
        common_runs = ["QUERY-001-RUN-1", "QUERY-002-RUN-1"]
        track_a["pareto_observations"] = [
            {
                "system": "scientific-research-skill",
                "run_ids": common_runs,
                "quality_score": 0.90,
                "token_cost": 130.0,
                "monetary_cost": 120.0,
            },
            {
                "system": "evo",
                "run_ids": common_runs,
                "quality_score": 0.80,
                "token_cost": 100.0,
                "monetary_cost": 100.0,
            },
            {
                "system": "ablation",
                "run_ids": common_runs,
                "quality_score": 0.84,
                "token_cost": 110.0,
                "monetary_cost": 90.0,
            },
        ]
        self.assertTrue(evaluate_report(report)["passed"])

        track_a["pareto_observations"][2]["run_ids"] = ["DIFFERENT-RUN"]
        failed = evaluate_report(report)
        self.assertFalse(failed["passed"])
        self.assertTrue(
            any(
                "same frozen runs" in error
                for error in failed["innovation_benchmark"]["errors"]
            )
        )

        extreme = valid_report()
        extreme_track = extreme["innovation_benchmark"]["track_a"]
        extreme_track["token_ratio_vs_evo"] = 100.0
        extreme_track["cost_ratio_vs_evo"] = 100.0
        extreme_track["pareto_observations"] = copy.deepcopy(
            track_a["pareto_observations"]
        )
        extreme_track["pareto_observations"][0]["run_ids"] = common_runs
        extreme_track["pareto_observations"][2]["run_ids"] = common_runs
        extreme_track["pareto_observations"][0]["token_cost"] = 10000.0
        extreme_track["pareto_observations"][0]["monetary_cost"] = 10000.0
        extreme_result = evaluate_report(extreme)
        self.assertFalse(extreme_result["passed"])
        self.assertTrue(
            any(
                "exception cap 2.0" in error
                for error in extreme_result["innovation_benchmark"]["errors"]
            ),
            extreme_result["innovation_benchmark"],
        )

    def test_track_b_rejects_regression_and_cross_workspace_memory(self) -> None:
        violations = {
            "confirmed_dead_end_recurrence_reduction": 0.499999,
            "overall_idea_quality_delta": -0.000001,
            "false_prune_rate": 0.050001,
            "cold_start_baseline_present": False,
            "project_local_same_mainline": False,
            "cross_workspace_memory_used": True,
        }
        for field, failing_value in violations.items():
            with self.subTest(field=field):
                report = valid_report()
                report["innovation_benchmark"]["comparison_claim"] = (
                    "approaches_native_ecosystem"
                )
                report["innovation_benchmark"]["track_b"] = track_b_at_boundaries()
                report["innovation_benchmark"]["track_b"][field] = failing_value
                report["dimensions"]["innovation_elicitation"][
                    "reference_stack"
                ] = {
                    "name": "native stack",
                    "version": "1",
                    "components": ["project-local memory"],
                }
                result = evaluate_report(report)
                self.assertFalse(result["passed"])
                self.assertTrue(
                    any(
                        field in error
                        for error in result["innovation_benchmark"]["errors"]
                    ),
                    result["innovation_benchmark"],
                )

    def test_benchmark_verified_requires_metadata_and_benchmark_evidence(self) -> None:
        missing_metadata = valid_report()
        workflow = missing_metadata["dimensions"]["workflow_governance"]
        workflow["status"] = "Benchmark-verified"
        result = evaluate_report(missing_metadata)
        workflow_result = result["dimensions"]["workflow_governance"]
        self.assertFalse(workflow_result["passed"])
        self.assertTrue(
            any("benchmark_metadata" in error for error in workflow_result["errors"])
        )

        missing_benchmark = valid_report()
        workflow = missing_benchmark["dimensions"]["workflow_governance"]
        workflow["status"] = "Benchmark-verified"
        workflow["benchmark_metadata"] = benchmark_metadata()
        workflow["evidence"]["benchmark"] = []
        result = evaluate_report(missing_benchmark)
        workflow_result = result["dimensions"]["workflow_governance"]
        self.assertFalse(workflow_result["passed"])
        self.assertTrue(
            any(
                "benchmark must contain passed evidence" in error
                for error in workflow_result["errors"]
            )
        )

    def test_declared_current_and_benchmark_statuses_are_not_inferred(self) -> None:
        report = valid_report()
        report["dimensions"]["workflow_governance"]["status"] = "Current"
        audit = report["dimensions"]["project_audit"]
        audit["status"] = "Benchmark-verified"
        audit["benchmark_metadata"] = benchmark_metadata()

        result = evaluate_report(report)
        self.assertFalse(result["passed"], result)
        self.assertTrue(
            any(
                "requires a verified evidence_pack" in error
                for error in result["dimensions"]["workflow_governance"]["errors"]
            )
        )
        self.assertTrue(
            any(
                "requires a verified evidence_pack" in error
                for error in result["dimensions"]["project_audit"]["errors"]
            )
        )
        self.assertEqual(
            result["dimensions"]["experiment_execution"]["declared_status"],
            "Target",
        )

    def test_strict_schema_rejects_unknown_and_missing_fields(self) -> None:
        unknown = valid_report()
        unknown["dimensions"]["workflow_governance"]["score"] = "very high"
        result = evaluate_report(unknown)
        self.assertFalse(result["passed"])
        self.assertTrue(
            any(
                "unknown fields: score" in error
                for error in result["dimensions"]["workflow_governance"]["errors"]
            )
        )

        missing = valid_report()
        del missing["assessment_boundary"]
        result = evaluate_report(missing)
        self.assertFalse(result["passed"])
        self.assertTrue(any("assessment_boundary" in error for error in result["errors"]))

        extra_authority = valid_report()
        extra_authority["dimensions"]["knowledge_management"][
            "human_authority"
        ].append("external_submission")
        result = evaluate_report(extra_authority)
        knowledge = result["dimensions"]["knowledge_management"]
        self.assertFalse(knowledge["passed"])
        self.assertTrue(
            any("unexpected for this boundary" in error for error in knowledge["errors"])
        )

    def test_track_b_data_cannot_expand_a_track_a_core_claim(self) -> None:
        report = valid_report()
        report["innovation_benchmark"]["track_b"] = track_b_at_boundaries()

        result = evaluate_report(report)
        self.assertFalse(result["passed"])
        self.assertFalse(result["innovation_benchmark"]["track_b_passed"])
        self.assertTrue(
            any(
                "track_b must be null" in error
                for error in result["innovation_benchmark"]["errors"]
            )
        )

    def test_loader_rejects_duplicate_keys_nan_and_nonobject_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = {
                "duplicate.json": '{"schema_version":"1.0","schema_version":"1.0"}',
                "nan.json": '{"schema_version": NaN}',
                "array.json": "[]",
            }
            for filename, payload in cases.items():
                with self.subTest(filename=filename):
                    path = root / filename
                    path.write_text(payload, encoding="utf-8")
                    with self.assertRaises(AcceptanceInputError):
                        load_report(path)

    def test_target_pack_is_verified_and_post_manifest_tampering_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = valid_report()
            retained = b'{"schema_version":"1.0","note":"retained"}\n'
            report["evidence_pack"] = write_evidence_pack(
                root,
                {"RETAINED-NOTE": ("application/json", retained)},
            )
            report_path = root / "report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")

            verified = validate_report(report_path)

            self.assertTrue(verified["passed"], verified)
            self.assertTrue(verified["evidence_pack"]["verified"])
            artifact = next((root / "evidence").iterdir())
            artifact.write_bytes(b'{"schema_version":"1.0","note":"tampered"}\n')

            tampered = validate_report(report_path)

            self.assertFalse(tampered["passed"])
            self.assertFalse(tampered["evidence_pack"]["verified"])
            self.assertTrue(
                any("content hash or size does not match" in error for error in tampered["errors"]),
                tampered,
            )

    def test_loaded_pack_rechecks_hash_before_each_reference_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            retained = b'{"schema_version":"1.0","passed":true}\n'
            declaration = write_evidence_pack(
                root,
                {"RETAINED-RESULT": ("application/json", retained)},
            )
            report_path = root / "report.json"
            report_path.write_text("{}", encoding="utf-8")
            pack = EvidencePack.load(report_path, declaration)
            artifact = next((root / "evidence").iterdir())
            artifact.write_bytes(
                b'{"schema_version":"1.0","passed":false}\n'
            )

            with self.assertRaisesRegex(
                EvidencePackError, "no longer matches its retained hash or size"
            ):
                pack.resolve("RETAINED-RESULT", "retained result", json_value=True)

    @unittest.skipIf(sys.platform == "win32", "symlink semantics differ on Windows")
    def test_evidence_pack_rejects_a_symlinked_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = valid_report()
            retained = b'{"schema_version":"1.0","note":"retained"}\n'
            report["evidence_pack"] = write_evidence_pack(
                root,
                {"RETAINED-NOTE": ("application/json", retained)},
            )
            artifact = next((root / "evidence").iterdir())
            backing = root / "backing.json"
            backing.write_bytes(retained)
            artifact.unlink()
            artifact.symlink_to(backing)
            report_path = root / "report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")

            result = validate_report(report_path)

            self.assertFalse(result["passed"])
            self.assertTrue(any("cannot traverse a symlink" in error for error in result["errors"]))

    def test_current_evidence_result_is_derived_from_retained_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = valid_report()
            workflow = report["dimensions"]["workflow_governance"]
            workflow["status"] = "Current"
            item = workflow["evidence"]["deterministic"][0]
            retained_result = {
                "schema_version": "1.0",
                "evidence_id": item["evidence_id"],
                "evidence_class": "deterministic",
                "passed": False,
                "scenario_ids": item["scenario_ids"],
                "finding": item["finding"],
            }
            retained_bytes = (
                json.dumps(retained_result, ensure_ascii=False, sort_keys=True) + "\n"
            ).encode("utf-8")
            item["report_ref"] = "DET-RESULT"
            item["content_hash"] = "sha256:" + hashlib.sha256(retained_bytes).hexdigest()
            report["evidence_pack"] = write_evidence_pack(
                root,
                {"DET-RESULT": ("application/json", retained_bytes)},
            )
            report_path = root / "report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")

            result = validate_report(report_path)

            workflow_result = result["dimensions"]["workflow_governance"]
            self.assertFalse(workflow_result["passed"])
            self.assertTrue(
                any(
                    ".passed does not match retained evidence" in error
                    for error in workflow_result["errors"]
                ),
                workflow_result,
            )

    def test_current_contract_requires_structured_provenance_but_never_self_qualifies(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = valid_report()
            knowledge = report["dimensions"]["knowledge_management"]
            knowledge["status"] = "Current"
            for optional_layer in set(EVIDENCE_LAYERS) - {
                "deterministic",
                "representative",
                "failure_recovery",
            }:
                knowledge["evidence"][optional_layer] = []
            representative_scenarios = sorted(
                REPRESENTATIVE_REQUIRED_CASES["knowledge_management"]
            ) + [
                "checkpoint_after_failure",
                "record_relation_query",
                "historical_revision_lookup",
                "memory_navigation_rebuild",
            ]
            representative = knowledge["evidence"]["representative"][0]
            representative["scenario_ids"] = representative_scenarios
            representative["provenance"]["case_count"] = len(
                representative_scenarios
            )

            artifacts: dict[str, tuple[str, bytes]] = {}
            for invariant in report["zero_tolerance_invariants"]:
                artifact_id = f"INVARIANT-{invariant['invariant_id'].upper()}"
                artifact_id = artifact_id.replace("_", "-")
                invariant["evidence_refs"] = [artifact_id]
                retained = {
                    "schema_version": "1.0",
                    "invariant_id": invariant["invariant_id"],
                    "passed": True,
                    "finding": invariant["finding"],
                }
                artifacts[artifact_id] = (
                    "application/json",
                    (json.dumps(retained, sort_keys=True) + "\n").encode(),
                )

            for layer in ("deterministic", "representative", "failure_recovery"):
                item = knowledge["evidence"][layer][0]
                result_id = f"KNOWLEDGE-{layer.upper().replace('_', '-')}-RESULT"
                item["report_ref"] = result_id
                retained_result = {
                    "schema_version": "1.0",
                    "evidence_id": item["evidence_id"],
                    "evidence_class": layer,
                    "passed": True,
                    "scenario_ids": item["scenario_ids"],
                    "finding": item["finding"],
                }
                result_bytes = (
                    json.dumps(retained_result, sort_keys=True) + "\n"
                ).encode()
                item["content_hash"] = (
                    "sha256:" + hashlib.sha256(result_bytes).hexdigest()
                )
                artifacts[result_id] = ("application/json", result_bytes)
                for field in {
                    "deterministic": ("command_ref",),
                    "representative": ("corpus_ref",),
                    "failure_recovery": (
                        "failure_injection_ref",
                        "recovery_check_ref",
                    ),
                }[layer]:
                    provenance_id = (
                        f"KNOWLEDGE-{layer}-{field}".upper().replace("_", "-")
                    )
                    item["provenance"][field] = provenance_id
                    retained_provenance = {
                        "schema_version": "1.0",
                        "evidence_class": layer,
                        "reference_kind": field,
                        "passed": True,
                        "scenario_ids": item["scenario_ids"],
                        "finding": f"Retained {layer} {field} observation.",
                    }
                    artifacts[provenance_id] = (
                        "application/json",
                        (
                            json.dumps(retained_provenance, sort_keys=True) + "\n"
                        ).encode(),
                    )

            report["evidence_pack"] = write_evidence_pack(root, artifacts)
            report_path = root / "report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")

            result = validate_report(report_path)

            self.assertTrue(result["passed"], result)
            knowledge_result = result["dimensions"]["knowledge_management"]
            self.assertEqual(
                knowledge_result["qualification"],
                "current_retained_evidence_contract_valid",
            )
            self.assertFalse(knowledge_result["capability_qualified"])
            self.assertFalse(result["capability_qualified"])

            command_id = knowledge["evidence"]["deterministic"][0]["provenance"][
                "command_ref"
            ]
            invalid_root = root / "invalid"
            invalid_root.mkdir()
            invalid_artifacts = copy.deepcopy(artifacts)
            invalid_artifacts[command_id] = ("text/plain", b"arbitrary command claim\n")
            report["evidence_pack"] = write_evidence_pack(
                invalid_root, invalid_artifacts
            )
            invalid_report_path = invalid_root / "report.json"
            invalid_report_path.write_text(json.dumps(report), encoding="utf-8")

            invalid = validate_report(invalid_report_path)

            self.assertFalse(invalid["passed"])
            self.assertTrue(
                any(
                    "requires a JSON evidence artifact" in error
                    for error in invalid["dimensions"]["knowledge_management"][
                        "errors"
                    ]
                ),
                invalid,
            )

    def test_innovation_summary_is_recomputed_from_retained_raw_rows(self) -> None:
        raw = raw_track_a_fixture()
        recomputed = recompute_raw_benchmark(raw)
        self.assertEqual(recomputed["track_a"]["novelty_lcb"], 0.0)
        self.assertEqual(recomputed["track_a"]["token_ratio_vs_evo"], 1.0)
        reordered = copy.deepcopy(raw)
        for field in (
            "paired_scores",
            "ranking_observations",
            "cost_observations",
        ):
            reordered["track_a"][field].reverse()
        self.assertEqual(recompute_raw_benchmark(reordered), recomputed)

        unpaired = copy.deepcopy(raw)
        unpaired["track_a"]["paired_scores"].pop()
        with self.assertRaisesRegex(InnovationRawError, "both swapped positions"):
            recompute_raw_benchmark(unpaired)

        unequal_binding = copy.deepcopy(raw)
        unequal_binding["track_a"]["paired_scores"][0]["evo_binding"][
            "primary_model"
        ] = "DIFFERENT-MODEL"
        with self.assertRaisesRegex(InnovationRawError, "share host, model, budgets"):
            recompute_raw_benchmark(unequal_binding)

        posthoc_query = copy.deepcopy(raw)
        posthoc_query["track_a"]["held_out_query_ids"][-1] = "POSTHOC-QUERY"
        with self.assertRaisesRegex(InnovationRawError, "does not bind the frozen query IDs"):
            recompute_raw_benchmark(posthoc_query)

        thin_evidence = copy.deepcopy(raw)
        thin_evidence["track_a"]["query_evidence_packs"][0]["paper_ids"].pop()
        with self.assertRaisesRegex(InnovationRawError, "at least 30 entries"):
            recompute_raw_benchmark(thin_evidence)

        relative_yield = copy.deepcopy(raw)
        relative_yield["track_a"]["candidate_counts"]["evo_valid_diverse"] = 50
        self.assertEqual(
            recompute_raw_benchmark(relative_yield)["track_a"][
                "valid_diverse_yield_ratio"
            ],
            1.8,
        )

        self_reported_quality = copy.deepcopy(raw)
        self_reported_quality["track_a"]["cost_observations"][0][
            "quality_score"
        ] = 1.0
        with self.assertRaisesRegex(InnovationRawError, "must contain exactly"):
            recompute_raw_benchmark(self_reported_quality)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_bytes = (
                json.dumps(raw, ensure_ascii=False, sort_keys=True) + "\n"
            ).encode("utf-8")
            report = valid_report()
            report["innovation_benchmark"]["track_a"] = recomputed["track_a"]
            report["innovation_benchmark"]["raw_observations_ref"] = "RAW-BENCHMARK"
            report["dimensions"]["innovation_elicitation"]["status"] = (
                "Benchmark-verified"
            )
            report["evidence_pack"] = write_evidence_pack(
                root,
                {"RAW-BENCHMARK": ("application/json", raw_bytes)},
            )
            report_path = root / "report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")

            matched = validate_report(report_path)

            self.assertTrue(
                matched["innovation_benchmark"]["raw_observations_verified"],
                matched["innovation_benchmark"],
            )
            self.assertTrue(matched["innovation_benchmark"]["track_a_passed"])

            report["innovation_benchmark"]["track_a"]["novelty_lcb"] = 0.1
            report_path.write_text(json.dumps(report), encoding="utf-8")
            spoofed = validate_report(report_path)

            self.assertFalse(
                spoofed["innovation_benchmark"]["raw_observations_verified"]
            )
            self.assertTrue(
                any(
                    "novelty_lcb does not match recomputed raw observations" in error
                    for error in spoofed["innovation_benchmark"]["errors"]
                ),
                spoofed["innovation_benchmark"],
            )

    def test_track_b_is_recomputed_from_three_project_local_cycles(self) -> None:
        raw = raw_track_a_fixture()
        workspace_id = "WORKSPACE-001"
        mainline_id = "MAINLINE-001"
        empty_hash = "sha256:" + hashlib.sha256(b"").hexdigest()
        parent_hash = empty_hash
        cycles: list[dict[str, Any]] = []
        for cycle in (1, 2, 3):
            snapshot_content = f"project-local-memory-cycle-{cycle}".encode()
            snapshot_hash = "sha256:" + hashlib.sha256(snapshot_content).hexdigest()
            cycles.append(
                {
                    "cycle": cycle,
                    "workspace_id": workspace_id,
                    "mainline_id": mainline_id,
                    "memory_snapshot": {
                        "workspace_id": workspace_id,
                        "mainline_id": mainline_id,
                        "cycle": cycle,
                        "content_hash": snapshot_hash,
                        "size_bytes": len(snapshot_content),
                        "parent_hash": parent_hash,
                    },
                    "observations": [
                        {
                            "query_id": f"TRACK-B-QUERY-{index + 1:03d}",
                            "run_id": f"TRACK-B-RUN-{index + 1:03d}",
                            "dead_end_opportunity_id": (
                                f"DEAD-END-OPPORTUNITY-{index + 1:03d}"
                            ),
                            "cold_dead_end_recurred": index < 10,
                            "warm_dead_end_recurred": index < 5,
                            "cold_quality_score": 0.8,
                            "warm_quality_score": 0.8,
                            "warm_pruned": True,
                            "warm_false_pruned": index == 0,
                        }
                        for index in range(20)
                    ],
                }
            )
            parent_hash = snapshot_hash
        raw["track_b"] = {
            "workspace_id": workspace_id,
            "mainline_id": mainline_id,
            "cold_memory_snapshot": {
                "workspace_id": workspace_id,
                "mainline_id": mainline_id,
                "cycle": 0,
                "content_hash": empty_hash,
                "size_bytes": 0,
                "parent_hash": None,
            },
            "cycles": cycles,
        }

        recomputed = recompute_raw_benchmark(raw)

        self.assertEqual(
            recomputed["track_b"],
            {
                "warm_cycle_count": 3,
                "cold_start_baseline_present": True,
                "project_local_same_mainline": True,
                "cross_workspace_memory_used": False,
                "confirmed_dead_end_recurrence_reduction": 0.5,
                "overall_idea_quality_delta": 0.0,
                "false_prune_rate": 0.05,
            },
        )
        missing_cycle = copy.deepcopy(raw)
        missing_cycle["track_b"]["cycles"].pop()
        with self.assertRaisesRegex(InnovationRawError, "exactly three warm cycles"):
            recompute_raw_benchmark(missing_cycle)

        cross_workspace = copy.deepcopy(raw)
        cross_workspace["track_b"]["cycles"][1]["workspace_id"] = "OTHER-WORKSPACE"
        with self.assertRaisesRegex(InnovationRawError, "leaves the frozen project"):
            recompute_raw_benchmark(cross_workspace)

        third_cycle_regression = copy.deepcopy(raw)
        for observation in third_cycle_regression["track_b"]["cycles"][2][
            "observations"
        ]:
            observation["warm_dead_end_recurred"] = observation[
                "cold_dead_end_recurred"
            ]
            observation["warm_quality_score"] = 0.3
        regressed = recompute_raw_benchmark(third_cycle_regression)["track_b"]
        self.assertEqual(
            regressed["confirmed_dead_end_recurrence_reduction"], 0.0
        )
        self.assertAlmostEqual(regressed["overall_idea_quality_delta"], -0.5)

        reordered = copy.deepcopy(raw)
        reordered["track_b"]["cycles"][0], reordered["track_b"]["cycles"][1] = (
            reordered["track_b"]["cycles"][1],
            reordered["track_b"]["cycles"][0],
        )
        with self.assertRaisesRegex(InnovationRawError, "chronological order"):
            recompute_raw_benchmark(reordered)

    def test_raw_budget_exception_quality_is_derived_from_blinded_same_run_ratings(
        self,
    ) -> None:
        raw = raw_track_a_fixture()
        track = raw["track_a"]
        binding = copy.deepcopy(track["paired_scores"][0]["scientific_binding"])
        score = {
            "novelty": 0.8,
            "feasibility": 0.8,
            "relevance": 0.8,
            "clarity": 0.8,
        }
        run_ids = sorted(
            {
                observation["run_id"]
                for observation in track["cost_observations"]
            }
        )
        for observation in track["cost_observations"]:
            if observation["system"] == "scientific-research-skill":
                observation["token_cost"] = 200.0
                observation["monetary_cost"] = 2.0
        track["cost_observations"].extend(
            {
                "run_id": run_id,
                "system": "third-system",
                "token_cost": 110.0,
                "monetary_cost": 1.1,
            }
            for run_id in run_ids
        )
        track["pareto_quality_observations"] = [
            {
                "run_id": run_id,
                "system": "third-system",
                "reviewer_id": reviewer_id,
                "position": position,
                "reviewer_blinded": True,
                "system_binding": copy.deepcopy(binding),
                "evo_binding": copy.deepcopy(binding),
                "system_score": copy.deepcopy(score),
                "evo_score": copy.deepcopy(score),
            }
            for run_id in run_ids
            for reviewer_id in ("REVIEWER-001", "REVIEWER-002")
            for position in ("system_left", "evo_left")
        ]

        recomputed = recompute_raw_benchmark(raw)["track_a"]

        self.assertEqual(recomputed["token_ratio_vs_evo"], 2.0)
        self.assertEqual(recomputed["cost_ratio_vs_evo"], 2.0)
        self.assertEqual(
            {item["system"] for item in recomputed["pareto_observations"]},
            {"scientific-research-skill", "evo", "third-system"},
        )
        self.assertTrue(
            all(
                item["quality_score"] == 0.8
                for item in recomputed["pareto_observations"]
            )
        )

        missing_position = copy.deepcopy(raw)
        missing_position["track_a"]["pareto_quality_observations"].pop()
        with self.assertRaisesRegex(InnovationRawError, "both swapped positions"):
            recompute_raw_benchmark(missing_position)

    def test_cli_exit_codes_and_versioned_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            passing_path = root / "passing.json"
            passing_path.write_text(json.dumps(valid_report()), encoding="utf-8")
            passing = self.run_cli(passing_path)
            self.assertEqual(passing.returncode, 0, passing.stderr)
            passing_output = json.loads(passing.stdout)
            self.assertEqual(passing_output["status"], "pass")
            self.assertEqual(
                passing_output["result_schema_version"], RESULT_SCHEMA_VERSION
            )

            failing_report = valid_report()
            failing_report["innovation_benchmark"]["track_a"]["flaw_recall"] = 0.1
            failing_path = root / "failing.json"
            failing_path.write_text(json.dumps(failing_report), encoding="utf-8")
            failing = self.run_cli(failing_path)
            self.assertEqual(failing.returncode, 1, failing.stderr)
            self.assertEqual(json.loads(failing.stdout)["status"], "fail")

            malformed_path = root / "malformed.json"
            malformed_path.write_text("{", encoding="utf-8")
            malformed = self.run_cli(malformed_path)
            self.assertEqual(malformed.returncode, 2, malformed.stderr)
            malformed_output = json.loads(malformed.stdout)
            self.assertEqual(malformed_output["status"], "input_error")
            self.assertEqual(
                malformed_output["result_schema_version"], RESULT_SCHEMA_VERSION
            )

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO creation is POSIX-only")
    def test_cli_rejects_a_fifo_report_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / "report.fifo"
            os.mkfifo(fifo)

            result = subprocess.run(
                [sys.executable, str(CLI), str(fifo)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "input_error")
        self.assertIn("regular file", json.loads(result.stdout)["errors"][0])

    def run_cli(self, path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), str(path)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
