---
name: research
description: "Operate an explicitly enabled, project-local scientific research workflow with evidence, reproducible artifacts, claim traceability, and human approval Gates. Use only when the user invokes `$research` or the current repository already has `.research/state.json` with `enabled: true`; do not activate for ordinary coding, one-off writing, or general research questions outside that boundary."
---

# Research

Operate one recoverable research workflow from project-local state.

## Route

1. Check `<project-root>/.research/state.json` before loading references; never search an unrelated parent. If it is absent, disabled, invalid, or incompatible, do not apply the workflow. Suggest `researchctl init`, `enable --reason "..."`, or `doctor`, and mutate activation only when authorized. For a terminal lifecycle, follow `policy.workspace_lifecycle.terminal_access` without loading stage execution material.
2. Resolve `<plugin-root>` from this file at `<plugin-root>/skills/research/SKILL.md`. Expand every command to `python3 <plugin-root>/scripts/researchctl.py ...`; never assume `researchctl` is on `PATH`.
3. Classify the request. For ordinary code or repository maintenance that does not interpret or change research state, artifacts, evidence, claims, or decisions, do not load a numbered stage reference. For scientific workflow work, read only the relevant transition, stage, artifact, Gate, lifecycle, and authority slices of `references/policy.yaml`; then read the one reference named by `policy.stages[current_stage].reference` completely. Enter another stage only through a policy transition.
4. Read `.research/memory.md` and registered artifacts only as needed. Memory navigates; it is never evidence or approval. Read `assets/runtime-contract.json` only for machine-state or schema work.

Load `references/retrospective-revision-import.md` only after the user requests that mode and confirms its eligibility facts.

## Act

- `references/policy.yaml` alone defines workflow, transitions, artifact roles, Gate requirements, approval modes, cascade, lifecycle, and authority. Numbered references describe execution and cannot override it.
- Distinguish evidence, interpretation, assumptions, exploration, and unknowns. Preserve supporting, opposing, failed, null, negative, excluded, and contradictory evidence with reasons. Never fabricate.
- Give material objects stable IDs and follow `policy.artifact_layout`. Use `researchctl record append` for typed records, `artifact register` for artifacts, and `trace [record-id]` for the disposable forward/reverse projection; never hand-edit registered prefixes or treat the trace as scientific judgment.
- Change activation, artifacts, Gates, lifecycle, and checkpoints only through `researchctl`. Human Gate and lifecycle decisions require explicit direction and policy-required review fields; never infer approval. If evidence invalidates an approved boundary, reopen the earliest affected GateRef and let `researchctl` compute cascade and stage movement.
- Run `doctor` before approval. Use `checkpoint` for bounded recovery, and keep `.research/memory.md` to durable facts, decisions, failures, open questions, and next action.
- For external operations, follow `policy.adapter_authority`: append requests and receipts atomically, run `researchctl adapter verify` for each new attempt, and register `accepted` before side effects. Consume only bound immutable snapshots; append terminal facts as superseding receipts. Receipts never approve Gates or claims. Reconcile `unknown` before retry unless policy declares idempotency.
- For researcher-selected `experiment_execution` or `paper_production`, use `assets/reference-stack-payload.template.json` and `<plugin-root>/scripts/reference_stack.py`. Bind publish paths under `.research/artifacts/<stage>/reference-stack/<attempt-id>/`. It journals, materializes every non-payload request input exactly once, runs declared commands, and publishes first-observation-bound outputs, logs, and result in one no-clobber `artifact publish-batch`, then records the terminal receipt. Retries reuse artifact IDs with a fresh path after reconciling `unknown` or crash orphans. It neither enforces network isolation nor certifies review, venue facts, validity, release, or submission; never use it for `external_release`.
- Do not cross costly, destructive, hardware, safety, publication, submission, or other external-action boundaries without user and policy authority.

## Hand off

Report stage and Gate state, registered revisions or checks, unsupported or reopened items, and the next smallest action. Use `doctor --json` for machine diagnostics and `audit export` with an externally retained evidence root for offline hand-off. A stage completes only when policy criteria are satisfied.
