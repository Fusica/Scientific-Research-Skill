---
schema_version: 1
artifact_id: OVERVIEW-001
artifact_version: 1
project_id: PROJECT-001
derived_from_state_version: 1
synced_gate_decision_ids: []
active_planning_tasks: []
updated_at: ""
---

# Project Overview

> This file is a derived navigation view. `.research/project-state.yaml` remains the sole scientific Gate authority and wins on conflict. Do not register this derived file back into project state or use it as Gate evidence.

## Research kernel

State the problem, proposed mechanism or approach, intended contribution, and main boundary in one sentence.

## Scope and non-goals

- In scope:
- Out of scope:

## Current stage and Gate summary

Mirror the current stage and latest Gate decision IDs from `project-state.yaml`. Do not create approval here.

## Canonical artifacts

List artifact ID, version, status, and path. Link rather than copy raw evidence, metrics, or run tables.

## Current claims

List only claim ID, current ledger status, and one bounded sentence. Do not turn an unassessed claim into an affirmative conclusion.

## Resources and constraints

Record compute, data, hardware, safety, schedule, access, and venue constraints that shape the project.

## Open scientific decisions

List unresolved decisions and the evidence needed to close them.

## Canonical terminology

Record terms whose spelling or meaning must remain consistent across code, artifacts, manuscript, and rebuttal.

## Active planning tasks

List each active task ID in front matter under `active_planning_tasks`, then link its `.planning/<task-id>/task_plan.md` here. The shared hook follows only these explicit pointers and verifies the task-plan status; it never guesses the current task from modification time. Planning status is execution state, not scientific approval.
