# Idea card

Assign a stable `idea_id` when a candidate is first recorded. Store approval only in project state; the card carries `gate_ref: idea_freeze`.

```yaml
schema_version: 1
artifact_id: IDEA-CARD-001
artifact_version: 1
content_hash: null
idea_id: IDEA-001
idea_version: 1
status: candidate
gate_ref: idea_freeze
title: ""
research_question: ""
proposed_insight:
  mechanism: ""
  contribution_type: method
  expected_delta: ""
closest_work:
  - source_id: SRC-001
    overlap: ""
    smallest_defensible_difference: ""
claim_candidates:
  - claim_candidate_id: CLAIM-CAND-001
    text: ""
    required_evidence: []
predictions:
  - prediction_id: PRED-001
    claim_candidate_ids: [CLAIM-CAND-001]
    observable: ""
    falsifying_outcome: ""
    baseline_or_intervention: ""
    boundary_conditions: []
feasibility: {data: "", code: "", compute: "", hardware: "", time: ""}
risks: []
kill_criteria: []
scores:
  novelty: {band: unknown, confidence: low, evidence_ids: []}
open_questions: []
decision:
  recommendation: revise
  rationale: ""
```

Allowed recommendations are `freeze`, `revise`, and `stop`. Keep rejected candidates in a rejection log with the evidence or constraint that changed the decision. Freezing changes status/version, not identity.
