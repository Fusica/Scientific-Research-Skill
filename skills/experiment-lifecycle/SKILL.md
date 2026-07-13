---
name: experiment-lifecycle
description: Design, register, execute, diagnose, and refine research experiments through staged evidence gates and reproducible run records. Use when planning baselines, metrics, ablations, robustness tests, hyperparameter searches, simulation or real-world studies, experiment code, failed-run recovery, or deciding what experiment should run next.
---

# Experiment Lifecycle

Treat experiments as tests of explicit claims and mechanisms, not a search for favorable numbers.

## Design before execution

Read the method contract, idea card, evidence matrix, available resources, and domain profile. For each experiment specify the question, hypothesis, independent variables, controls, baselines, data/environment, statistical unit, metrics, repetitions or seeds, analysis method, expected artifact, cost, safety constraints, and stop/kill criteria.

Do not import fixed thresholds from a generic upstream workflow. Seed counts, variance expectations, effect thresholds, and evaluation protocols must follow the domain profile, literature, and resource constraints.

## Use progressive stages

1. **Integrity checks:** data, environment, metric, leakage, and deterministic smoke tests.
2. **Baseline reproduction:** confirm implementation and evaluation parity.
3. **Minimal mechanism test:** test the smallest version of the proposed insight.
4. **Main comparison:** run the approved primary evaluation.
5. **Ablation and causal probes:** isolate claimed mechanisms.
6. **Robustness, generalization, safety, scaling, and efficiency:** only where relevant to the claim.

Require human approval before expensive, safety-relevant, or irreversible execution.

## Register every run

Use `references/experiment-records.md`. Lock each experiment row with a spec
version and hash. Every run points to that exact spec and method-contract
version, and records the exact command, code commit/dirty patch, resolved
configuration, profile, data/environment, randomization, runtime lock,
checksummed outputs, execution status, scientific outcome, and analysis
eligibility. Never silently replace failed runs or retain only the best seed.

## Diagnose and iterate

When a run fails scientifically or technically:

- classify data, implementation, optimization, evaluation, resource, or hypothesis failure;
- reproduce the simplest failing case;
- change one causal factor where practical;
- record the rationale and result;
- return to method or idea stages if evidence invalidates an assumption.

Record each diagnosis and controlled change in the experiment decision log.
Keep execution failure separate from a scientifically negative or falsifying
outcome.

## Deliver artifacts

Maintain `experiment_matrix.yaml`, `run_registry.jsonl`, and
`decision_log.yaml`. End with evidence gaps and the next approved run, not an
open-ended promise to keep tuning.
