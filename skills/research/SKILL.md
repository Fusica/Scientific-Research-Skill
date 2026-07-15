---
name: research
description: "Operate an explicitly enabled, project-local scientific research workflow with evidence, reproducible artifacts, claim traceability, and human approval Gates. Use only when the user invokes `$research` or the current repository already has `.research/state.json` with `enabled: true`; do not activate for ordinary coding, one-off writing, or general research questions outside that boundary."
---

# Research

Operate one recoverable research workflow from project-local state.

## Enter and route

1. Resolve the active project root; never search an unrelated parent for research state. Check `<project-root>/.research/state.json` before loading references.
   - If absent, disabled, invalid, or incompatible, do not apply the workflow. Suggest the corresponding `researchctl init`, `enable --reason "..."`, or `doctor` action; initialize or enable only when authorized.
   - If lifecycle is terminal, follow `policy.workspace_lifecycle.terminal_access`; do not load a stage execution reference or advance research.
2. Resolve `<plugin-root>` from this file at `<plugin-root>/skills/research/SKILL.md`. Expand every `researchctl` instruction to `python3 <plugin-root>/scripts/researchctl.py ...`; never assume it is on `PATH`.
3. Classify the request before loading stage material.
   - For ordinary code or repository maintenance that neither interprets nor changes research state, research artifacts, evidence, claims, or decisions, do not load a numbered stage reference. Hooks still apply their mechanical protections.
   - For scientific workflow work, read only the needed slices of `references/policy.yaml`: the relevant transition, current stage, artifact, Gate, lifecycle, and authority contracts. Then read the one reference named by `policy.stages[current_stage].reference` completely. Use another stage only through a policy transition.
4. Read `.research/memory.md` and registered artifacts only as needed. Memory is navigation, never evidence or approval authority. Read `assets/runtime-contract.json` only when diagnosing or changing machine state/schema behavior.

Load `references/retrospective-revision-import.md` only after the user explicitly requests that policy mode and confirms its eligibility facts.

## Execute within the boundary

- `references/policy.yaml` alone defines workflow, transitions, artifact roles, Gate requirements, approval modes, cascade, lifecycle, and authority. Numbered references describe scientific execution and must not override it.
- Label evidence, interpretation, assumptions, exploratory findings, and unknowns. Preserve supporting, opposing, failed, null, negative, excluded, and contradictory evidence with reasons. Never fabricate sources, results, metadata, behavior, checks, decisions, or completed actions.
- Give material research objects stable IDs. Follow `policy.artifact_layout` for stable working files, immutable revisions, snapshots, cardinality, and large-file manifests.
- Make artifact, Gate, lifecycle, activation, and checkpoint changes only through `researchctl`; never edit control fields directly. Gate and lifecycle decisions require explicit human direction and the structured review fields required by policy. Never infer approval.
- When evidence invalidates an approved boundary, reopen the earliest affected GateRef named by policy before changing protected material. Let `researchctl` compute downstream cascade and stage movement; do not reconstruct them in prose or state edits.
- Run `researchctl doctor` before approval. Use `checkpoint` for bounded recovery summaries, and keep `.research/memory.md` limited to durable facts, decisions, failures, open questions, and the next action.
- Do not cross costly, destructive, safety-relevant, hardware, publication, submission, or other external-action boundaries without the authority required by the user and policy.

## Hand off

Report the active stage and Gate state, current registered revisions or checks, unsupported or reopened items, and the next smallest action. A stage is complete only when the current policy criteria are satisfied.
