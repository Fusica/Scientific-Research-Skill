---
name: research
description: Orchestrate evidence-grounded research from idea generation and literature review through method design, experiments, result synthesis, paper production, and reviewer revision. Use when substantial CS, ML, reinforcement-learning, LLM, robotics, or UAV research work benefits from project-local state, explicit evidence, reproducible artifacts, claim traceability, and human approval gates.
---

# Research

Run one recoverable research workflow from project state. Keep scientific evidence in canonical project artifacts and keep only bounded navigation memory in `.research/memory.md`.

## Enter through project state

1. Resolve the current project root from the active workspace or repository. Do not search an unrelated parent directory for research state.
2. Resolve the plugin root as two parent directories above this `SKILL.md`. Map every `researchctl` instruction, including a user's conversational use of that name, to `python3 <plugin-root>/scripts/researchctl.py ...`. Never assume the command is installed on `PATH`.
3. Read `references/policy.yaml` completely. It is the sole workflow and Gate policy.
4. Check `<project-root>/.research/state.json`.
   - If it is absent, say that the research workflow is not enabled for this project and suggest `researchctl init`. Do not initialize implicitly.
   - If `enabled` is `false`, say that it is disabled and suggest `researchctl enable`. Do not apply the staged workflow.
   - If it is invalid or incompatible, stop workflow actions and run or suggest `researchctl doctor`.
5. Read `.research/memory.md` and only the canonical artifacts referenced by state that matter to the request. Never read or write Codex global memory for this workflow.

## Select one stage

Treat `current_stage` as the default stage. Choose the smallest stage that answers the request, then read its reference completely:

| Stage | Reference | Primary work |
| --- | --- | --- |
| `idea` | `references/01-idea.md` | Generate, challenge, select, and freeze an idea |
| `literature` | `references/02-literature.md` | Search, verify, compare closest work, and update the idea |
| `method` | `references/03-method.md` | Formalize assumptions, equations, algorithms, interfaces, and predictions |
| `experiment_results` | `references/04-experiment-results.md` | Design and run experiments, analyze outputs, and promote claims |
| `paper` | `references/05-paper.md` | Assemble and verify an evidence-backed manuscript |
| `revision` | `references/06-revision.md` | Resolve reviews, revise artifacts, and prepare responses |

Use another stage only when the request requires a direct handoff or evidence has invalidated an upstream assumption. Check `allowed_transitions` and required Gates before changing `current_stage`. Never infer Gate approval from silence or task completion.

## Preserve the scientific contract

- Label evidence, interpretation, assumptions, exploratory findings, and unknowns; never fabricate sources, results, metadata, code behavior, checks, or decisions.
- Give stable IDs to material research objects. Version and hash mutable specifications, and bind runs to the method, experiment specification, code, configuration, data/environment, randomization, and outputs used.
- Preserve failed, excluded, null, negative, and contradictory evidence with reasons. Trace promoted claims backward to evidence and forward to manuscript and review artifacts.
- Treat `.research/memory.md` only as bounded navigation context, never as evidence or approval authority.

## Update state safely

- Register every material canonical artifact before a downstream handoff with `researchctl artifact register <role> --stage <stage-id> --path <file> --artifact-id <id> --version <version> [--status <status>]`. For a policy name such as `idea.idea_card`, pass `idea` to `--stage` and `idea_card` as `<role>`. Status is descriptive, defaults to `current`, and never means Gate approval. The command hashes but does not copy the file, so preserve approved versions at stable paths; never register `.research/state.json` or `.research/memory.md` as evidence.
- Register material files directly, or retain large run/output collections through a registered registry or manifest containing stable IDs, paths, and checksums. `researchctl` verifies the registry or manifest file itself; the current stage must still verify the files it references.
- Use `researchctl gate approve|reopen` for every Gate decision. Never edit Gate fields directly.
- Run `researchctl doctor` to verify registered pointers and hashes before Gate approval. Gate approval separately refuses missing policy-required roles. A missing or changed historical approval file remains an audit warning; reopen the affected Gate and register the replacement at a new versioned path before reapproval.
- Use `researchctl checkpoint` for a bounded recovery summary after material work. Add `--stage <stage-id>` only for a transition permitted by `allowed_transitions`; never edit `current_stage` directly.
- Update `.research/memory.md` with only durable facts, decisions, failures, open questions, and the next checkpoint; point entries to canonical artifact IDs or paths.
- Do not cross an expensive, destructive, safety-relevant, or external-action boundary without the authority required by the user and policy.

## Finish with a bounded handoff

Report the active stage, Gate state, verified artifacts or checks, unsupported or reopened items, and the next smallest action. Do not claim a stage is complete unless its policy exit criteria are satisfied.
