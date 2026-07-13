---
name: review-revision
description: Convert editor and reviewer feedback into traceable concerns, evidence actions, manuscript changes, verification checks, and point-by-point responses. Use when triaging reviews, deciding whether new experiments are necessary, revising a manuscript after peer review, writing rebuttals, or auditing consistency between replies and the actual paper.
---

# Review Revision

Answer the scientific concern first, then identify the exact manuscript change and evidence. A polished response is not ready if the promised edit or experiment is absent.

## Parse the review

Preserve editor/reviewer hierarchy and assign stable comment IDs. Split compound comments into atomic concerns without losing the original wording. Classify each as misunderstanding, clarification, literature, analysis, experiment, method, presentation, compliance, or out of scope.

## Build the action map

For every concern record:

- the underlying scientific question;
- current evidence and manuscript location;
- required versus optional action;
- missing author input or new authority;
- affected claim and artifacts;
- linked experiment, run, analysis, and manuscript change IDs;
- planned edit/experiment and verification test;
- status: ready, in progress, blocked, or not adopted with rationale.

Use `references/revision-map.md`.

## Choose the lightest defensible response

Check whether the concern is already answered by existing evidence, needs clearer wording, requires re-analysis, or truly requires new data/training/experiments. Do not overpromise. If the reviewer is factually mistaken, correct the misunderstanding directly and respectfully with evidence.

## Revise before finalizing the reply

Apply the manuscript, appendix, figure, table, or code change; verify it; then write the point-by-point reply. Distinguish newly added material from text that already existed. Keep manuscript terminology and rebuttal wording aligned.

Each response should contain:

1. a direct answer;
2. supporting evidence or reasoning;
3. the concrete change and exact location;
4. any honest limitation or boundary.

## Release check

Audit every promised action, number, citation, location, and cross-document
statement. Confirm that reviewer-facing claims are no stronger than the current
claim ledger. Require a Gate 4 decision whose release target is the current
revision or rebuttal before external submission.

Maintain `review_map.yaml`, `revision_change_log.yaml`, and the response document.
