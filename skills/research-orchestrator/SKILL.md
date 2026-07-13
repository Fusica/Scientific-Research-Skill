---
name: research-orchestrator
description: Orchestrate a multi-stage research project from idea formation through literature, method design, experiments, claims, paper production, and reviewer revision. Use when starting or resuming a substantial research project, deciding the next research stage, coordinating several research skills, or auditing whether stage artifacts and human approval gates are complete.
---

# Research Orchestrator

Route work through explicit artifacts and approval gates. Treat the workflow as a recoverable research state machine, not a fixed waterfall: evidence can invalidate an idea, experiments can expose a method flaw, and review can reopen analysis.

## Start from project state

1. Inspect user instructions and `AGENTS.md`, then read `.research/project-overview.md`, `.research/project-state.yaml`, the active `.planning/<task-id>/` bundle, source-control changes, and the actual source artifacts. Project state wins if the overview is stale; verified files win over remembered chat context.
2. If this is a multi-stage project and no state exists, initialize a project-local `.research/` directory from the artifact chain in `references/artifact-chain.md`, including the derived project overview.
3. For every non-trivial research task, create or reuse `.planning/<task-id>/task_plan.md`, `findings.md`, and `progress.md` before substantive execution. Follow `references/planning-with-files.md`.
4. Record facts already supported by files separately from assumptions, provisional findings, and open questions.
5. Select the smallest stage that advances the project. Do not restart completed stages without evidence that their inputs changed.

## Route to the stage skill

- Use `$idea-evolution` for candidate generation, criticism, comparison, and idea freeze.
- Use `$literature-evidence` for background search, closest-work analysis, evidence records, and novelty stress tests.
- Use `$method-formalization` for assumptions, mathematics, algorithms, interfaces, and testable predictions.
- Use `$experiment-lifecycle` for experiment matrices, run governance, diagnosis, and iterative execution.
- Use `$result-synthesis` for statistical analysis, figures, tables, and claim promotion.
- Use `$paper-production` for evidence-backed manuscript assembly and submission checks.
- Use `$review-revision` for reviewer mapping, targeted revisions, and point-by-point replies.

Use the routing rules in `references/routing.md` when a request spans several stages.

## Enforce gates

Require explicit human approval at these boundaries:

1. **Idea freeze:** approve one idea and its falsification/kill criteria.
2. **Method and experiment approval:** approve assumptions, baselines, metrics, resources, and safety constraints before expensive execution.
3. **Claim freeze:** approve which findings are sufficiently supported for the manuscript.
4. **Submission or revision release:** approve the final manuscript and external response.

Do not infer approval from silence. Work may prepare the next gate without crossing it.

`.research/project-state.yaml` is the sole gate authority. A gate decision
must bind the decision ID to artifact IDs, versions, and content hashes. Stage
artifacts carry only a gate reference; they cannot approve themselves. Gate 4
can authorize either an initial submission or a revision/rebuttal release.

## Preserve traceability

- Give each idea, source, experiment, run, claim, figure, reviewer comment, and manuscript change a stable ID.
- Version and hash mutable artifact specifications; a run must point to the exact experiment and method versions it executed.
- Link claims backward to runs and sources, and forward to manuscript locations and reviewer replies.
- Record code commit, configuration, data version, environment, and failure state for every substantive run.
- Mark unsupported, exploratory, conflicting, and negative evidence instead of deleting it.
- Never invent a citation, result, implementation detail, or approval state.

## Enforce the execution/science boundary

Planning with Files is the default coordination layer for non-trivial research work. Put the current objective, steps, owners, provisional findings, failures, and progress in `.planning/<task-id>/`; put verified scientific evidence, specifications, runs, claims, and Gate decisions in `.research/`.

Planning status never approves science. Promote a finding only after source or run verification, preserve its stable IDs and provenance in the canonical `.research/` artifact, link the promotion from `findings.md`, update project state when warranted, and then refresh `project-overview.md`. Read `references/planning-with-files.md` for the complete contract.

## Finish each turn with a handoff

Report:

- current stage and gate status;
- active planning task and completed execution steps;
- artifacts created or updated;
- evidence-backed decisions;
- unresolved risks or missing inputs;
- the next smallest executable action.
