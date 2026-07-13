# Research repository operating rules

## Mission

Build defensible research through explicit evidence, reproducible artifacts, and human-approved decisions. Optimize for scientific integrity and traceability before writing fluency or benchmark gains.

## Routing

- Start substantial or cross-stage work with `$research-orchestrator`.
- Route idea work to `$idea-evolution`.
- Route search and novelty evidence to `$literature-evidence`.
- Route mathematical and algorithmic specification to `$method-formalization`.
- Route experiment planning, execution, and diagnosis to `$experiment-lifecycle`.
- Route statistics and claim promotion to `$result-synthesis`.
- Route manuscript work to `$paper-production`.
- Route peer-review revision to `$review-revision`.

For a bounded request, use only the smallest applicable skill.

## Research state

- Read `AGENTS.md`, `.research/project-overview.md`, `.research/project-state.yaml`, and the planning bundles explicitly listed by `active_planning_tasks` before resuming substantial work. On conflict, project state controls stages, Gates, and artifact registry metadata; each canonical artifact controls its scientific content; `task_plan.md` controls task execution status; overview never overrides them.
- Use project-local `.research/` artifacts as the durable scientific record. Treat `.research/project-overview.md` as a derived navigation view, never as a second approval authority.
- Use `.planning/<task-id>/` as the default execution layer for every non-trivial research task. Create or reuse `task_plan.md`, `findings.md`, and `progress.md` before substantive execution.
- A task is non-trivial when it has dependent steps, multiple files, literature/data/code/experiment/manuscript work requiring verification, agent delegation, cross-stage decisions, or a meaningful recovery requirement. Simple factual answers, one-line rewrites, and tiny formatting changes may skip file initialization.
- Existing repository conventions take precedence; map them to the artifact contract instead of duplicating state.
- Treat `.planning/` as task execution state only. Never use `approved` there, and never promote a planning note, model guess, or unverified abstract directly into evidence.
- Treat `.research/project-state.yaml` as the sole gate authority. Bind each gate decision to artifact IDs, versions, and content hashes.
- Promote a planning finding only after verification into the canonical `.research/` artifact, then refresh the derived project overview.

## Human gates

Do not infer approval. Require explicit human approval for:

1. idea freeze;
2. method and experiment plan;
3. claim freeze;
4. external submission or reviewer response.

Prepare gate material autonomously, but do not cross a gate silently.
Gate 4 may authorize an initial submission or a later revision/rebuttal as separate release targets.

## Agent orchestration

Use parallel agents when tasks are genuinely independent. Give each agent a bounded objective, required inputs, exact write scope, validation criteria, and handoff format. Avoid overlapping edits. The orchestrator reviews evidence and integrates results; subagent agreement is not itself scientific validation.

Record delegated steps and owners in the active `task_plan.md`; record handoffs in `findings.md` and execution outcomes in `progress.md`. Runtime todo tools may mirror the plan but cannot replace the project-local planning bundle.

When the runtime exposes model choices, reserve the strongest reasoning configuration for planning, review, and synthesis; use capable coding configurations for implementation; use fast context agents for read-only repository or literature triage.

## Evidence and execution rules

- Give stable IDs to sources, evidence records, experiments, runs, claims, figures/tables, reviewer comments, and changes.
- Version and hash mutable specifications; bind every run to its exact experiment spec and method contract.
- Preserve negative, failed, excluded, and contradictory evidence with reasons.
- Record code commit, dirty state, configuration hash, data/environment version, seed, and runtime for substantive runs.
- Never choose only favorable seeds or tune on the final test set.
- Start new claims as `unassessed`; promote them only after registered analysis.
- Domain profiles, literature, and the data-generating process determine statistical units and thresholds; generic upstream defaults do not.
- Never fabricate citations, results, metadata, code behavior, manuscript locations, or completed actions.
- Treat vendored upstream content as read-only reference material. Implement local behavior in `skills/`.

## External actions

Creating local artifacts and running safe validations are allowed within the task. Pushing, submitting, sending, publishing, destructive data operations, costly compute, and safety-relevant hardware execution require the authority implied by the user's request or explicit confirmation.
