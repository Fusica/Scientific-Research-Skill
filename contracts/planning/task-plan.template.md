---
schema_version: 1
task_id: ""
status: pending
created_at: ""
updated_at: ""
research_stage: ""
owners: []
input_artifacts: []
write_scope: []
planned_output_artifacts: []
---

# Task Plan

## Objective

Define one verifiable task outcome.

## Scope and non-goals

- In scope:
- Non-goals:

## Inputs and authority

List the exact `.research` artifact IDs, versions, and paths that constrain this task. Planning notes never override them.

## Execution plan

Use dependency order and keep at most one item `in_progress`.

| Step | Owner | Status | Acceptance criteria |
| --- | --- | --- | --- |
| 1 |  | pending |  |

Allowed execution states: `pending`, `in_progress`, `awaiting_user`, `blocked`, `completed`, `superseded`. Do not use `approved`; scientific approval belongs only in `project-state.yaml`.

## Risks, blockers, and approvals

Separate execution approval, alignment checkpoints, and scientific Gates.

## Final handoff

Record verified outputs, promoted `.research` artifacts, unresolved risks, and the next smallest action.
