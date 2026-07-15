# ADR 0001: Use full-lifecycle, human-sovereign research self-governance

- Status: Accepted
- Date: 2026-07-15
- Scope: Scientific Research Skill workflow and experiment-review behavior

## Context

The design interview selected strong procedural supervision with the researcher retaining final scientific authority. The implementation must cover the full paper lifecycle without turning experiment learning into another state machine.

## Decision index

This ADR records rationale only. Canonical behavior lives in:

- `skills/research/references/policy.yaml`: `workspace_lifecycle`, `authority_boundary`, `workflow_graph`, Gate contracts, and artifact layout;
- `skills/research/assets/runtime-contract.json`: lifecycle, activation, decision, Gate, artifact, and transition machine fields;
- `skills/research/references/04-experiment-results.md`: the stage-owned cumulative-review and Retry procedure.

Those sources implement the accepted boundary: strict review at commitment points, human scientific sovereignty, one paper-bound mainline per workspace, and a lightweight append-only experiment learning loop. They intentionally do not add authenticated roles, a paused state, an import state machine, per-run approval state, or a numerical Bayesian/sequential optimizer.

## Rationale and limits

- Mechanical completeness and traceability can be enforced; scientific truth and approval quality cannot.
- The cumulative review should be exercised on real sequential experiments before adding Run Contract machinery.
- Trust-region, sequential-test, and Bayesian language remains analogy or researcher-authored methodology until a mathematical contract exists.

## Open follow-up

- Define the evidence threshold separating a bounded next-parameter choice from a material method/experiment-contract change that requires Gate reopen.
- Exercise at least three sequential experiment outcomes through one cumulative review and next-experiment recommendation.
- Run the installed-bundle smoke test and release workflow only after the integrated v2 worktree is committed and versioned.
