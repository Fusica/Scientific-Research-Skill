# Default Planning with Files contract

Planning with Files is the default execution layer for every non-trivial research task. It has no scientific Gate authority and is independent of whether a separate planning skill is installed.

## Trigger

Create or reuse one `.planning/<task-id>/` bundle before substantive execution when a task has any of these properties:

- multiple dependent steps or files;
- literature, data, code, experiment, or manuscript work that must be verified;
- agent delegation or parallel work;
- cross-stage decisions, tool-driven iteration, or a meaningful recovery requirement.

Simple factual answers, one-line rewrites, and tiny formatting changes may skip bundle creation. The evidence and Gate boundaries still apply.

## Required files

```text
.planning/<task-id>/
├── task_plan.md
├── findings.md
└── progress.md
```

Initialize them from the plugin templates at `../../../contracts/planning/`, resolved relative to this reference file. Reuse an existing task directory when its objective and write scope still match; do not create a new bundle merely because chat context was compacted.

While the task is active, add its exact `<task-id>` to `project-overview.md` front matter under `active_planning_tasks`. The overview is only a pointer index; `task_plan.md` remains the execution-status authority. Remove a task from the active list after marking it `completed` or `superseded`. Never select a current task from file modification time.

### `task_plan.md`

This is the execution authority for one task. Record objective, scope/non-goals, input artifact IDs/versions/paths, write scope, dependency-ordered steps, owners, acceptance criteria, blockers, planned outputs, and final handoff. Keep at most one step `in_progress`.

Allowed states are `pending`, `in_progress`, `awaiting_user`, `blocked`, `completed`, and `superseded`. Never use `approved`, because scientific approval belongs to `.research/project-state.yaml`.

### `findings.md`

Store provisional observations, diagnostics, candidate interpretations, search notes, and subagent handoffs. Mark entries `provisional`, `verified`, `promoted`, or `rejected`. Verification means the cited source or execution was checked; it does not make the finding a supported paper claim.

### `progress.md`

Append material actions, results, errors, retries, output paths, and next steps. It is a recovery log, not a Gate or claim ledger.

## Promotion boundary

Planning artifacts may point to sources, runs, code, and candidate decisions. To promote content into `.research/`:

1. verify the source, run, code path, or analysis;
2. write the result into the canonical scientific artifact with stable IDs and provenance;
3. link the promoted artifact back from `findings.md`;
4. update `project-state.yaml` only when a stage, registry entry, transition, or explicit human Gate decision changes;
5. refresh `project-overview.md` as a derived navigation view.

Never cite `findings.md` or `progress.md` as the sole evidence for a paper claim.

## Resume and close

On resumption, read `AGENTS.md`, `project-overview.md`, `project-state.yaml`, the current planning bundle, and the actual source-control/filesystem state. State wins over overview; verified artifacts win over planning notes; current files win over remembered chat context.

Close the task only after its acceptance criteria are verified and the handoff records promoted artifacts, unresolved risks, and the next smallest action. Preserve superseded or failed plans instead of rewriting history.
