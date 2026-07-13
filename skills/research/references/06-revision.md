# Stage 6: Review and revision

Resolve the scientific concern first, apply and verify the corresponding change, then write the response. A polished reply is not ready when its promised evidence or edit is absent.

## Preserve and decompose the review

Preserve editor and reviewer hierarchy and the verbatim wording. Give each comment a stable ID and split compound comments into atomic concerns without weakening or omitting the original request.

Classify concerns as misunderstanding, clarification, literature, analysis, experiment, method, presentation, compliance, or out of scope. For each concern identify the underlying scientific question, current evidence, affected claims, current manuscript location, required versus optional action, missing human input, and verification test.

## Maintain the review map

Use an entry such as:

```yaml
comment_id: R1-C01
reviewer: Reviewer 1
verbatim_comment: ""
atomic_concern: ""
category: experiment
scientific_question: ""
current_state:
  evidence_ids: []
  claim_ids: []
  experiment_ids: []
  run_ids: []
  analysis_ids: []
  manuscript_locations: []
action:
  necessity: required
  type: reanalysis
  description: ""
  missing_author_input: []
  target_files: []
  planned_change_ids: []
  verification: ""
status: in_progress
response:
  direct_answer: ""
  evidence_summary: ""
  change_summary: ""
  exact_locations: []
  limitation: ""
release_checks:
  action_completed: false
  manuscript_matches_reply: false
  numbers_verified: false
  citations_verified: false
  claim_strength_verified: false
```

Use `required`, `optional`, or `not_adopted` for necessity. Give every not-adopted action a scientific, evidential, safety, or scope rationale rather than a convenience rationale. Use `ready` only after the change and all applicable checks pass.

Record completed edits separately in a revision change log and link change IDs to reviewer comments, paper changes, claims, experiments, runs, and analyses.

## Choose the lightest defensible action

Determine whether existing evidence already answers the concern, clearer wording is enough, registered reanalysis is needed, or new data, training, or experiments are genuinely required. Distinguish must-fix items from optional strengthening. Do not propose expensive reruns before checking existing runs, evaluation-only analyses, provenance, and the exact requested claim.

Correct a factual misunderstanding directly and respectfully with evidence. Do not overexplain, concede an incorrect premise, or create unnecessary future-work commitments.

Return to literature, method, experiment/results, or paper work when evidence is missing. Reopen affected Gates when a frozen artifact or claim must change.

## Apply and verify before responding

Apply the manuscript, appendix, table, figure, code, or analysis change first. Verify it from the actual source, diff, outputs, and rendered document. Distinguish new changes from text that already existed, and keep terminology and numerical claims aligned across the manuscript and response.

Write each response with:

1. a direct answer to the atomic concern;
2. supporting evidence or reasoning;
3. the concrete completed change and exact stable location;
4. an honest limitation or boundary where relevant.

Never claim an experiment, citation check, edit, compilation, or visual inspection was completed without verifying it. Never promise an action that is not represented in the action and change maps.

## Audit and release

Check every comment, promised action, number, citation, file location, manuscript/rebuttal statement, claim boundary, and rendered change. Confirm that reviewer-facing claims do not exceed the current claim ledger and that all affected source files agree.

Maintain canonical equivalents of a review map, revision change log, response document, manuscript diff, verification records, and release checklist.

Use `researchctl gate approve release --reason "..."` for release target `revision_rebuttal` only after explicit human approval of the exact revised manuscript and response artifacts. Reopen release approval after any material edit or failed promise, number, citation, location, rendering, or consistency check. Do not send the response externally without release authority.
