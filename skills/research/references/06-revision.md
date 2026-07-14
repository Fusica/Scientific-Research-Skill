# Stage 6: Review and revision

Resolve the concern, apply and verify the change, then write the response. Treat
the response letter as an editor-facing explanation, not a change log or results
appendix.

## Preserve and decompose the review

Preserve editor/reviewer hierarchy and verbatim wording. Give each comment a stable ID, split compound comments into atomic concerns, and classify each concern. Separate summary or praise from actionable feedback. For each atomic concern, identify both the surface request and the underlying question whose answer could change the reviewer's judgment. Record its evidence, affected claims and locations, required versus optional action, missing human input, and verification test.

## Maintain the review map

Use an entry such as:

```yaml
comment_id: R1-C01
reviewer: Reviewer 1
verbatim_comment: ""
atomic_concern: ""
category: experiment
scientific_question: ""
underlying_concern: ""
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
  stance: ""
  direct_answer: ""
  decisive_evidence_ids: []
  evidence_interpretation: ""
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

Choose among existing evidence, clearer wording, registered reanalysis, or genuinely necessary new work. Separate must-fix items from optional strengthening and inspect existing runs, evaluation-only options, provenance, and the exact claim before proposing reruns. Select the smallest evidence set that decides the concern; do not use data volume as a proxy for an answer. Correct misunderstandings directly with evidence.

Return to literature, method, experiment/results, or paper work when evidence is missing. Reopen affected Gates when a frozen artifact or claim must change.

## Apply and verify before responding

Apply changes first and verify them from source, diffs, outputs, and rendered documents. Distinguish new changes from existing text and align terminology and numbers across manuscript and response.

Write point by point in the original reviewer order. A shared analysis or experiment may be referenced across comments, but each comment still needs a direct answer in its own terms. Use this reasoning order without forcing identical prose:

1. acknowledge the specific concern where useful and state whether the response accepts, clarifies, partly accepts, or narrowly disagrees;
2. answer the underlying question within the opening sentences;
3. give only the decisive evidence or reasoning and explain why it resolves, narrows, or fails to resolve the concern;
4. state the concrete completed change and exact stable location;
5. state an honest remaining limitation or boundary only when material.

Keep the tone cooperative, confident, and non-defensive rather than submissive. Do not require every response to begin with generic thanks, apologize for a reviewer misunderstanding unless the manuscript was actually unclear, or use `we added` as a substitute for explaining why the change answers the concern. When disagreeing, first acknowledge the legitimate concern, narrow the disagreement to one scientific point, support it, and make any clarification or claim reduction needed.

Tables, figures, and numbers support the answer; they do not constitute it. State the takeaway before presenting compact evidence, interpret what the evidence supports or does not support, and leave unrelated values or full configurations in the manuscript or supplement. A reader given only the comment and response should be able to identify the answer, its basis, the resulting change, and any remaining boundary without reconstructing the argument from a data dump.

## Audit and release

Check every comment, promised action, number, citation, location, manuscript/response statement, claim boundary, and rendered change against the claim ledger and affected files.

Register `revision.revised_manuscript`, `review_map`, `change_log`, `response_document`, `manuscript_diff`, `verification_records`, `rendered_output`, and `release_checklist` for the release package.

After explicit human approval of the exact revised manuscript and response artifacts, record `release` for target `revision_rebuttal` through `researchctl`. Reopen it after any material edit or failed promise, number, citation, location, render, or consistency check; external sending still requires release authority.
