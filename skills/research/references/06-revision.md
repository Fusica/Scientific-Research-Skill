# Stage 6: Review and revision

Resolve the concern, apply and verify the change, then write the response.

## Preserve and decompose the review

Preserve editor/reviewer hierarchy and verbatim wording. Give each comment a stable ID, split compound comments into atomic concerns, and classify each concern. Record its scientific question, evidence, affected claims and locations, required versus optional action, missing human input, and verification test.

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

Use `required`, `optional`, or `not_adopted` for necessity and justify non-adoption scientifically, evidentially, by safety, or by scope. Mark `ready` only after applicable checks pass. Link the revision change log to comments, paper changes, claims, experiments, runs, and analyses.

## Choose the lightest defensible action

Choose among existing evidence, clearer wording, registered reanalysis, or genuinely necessary new work. Separate must-fix items from optional strengthening and inspect existing runs, evaluation-only options, provenance, and the exact claim before proposing reruns. Correct misunderstandings directly with evidence.

Return to literature, method, experiment/results, or paper work when evidence is missing. Reopen affected Gates when a frozen artifact or claim must change.

## Apply and verify before responding

Apply changes first and verify them from source, diffs, outputs, and rendered documents. Distinguish new changes from existing text and align terminology and numbers across manuscript and response.

Write each response with:

1. a direct answer to the atomic concern;
2. supporting evidence or reasoning;
3. the concrete completed change and exact stable location;
4. an honest limitation or boundary where relevant.

## Audit and release

Check every comment, promised action, number, citation, location, manuscript/response statement, claim boundary, and rendered change against the claim ledger and affected files.

Register `revision.revised_manuscript`, `review_map`, `change_log`, `response_document`, `manuscript_diff`, `verification_records`, `rendered_output`, and `release_checklist` for the release package.

After explicit human approval of the exact revised manuscript and response artifacts, record `release` for target `revision_rebuttal` through `researchctl`. Reopen it after any material edit or failed promise, number, citation, location, render, or consistency check; external sending still requires release authority.
