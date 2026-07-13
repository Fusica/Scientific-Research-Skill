# Review and revision map

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

Allowed necessity values are `required`, `optional`, and `not_adopted`. A not-adopted action needs a scientific or scope rationale, not convenience alone.

Record completed changes separately in `revision_change_log.yaml`, linking revision-change IDs to comment, paper-change, claim, experiment, run, and analysis IDs. Use `ready` only after the change and all relevant checks are complete. Preserve the original comment verbatim even when decomposing it.
