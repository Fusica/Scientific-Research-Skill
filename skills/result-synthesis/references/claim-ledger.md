# Claim ledger

New claims start as `unassessed`; analysis promotes them to a supported status and Gate 3 freezes the ledger through project state.

```yaml
schema_version: 1
artifact_id: CLAIM-LEDGER-001
artifact_version: 1
content_hash: null
gate_ref: claim_freeze
claims:
  - claim_id: CLAIM-001
    claim_version: 1
    supersedes_claim_id: null
    origin_claim_candidate_ids: [CLAIM-CAND-001]
    prediction_ids: [PRED-001]
    experiment_ids: []
    text: ""
    status: unassessed
    scope:
      settings: []
      assumptions: []
    evidence:
      literature_evidence_ids: []
      run_ids: []
      analysis_ids: []
      artifact_ids: []
    statistics:
      estimand: ""
      estimate: null
      effect_size: null
      uncertainty: ""
      test: null
      multiplicity_handling: null
    limitations: []
    allowed_wording: ""
    forbidden_stronger_wording: ""
    manuscript_locations: []
```

Allowed statuses are `unassessed`, `supported`, `bounded`, `exploratory`, `unsupported`, and `contradicted`.

Every `analysis_id` resolves to an analysis record containing the included/excluded run population, code/config provenance, statistical unit, estimand, uncertainty method, and output artifact IDs. Every figure/table ID resolves to a checksummed artifact-manifest record.
