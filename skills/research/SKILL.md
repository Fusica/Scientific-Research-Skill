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
6. Ignore `.planning/`; it is not part of this workflow and must not be created or required.

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

- Distinguish verified evidence, author interpretation, assumptions, exploratory findings, and open questions.
- Give stable IDs to material sources, ideas, predictions, experiments, runs, analyses, claims, figures/tables, reviewer comments, and changes.
- Version and hash mutable specifications. Bind each run to the exact method, experiment specification, code, configuration, data/environment, randomization, and outputs used.
- Preserve failed, excluded, null, negative, and contradictory evidence with reasons. Never select only favorable runs or seeds.
- Trace every promoted claim backward to evidence and analyses and forward to manuscript locations and reviewer replies.
- Treat `.research/memory.md` as navigation context, never as evidence or approval authority.
- Never fabricate citations, results, metadata, code behavior, completed checks, or human decisions.

## Update state safely

- Use `researchctl gate approve|reopen` for every Gate decision. Never edit Gate fields directly.
- Use `researchctl checkpoint` for a bounded recovery summary after material work. Add `--stage <stage-id>` only for a transition permitted by `allowed_transitions`; never edit `current_stage` directly.
- Update `.research/memory.md` with only durable facts, decisions, failures, open questions, and the next checkpoint; point entries to canonical artifact IDs or paths.
- Do not cross an expensive, destructive, safety-relevant, or external-action boundary without the authority required by the user and policy.

## Finish with a bounded handoff

Report the active stage, Gate state, verified artifacts or checks, unsupported or reopened items, and the next smallest action. Do not claim a stage is complete unless its policy exit criteria are satisfied.
