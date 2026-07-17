"""Deterministically recompute innovation benchmark summaries from raw rows."""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from collections import defaultdict
from typing import Any


RAW_SCHEMA_VERSION = "1.0"
SCORE_FIELDS = {"novelty", "feasibility", "relevance", "clarity"}
BINDING_FIELDS = {
    "host_id",
    "primary_model",
    "token_budget",
    "tool_budget",
    "worker_limit",
    "resource_constraints_hash",
    "host_memory_empty",
}
PAIR_FIELDS = {
    "query_id",
    "run_id",
    "discipline",
    "reviewer_id",
    "position",
    "reviewer_blinded",
    "closest_prior_work_verified",
    "scientific_binding",
    "evo_binding",
    "scientific",
    "evo",
}
CANDIDATE_COUNT_FIELDS = {
    "valid_diverse",
    "total_candidates",
    "evo_valid_diverse",
    "evo_total_candidates",
    "duplicates",
    "false_novelty",
    "novelty_predictions",
    "evo_false_novelty",
    "evo_novelty_predictions",
    "flaw_true_positive",
    "flaw_false_positive",
    "flaw_false_negative",
    "repairs_succeeded",
    "repairs_attempted",
    "false_pruned",
    "pruned_total",
    "top1_in_expert_top3",
    "ranking_queries",
}
RANKING_FIELDS = {"query_id", "normalized_regret", "kendall_tau"}
COST_FIELDS = {
    "run_id",
    "system",
    "token_cost",
    "monetary_cost",
}
QUERY_EVIDENCE_PACK_FIELDS = {"query_id", "paper_ids"}
PARETO_RATING_FIELDS = {
    "run_id",
    "system",
    "reviewer_id",
    "position",
    "reviewer_blinded",
    "system_binding",
    "evo_binding",
    "system_score",
    "evo_score",
}
TRACK_A_RAW_FIELDS = {
    "held_out_query_ids",
    "preregistration_hash",
    "query_evidence_packs",
    "calibration_query_ids",
    "adversarial_control_ids",
    "paired_scores",
    "candidate_counts",
    "ranking_observations",
    "cost_observations",
    "pareto_quality_observations",
}
TRACK_B_RAW_FIELDS = {
    "workspace_id",
    "mainline_id",
    "cold_memory_snapshot",
    "cycles",
}
TRACK_B_CYCLE_FIELDS = {
    "cycle",
    "workspace_id",
    "mainline_id",
    "memory_snapshot",
    "observations",
}
MEMORY_SNAPSHOT_FIELDS = {
    "workspace_id",
    "mainline_id",
    "cycle",
    "content_hash",
    "size_bytes",
    "parent_hash",
}
TRACK_B_OBSERVATION_FIELDS = {
    "query_id",
    "run_id",
    "dead_end_opportunity_id",
    "cold_dead_end_recurred",
    "warm_dead_end_recurred",
    "cold_quality_score",
    "warm_quality_score",
    "warm_pruned",
    "warm_false_pruned",
}
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class InnovationRawError(ValueError):
    """Raw benchmark rows cannot support deterministic recomputation."""


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise InnovationRawError(f"{label} must contain exactly {', '.join(sorted(fields))}")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InnovationRawError(f"{label} must be a non-empty string")
    return value


def _identifiers(value: Any, label: str, *, minimum: int) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise InnovationRawError(f"{label} must be a list of non-empty strings")
    if len(value) != len(set(value)):
        raise InnovationRawError(f"{label} must not contain duplicates")
    if len(value) < minimum:
        raise InnovationRawError(f"{label} must contain at least {minimum} entries")
    return value


def _number(
    value: Any,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InnovationRawError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise InnovationRawError(f"{label} must be a finite number")
    if minimum is not None and number < minimum:
        raise InnovationRawError(f"{label} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise InnovationRawError(f"{label} must be at most {maximum}")
    return number


def _count(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise InnovationRawError(f"{label} must be a non-negative integer")
    return value


def _rate(numerator: int, denominator: int, label: str) -> float:
    if denominator <= 0:
        raise InnovationRawError(f"{label} denominator must be greater than zero")
    if numerator > denominator:
        raise InnovationRawError(f"{label} numerator cannot exceed its denominator")
    return numerator / denominator


def _bootstrap_lcb(query_values: list[float]) -> float:
    """Return a deterministic one-sided 95% query-cluster bootstrap LCB."""

    if len(query_values) < 20:
        raise InnovationRawError("paired_scores must retain at least 20 held-out queries")
    generator = random.Random(0)
    count = len(query_values)
    bootstrap_means = [
        math.fsum(
            query_values[generator.randrange(count)] for _ in range(count)
        )
        / count
        for _ in range(10_000)
    ]
    bootstrap_means.sort()
    return bootstrap_means[int(0.05 * (len(bootstrap_means) - 1))]


def _score(value: Any, label: str) -> dict[str, float]:
    score = _exact(value, SCORE_FIELDS, label)
    return {
        field: _number(score.get(field), f"{label}.{field}", minimum=0.0, maximum=1.0)
        for field in sorted(SCORE_FIELDS)
    }


def _binding(value: Any, label: str) -> dict[str, Any]:
    binding = _exact(value, BINDING_FIELDS, label)
    normalized = {
        "host_id": _identifier(binding.get("host_id"), f"{label}.host_id"),
        "primary_model": _identifier(
            binding.get("primary_model"), f"{label}.primary_model"
        ),
        "token_budget": _number(
            binding.get("token_budget"), f"{label}.token_budget", minimum=1.0
        ),
        "tool_budget": _count(
            binding.get("tool_budget"), f"{label}.tool_budget"
        ),
        "worker_limit": _count(
            binding.get("worker_limit"), f"{label}.worker_limit"
        ),
        "resource_constraints_hash": _identifier(
            binding.get("resource_constraints_hash"),
            f"{label}.resource_constraints_hash",
        ),
        "host_memory_empty": binding.get("host_memory_empty"),
    }
    if normalized["tool_budget"] <= 0 or normalized["worker_limit"] <= 0:
        raise InnovationRawError(f"{label} tool budget and worker limit must be positive")
    if normalized["host_memory_empty"] is not True:
        raise InnovationRawError(f"{label}.host_memory_empty must be true for Track A")
    return normalized


def _expected_preregistration_hash(
    held_out_query_ids: list[str], calibration_query_ids: list[str]
) -> str:
    content = json.dumps(
        {
            "calibration_query_ids": sorted(calibration_query_ids),
            "held_out_query_ids": sorted(held_out_query_ids),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _track_a(value: Any) -> dict[str, Any]:
    track = _exact(value, TRACK_A_RAW_FIELDS, "raw.track_a")
    held_out = _identifiers(
        track.get("held_out_query_ids"),
        "raw.track_a.held_out_query_ids",
        minimum=20,
    )
    calibration = _identifiers(
        track.get("calibration_query_ids"),
        "raw.track_a.calibration_query_ids",
        minimum=30,
    )
    if len(calibration) != 30:
        raise InnovationRawError(
            "raw.track_a.calibration_query_ids must contain exactly 30 entries"
        )
    if set(calibration) & set(held_out):
        raise InnovationRawError(
            "calibration queries must be disjoint from preregistered held-out queries"
        )
    preregistration_hash = track.get("preregistration_hash")
    if not isinstance(preregistration_hash, str) or SHA256_PATTERN.fullmatch(
        preregistration_hash
    ) is None:
        raise InnovationRawError(
            "raw.track_a.preregistration_hash must be sha256:<64 lowercase hex>"
        )
    if preregistration_hash != _expected_preregistration_hash(held_out, calibration):
        raise InnovationRawError(
            "raw.track_a.preregistration_hash does not bind the frozen query IDs"
        )
    packs = track.get("query_evidence_packs")
    if not isinstance(packs, list):
        raise InnovationRawError("raw.track_a.query_evidence_packs must be a list")
    pack_queries: set[str] = set()
    for index, raw in enumerate(packs):
        label = f"raw.track_a.query_evidence_packs[{index}]"
        pack = _exact(raw, QUERY_EVIDENCE_PACK_FIELDS, label)
        query_id = _identifier(pack.get("query_id"), f"{label}.query_id")
        if query_id in pack_queries:
            raise InnovationRawError(f"{label}.query_id is duplicated")
        pack_queries.add(query_id)
        papers = _identifiers(pack.get("paper_ids"), f"{label}.paper_ids", minimum=30)
        if len(papers) > 50:
            raise InnovationRawError(f"{label}.paper_ids must contain at most 50 entries")
    if pack_queries != set(held_out):
        raise InnovationRawError(
            "query_evidence_packs must cover every preregistered held-out query exactly"
        )
    _identifiers(
        track.get("adversarial_control_ids"),
        "raw.track_a.adversarial_control_ids",
        minimum=15,
    )

    pairs = track.get("paired_scores")
    if not isinstance(pairs, list) or not pairs:
        raise InnovationRawError("raw.track_a.paired_scores must be non-empty")
    query_deltas: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    query_runs: dict[str, set[str]] = defaultdict(set)
    query_disciplines: dict[str, str] = {}
    run_queries: dict[str, str] = {}
    run_ids: set[str] = set()
    reviewers: set[str] = set()
    position_pairs: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    observation_keys: set[tuple[str, str, str, str]] = set()
    run_quality: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    common_binding: dict[str, Any] | None = None
    for index, raw in enumerate(pairs):
        label = f"raw.track_a.paired_scores[{index}]"
        pair = _exact(raw, PAIR_FIELDS, label)
        query_id = _identifier(pair.get("query_id"), f"{label}.query_id")
        run_id = _identifier(pair.get("run_id"), f"{label}.run_id")
        discipline = _identifier(pair.get("discipline"), f"{label}.discipline")
        reviewer_id = _identifier(pair.get("reviewer_id"), f"{label}.reviewer_id")
        if query_id not in set(held_out):
            raise InnovationRawError(f"{label}.query_id was not preregistered")
        prior_discipline = query_disciplines.setdefault(query_id, discipline)
        if prior_discipline != discipline:
            raise InnovationRawError(f"held-out query {query_id!r} changes discipline")
        prior_query = run_queries.setdefault(run_id, query_id)
        if prior_query != query_id:
            raise InnovationRawError(f"run_id {run_id!r} is reused across queries")
        position = pair.get("position")
        if position not in {"scientific_left", "evo_left"}:
            raise InnovationRawError(
                f"{label}.position must be scientific_left or evo_left"
            )
        if pair.get("reviewer_blinded") is not True:
            raise InnovationRawError(f"{label}.reviewer_blinded must be true")
        if pair.get("closest_prior_work_verified") is not True:
            raise InnovationRawError(
                f"{label}.closest_prior_work_verified must be true"
            )
        observation_key = (query_id, run_id, reviewer_id, position)
        if observation_key in observation_keys:
            raise InnovationRawError(f"{label} duplicates a reviewer/position rating")
        observation_keys.add(observation_key)
        position_pairs[(query_id, run_id, reviewer_id)].add(position)
        reviewers.add(reviewer_id)
        run_ids.add(run_id)
        query_runs[query_id].add(run_id)
        scientific_binding = _binding(
            pair.get("scientific_binding"), f"{label}.scientific_binding"
        )
        evo_binding = _binding(pair.get("evo_binding"), f"{label}.evo_binding")
        if scientific_binding != evo_binding:
            raise InnovationRawError(
                f"{label} systems must share host, model, budgets, workers, and resources"
            )
        if common_binding is None:
            common_binding = scientific_binding
        elif scientific_binding != common_binding:
            raise InnovationRawError(
                f"{label} does not use the frozen common Track A binding"
            )
        scientific_score = _score(pair.get("scientific"), f"{label}.scientific")
        evo_score = _score(pair.get("evo"), f"{label}.evo")
        for field in SCORE_FIELDS:
            query_deltas[query_id][field].append(
                scientific_score[field] - evo_score[field]
            )
        query_deltas[query_id]["composite"].append(
            math.fsum(
                scientific_score[field] - evo_score[field]
                for field in SCORE_FIELDS
            )
            / len(SCORE_FIELDS)
        )
        run_quality["scientific-research-skill"][run_id].append(
            math.fsum(scientific_score.values()) / len(SCORE_FIELDS)
        )
        run_quality["evo"][run_id].append(
            math.fsum(evo_score.values()) / len(SCORE_FIELDS)
        )
    if set(query_deltas) != set(held_out):
        raise InnovationRawError(
            "paired_scores must cover every preregistered held-out query exactly"
        )
    if len(set(query_disciplines.values())) < 4:
        raise InnovationRawError("raw.track_a.paired_scores needs at least four disciplines")
    if len(reviewers) < 2:
        raise InnovationRawError(
            "raw.track_a.paired_scores needs at least two blinded reviewers"
        )
    for pair_key, positions in position_pairs.items():
        if positions != {"scientific_left", "evo_left"}:
            raise InnovationRawError(
                "every query/run/reviewer rating must retain both swapped positions: "
                + "/".join(pair_key)
            )
    for query_id, observed_runs in query_runs.items():
        if len(observed_runs) != 3:
            raise InnovationRawError(
                f"held-out query {query_id!r} must retain exactly three runs"
            )
    dimension_lcbs = {
        field: _bootstrap_lcb(
            [
                math.fsum(query_deltas[query_id][field])
                / len(query_deltas[query_id][field])
                for query_id in sorted(query_deltas)
            ]
        )
        for field in ("novelty", "feasibility", "relevance", "clarity")
    }
    composite_lcb = _bootstrap_lcb(
        [
            math.fsum(query_deltas[query_id]["composite"])
            / len(query_deltas[query_id]["composite"])
            for query_id in sorted(query_deltas)
        ]
    )

    raw_counts = _exact(
        track.get("candidate_counts"),
        CANDIDATE_COUNT_FIELDS,
        "raw.track_a.candidate_counts",
    )
    counts = {
        field: _count(raw_counts.get(field), f"raw.track_a.candidate_counts.{field}")
        for field in CANDIDATE_COUNT_FIELDS
    }
    scientific_yield = _rate(
        counts["valid_diverse"], counts["total_candidates"], "valid-diverse yield"
    )
    evo_yield = _rate(
        counts["evo_valid_diverse"],
        counts["evo_total_candidates"],
        "Evo valid-diverse yield",
    )
    if evo_yield <= 0:
        raise InnovationRawError("Evo valid-diverse yield must be greater than zero")
    valid_diverse_yield_ratio = scientific_yield / evo_yield
    duplicate_rate = _rate(
        counts["duplicates"], counts["total_candidates"], "duplicate rate"
    )
    false_novelty_rate = _rate(
        counts["false_novelty"], counts["novelty_predictions"], "false novelty rate"
    )
    evo_false_novelty_rate = _rate(
        counts["evo_false_novelty"],
        counts["evo_novelty_predictions"],
        "evo false novelty rate",
    )
    flaw_recall = _rate(
        counts["flaw_true_positive"],
        counts["flaw_true_positive"] + counts["flaw_false_negative"],
        "flaw recall",
    )
    flaw_precision = _rate(
        counts["flaw_true_positive"],
        counts["flaw_true_positive"] + counts["flaw_false_positive"],
        "flaw precision",
    )
    repair_success = _rate(
        counts["repairs_succeeded"], counts["repairs_attempted"], "repair success"
    )
    false_prune_rate = _rate(
        counts["false_pruned"], counts["pruned_total"], "false prune rate"
    )
    top1_rate = _rate(
        counts["top1_in_expert_top3"],
        counts["ranking_queries"],
        "top-1 in expert top-3 rate",
    )

    rankings = track.get("ranking_observations")
    if not isinstance(rankings, list) or not rankings:
        raise InnovationRawError("raw.track_a.ranking_observations must be non-empty")
    ranking_queries: set[str] = set()
    regrets: list[float] = []
    taus: list[float] = []
    for index, raw in enumerate(rankings):
        label = f"raw.track_a.ranking_observations[{index}]"
        item = _exact(raw, RANKING_FIELDS, label)
        query_id = _identifier(item.get("query_id"), f"{label}.query_id")
        if query_id in ranking_queries:
            raise InnovationRawError(f"{label}.query_id is duplicated")
        ranking_queries.add(query_id)
        regrets.append(
            _number(
                item.get("normalized_regret"),
                f"{label}.normalized_regret",
                minimum=0.0,
                maximum=1.0,
            )
        )
        taus.append(
            _number(
                item.get("kendall_tau"),
                f"{label}.kendall_tau",
                minimum=-1.0,
                maximum=1.0,
            )
        )
    if ranking_queries != set(held_out):
        raise InnovationRawError(
            "ranking observations must cover every held-out query exactly"
        )
    if counts["ranking_queries"] != len(ranking_queries):
        raise InnovationRawError(
            "candidate_counts.ranking_queries does not match raw rankings"
        )

    costs = track.get("cost_observations")
    if not isinstance(costs, list) or not costs:
        raise InnovationRawError("raw.track_a.cost_observations must be non-empty")
    systems: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cost_keys: set[tuple[str, str]] = set()
    for index, raw in enumerate(costs):
        label = f"raw.track_a.cost_observations[{index}]"
        item = _exact(raw, COST_FIELDS, label)
        run_id = _identifier(item.get("run_id"), f"{label}.run_id")
        system = _identifier(item.get("system"), f"{label}.system")
        key = (system, run_id)
        if key in cost_keys:
            raise InnovationRawError(f"{label} duplicates a system/run observation")
        cost_keys.add(key)
        systems[system].append(
            {
                "run_id": run_id,
                "token_cost": _number(
                    item.get("token_cost"), f"{label}.token_cost", minimum=0.0
                ),
                "monetary_cost": _number(
                    item.get("monetary_cost"),
                    f"{label}.monetary_cost",
                    minimum=0.0,
                ),
            }
        )
    required_systems = {"scientific-research-skill", "evo"}
    if not required_systems <= set(systems):
        raise InnovationRawError(
            "cost observations must include scientific-research-skill and evo"
        )
    for system, observations in systems.items():
        observed = {item["run_id"] for item in observations}
        if observed != run_ids:
            raise InnovationRawError(
                f"cost observations for {system} must cover the exact paired runs"
            )
    cost_totals = {
        system: {
            "token_cost": math.fsum(item["token_cost"] for item in observations),
            "monetary_cost": math.fsum(
                item["monetary_cost"] for item in observations
            ),
        }
        for system, observations in systems.items()
    }
    evo_cost = cost_totals["evo"]
    if evo_cost["token_cost"] <= 0 or evo_cost["monetary_cost"] <= 0:
        raise InnovationRawError("evo cost denominators must be greater than zero")
    scientific_cost = cost_totals["scientific-research-skill"]
    token_ratio = scientific_cost["token_cost"] / evo_cost["token_cost"]
    monetary_ratio = (
        scientific_cost["monetary_cost"] / evo_cost["monetary_cost"]
    )
    exception_required = token_ratio > 1.25 or monetary_ratio > 1.25
    third_systems = set(systems) - required_systems
    if exception_required and not third_systems:
        raise InnovationRawError("a budget exception requires a third same-run system")
    if not exception_required and third_systems:
        raise InnovationRawError(
            "third-system observations are only allowed for a budget exception"
        )

    raw_pareto = track.get("pareto_quality_observations")
    if not isinstance(raw_pareto, list):
        raise InnovationRawError(
            "raw.track_a.pareto_quality_observations must be a list"
        )
    pareto_positions: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    pareto_reviewers: dict[str, set[str]] = defaultdict(set)
    pareto_runs: dict[str, set[str]] = defaultdict(set)
    pareto_keys: set[tuple[str, str, str, str]] = set()
    third_quality: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for index, raw in enumerate(raw_pareto):
        label = f"raw.track_a.pareto_quality_observations[{index}]"
        item = _exact(raw, PARETO_RATING_FIELDS, label)
        run_id = _identifier(item.get("run_id"), f"{label}.run_id")
        system = _identifier(item.get("system"), f"{label}.system")
        reviewer_id = _identifier(item.get("reviewer_id"), f"{label}.reviewer_id")
        if system not in third_systems:
            raise InnovationRawError(f"{label}.system has no same-run cost observation")
        if run_id not in run_ids:
            raise InnovationRawError(f"{label}.run_id is outside the frozen paired runs")
        position = item.get("position")
        if position not in {"system_left", "evo_left"}:
            raise InnovationRawError(
                f"{label}.position must be system_left or evo_left"
            )
        if item.get("reviewer_blinded") is not True:
            raise InnovationRawError(f"{label}.reviewer_blinded must be true")
        system_binding = _binding(
            item.get("system_binding"), f"{label}.system_binding"
        )
        evo_binding = _binding(item.get("evo_binding"), f"{label}.evo_binding")
        if system_binding != evo_binding or system_binding != common_binding:
            raise InnovationRawError(
                f"{label} must use the frozen common Track A binding"
            )
        key = (system, run_id, reviewer_id, position)
        if key in pareto_keys:
            raise InnovationRawError(f"{label} duplicates a reviewer/position rating")
        pareto_keys.add(key)
        pareto_positions[(system, run_id, reviewer_id)].add(position)
        pareto_reviewers[system].add(reviewer_id)
        pareto_runs[system].add(run_id)
        system_score = _score(item.get("system_score"), f"{label}.system_score")
        _score(item.get("evo_score"), f"{label}.evo_score")
        third_quality[system][run_id].append(
            math.fsum(system_score.values()) / len(SCORE_FIELDS)
        )
    if not third_systems and raw_pareto:
        raise InnovationRawError(
            "pareto quality observations require a third same-run system"
        )
    for system in third_systems:
        if pareto_runs[system] != run_ids:
            raise InnovationRawError(
                f"Pareto ratings for {system} must cover the exact paired runs"
            )
        if len(pareto_reviewers[system]) < 2:
            raise InnovationRawError(
                f"Pareto ratings for {system} need at least two blinded reviewers"
            )
    for key, positions in pareto_positions.items():
        if positions != {"system_left", "evo_left"}:
            raise InnovationRawError(
                "every Pareto run/reviewer rating must retain both swapped positions: "
                + "/".join(key)
            )

    grouped: list[dict[str, Any]] = []
    for system in sorted(systems):
        quality_rows = (
            run_quality[system]
            if system in required_systems
            else third_quality[system]
        )
        if set(quality_rows) != run_ids:
            raise InnovationRawError(
                f"expert quality ratings for {system} must cover every frozen run"
            )
        per_run_quality = [
            math.fsum(quality_rows[run_id]) / len(quality_rows[run_id])
            for run_id in sorted(run_ids)
        ]
        grouped.append(
            {
                "system": system,
                "run_ids": sorted(run_ids),
                "quality_score": math.fsum(per_run_quality) / len(per_run_quality),
                "token_cost": cost_totals[system]["token_cost"],
                "monetary_cost": cost_totals[system]["monetary_cost"],
            }
        )

    return {
        "novelty_lcb": dimension_lcbs["novelty"],
        "composite_lcb": composite_lcb,
        "dimension_lcbs": dimension_lcbs,
        "valid_diverse_yield_ratio": valid_diverse_yield_ratio,
        "duplicate_rate": duplicate_rate,
        "false_novelty_rate": false_novelty_rate,
        "false_novelty_delta_percentage_points": (
            false_novelty_rate - evo_false_novelty_rate
        )
        * 100.0,
        "flaw_recall": flaw_recall,
        "flaw_precision": flaw_precision,
        "repair_success": repair_success,
        "false_prune_rate": false_prune_rate,
        "top1_in_expert_top3_rate": top1_rate,
        "normalized_regret": math.fsum(regrets) / len(regrets),
        "kendall_tau": math.fsum(taus) / len(taus),
        "token_ratio_vs_evo": token_ratio,
        "cost_ratio_vs_evo": monetary_ratio,
        "pareto_observations": grouped if exception_required else None,
    }


def _memory_snapshot(
    value: Any,
    label: str,
    *,
    workspace_id: str,
    mainline_id: str,
    cycle: int,
    parent_hash: str | None,
) -> dict[str, Any]:
    snapshot = _exact(value, MEMORY_SNAPSHOT_FIELDS, label)
    if snapshot.get("workspace_id") != workspace_id:
        raise InnovationRawError(f"{label}.workspace_id leaves the frozen project")
    if snapshot.get("mainline_id") != mainline_id:
        raise InnovationRawError(f"{label}.mainline_id leaves the frozen mainline")
    if snapshot.get("cycle") != cycle:
        raise InnovationRawError(f"{label}.cycle must be {cycle}")
    content_hash = snapshot.get("content_hash")
    if not isinstance(content_hash, str) or SHA256_PATTERN.fullmatch(content_hash) is None:
        raise InnovationRawError(f"{label}.content_hash must be sha256:<64 lowercase hex>")
    size_bytes = _count(snapshot.get("size_bytes"), f"{label}.size_bytes")
    if snapshot.get("parent_hash") != parent_hash:
        raise InnovationRawError(f"{label}.parent_hash does not continue the local lineage")
    return {"content_hash": content_hash, "size_bytes": size_bytes}


def _track_b(value: Any) -> dict[str, Any]:
    track = _exact(value, TRACK_B_RAW_FIELDS, "raw.track_b")
    workspace_id = _identifier(track.get("workspace_id"), "raw.track_b.workspace_id")
    mainline_id = _identifier(track.get("mainline_id"), "raw.track_b.mainline_id")
    empty_hash = f"sha256:{hashlib.sha256(b'').hexdigest()}"
    cold_snapshot = _memory_snapshot(
        track.get("cold_memory_snapshot"),
        "raw.track_b.cold_memory_snapshot",
        workspace_id=workspace_id,
        mainline_id=mainline_id,
        cycle=0,
        parent_hash=None,
    )
    if cold_snapshot != {"content_hash": empty_hash, "size_bytes": 0}:
        raise InnovationRawError(
            "raw.track_b.cold_memory_snapshot must be the empty cold-start snapshot"
        )
    cycles = track.get("cycles")
    if not isinstance(cycles, list) or len(cycles) != 3:
        raise InnovationRawError("raw.track_b.cycles must retain exactly three warm cycles")
    parent_hash = empty_hash
    snapshot_hashes = {empty_hash}
    expected_keys: set[tuple[str, str, str]] | None = None
    cold_baseline: dict[tuple[str, str, str], tuple[bool, float]] | None = None
    cycle_metrics: dict[int, dict[str, Any]] = {}
    for index, raw in enumerate(cycles):
        label = f"raw.track_b.cycles[{index}]"
        item = _exact(raw, TRACK_B_CYCLE_FIELDS, label)
        cycle = item.get("cycle")
        if type(cycle) is not int or cycle != index + 1:
            raise InnovationRawError(
                f"{label}.cycle must preserve chronological order 1, 2, 3"
            )
        if item.get("workspace_id") != workspace_id:
            raise InnovationRawError(f"{label}.workspace_id leaves the frozen project")
        if item.get("mainline_id") != mainline_id:
            raise InnovationRawError(f"{label}.mainline_id leaves the frozen mainline")
        snapshot = _memory_snapshot(
            item.get("memory_snapshot"),
            f"{label}.memory_snapshot",
            workspace_id=workspace_id,
            mainline_id=mainline_id,
            cycle=cycle,
            parent_hash=parent_hash,
        )
        if snapshot["size_bytes"] <= 0 or snapshot["content_hash"] in snapshot_hashes:
            raise InnovationRawError(
                f"{label}.memory_snapshot must retain a new non-empty local revision"
            )
        snapshot_hashes.add(snapshot["content_hash"])
        parent_hash = snapshot["content_hash"]
        observations = item.get("observations")
        if not isinstance(observations, list) or len(observations) < 20:
            raise InnovationRawError(
                f"{label}.observations must retain at least 20 matched rows"
            )
        cycle_keys: set[tuple[str, str, str]] = set()
        cycle_cold: dict[tuple[str, str, str], tuple[bool, float]] = {}
        cycle_baseline = 0
        cycle_warm = 0
        cycle_pruned_total = 0
        cycle_false_pruned = 0
        cycle_quality_deltas: list[float] = []
        for observation_index, raw_observation in enumerate(observations):
            observation_label = f"{label}.observations[{observation_index}]"
            observation = _exact(
                raw_observation, TRACK_B_OBSERVATION_FIELDS, observation_label
            )
            key = (
                _identifier(
                    observation.get("query_id"), f"{observation_label}.query_id"
                ),
                _identifier(
                    observation.get("run_id"), f"{observation_label}.run_id"
                ),
                _identifier(
                    observation.get("dead_end_opportunity_id"),
                    f"{observation_label}.dead_end_opportunity_id",
                ),
            )
            if key in cycle_keys:
                raise InnovationRawError(f"{observation_label} duplicates a matched row")
            cycle_keys.add(key)
            cold_recurred = observation.get("cold_dead_end_recurred")
            warm_recurred = observation.get("warm_dead_end_recurred")
            warm_pruned = observation.get("warm_pruned")
            warm_false_pruned = observation.get("warm_false_pruned")
            if not all(
                isinstance(flag, bool)
                for flag in (
                    cold_recurred,
                    warm_recurred,
                    warm_pruned,
                    warm_false_pruned,
                )
            ):
                raise InnovationRawError(
                    f"{observation_label} recurrence and pruning fields must be booleans"
                )
            if warm_false_pruned and not warm_pruned:
                raise InnovationRawError(
                    f"{observation_label}.warm_false_pruned requires warm_pruned"
                )
            cold_quality = _number(
                observation.get("cold_quality_score"),
                f"{observation_label}.cold_quality_score",
                minimum=0.0,
                maximum=1.0,
            )
            warm_quality = _number(
                observation.get("warm_quality_score"),
                f"{observation_label}.warm_quality_score",
                minimum=0.0,
                maximum=1.0,
            )
            cycle_cold[key] = (cold_recurred, cold_quality)
            cycle_baseline += int(cold_recurred)
            cycle_warm += int(warm_recurred)
            cycle_pruned_total += int(warm_pruned)
            cycle_false_pruned += int(warm_false_pruned)
            cycle_quality_deltas.append(warm_quality - cold_quality)
        if expected_keys is None:
            expected_keys = cycle_keys
            cold_baseline = cycle_cold
        elif cycle_keys != expected_keys or cycle_cold != cold_baseline:
            raise InnovationRawError(
                "every warm cycle must retain the same matched cold-start rows"
            )
        if cycle_baseline <= 0 or cycle_warm > cycle_baseline:
            raise InnovationRawError(
                f"{label} dead-end recurrence rows are invalid"
            )
        cycle_metrics[cycle] = {
            "baseline": cycle_baseline,
            "warm": cycle_warm,
            "pruned_total": cycle_pruned_total,
            "false_pruned": cycle_false_pruned,
            "quality_deltas": cycle_quality_deltas,
        }
    final_cycle = cycle_metrics[3]
    return {
        "warm_cycle_count": 3,
        "cold_start_baseline_present": True,
        "project_local_same_mainline": True,
        "cross_workspace_memory_used": False,
        "confirmed_dead_end_recurrence_reduction": (
            final_cycle["baseline"] - final_cycle["warm"]
        )
        / final_cycle["baseline"],
        "overall_idea_quality_delta": math.fsum(final_cycle["quality_deltas"])
        / len(final_cycle["quality_deltas"]),
        "false_prune_rate": _rate(
            final_cycle["false_pruned"],
            final_cycle["pruned_total"],
            "Track B false prune rate",
        ),
    }


def recompute_raw_benchmark(value: Any) -> dict[str, Any]:
    root = _exact(value, {"schema_version", "track_a", "track_b"}, "raw benchmark")
    if root.get("schema_version") != RAW_SCHEMA_VERSION:
        raise InnovationRawError(
            f"raw benchmark schema_version must be {RAW_SCHEMA_VERSION!r}"
        )
    track_a = _track_a(root.get("track_a"))
    track_b_raw = root.get("track_b")
    track_b = None if track_b_raw is None else _track_b(track_b_raw)
    return {"track_a": track_a, "track_b": track_b}


__all__ = [
    "InnovationRawError",
    "RAW_SCHEMA_VERSION",
    "recompute_raw_benchmark",
]
