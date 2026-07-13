---
name: research-orchestrator
description: Orchestrate a multi-stage research project from idea formation through literature, method design, experiments, claims, paper production, and reviewer revision. Use when starting or resuming a substantial research project, deciding the next research stage, coordinating several research skills, or auditing whether stage artifacts and human approval gates are complete.
---

# Research Orchestrator

Route work through explicit artifacts and approval gates. Treat the workflow as a recoverable research state machine, not a fixed waterfall: evidence can invalidate an idea, experiments can expose a method flaw, and review can reopen analysis.

## Start from project state

1. Inspect the repository, existing research artifacts, user instructions, and any domain or venue profile.
2. If this is a multi-stage project and no state exists, initialize a project-local `.research/` directory from the artifact chain in `references/artifact-chain.md`.
3. Record facts already supported by files separately from assumptions and open questions.
4. Select the smallest stage that advances the project. Do not restart completed stages without evidence that their inputs changed.

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

## Separate coordination notes from scientific records

`Planning with Files` is optional and useful for long, recoverable, or multi-agent execution. Put task coordination in `.planning/<task>/`; put durable scientific evidence and decisions in `.research/`. Do not treat a planning note as evidence merely because it exists on disk.

## Finish each turn with a handoff

Report:

- current stage and gate status;
- artifacts created or updated;
- evidence-backed decisions;
- unresolved risks or missing inputs;
- the next smallest executable action.
