# Experiment, run, and decision records

## Experiment matrix

The matrix has its own artifact ID/version/hash and exact idea/method refs. Each row includes:

```yaml
experiment_id: EXP-001
spec_version: 1
spec_hash: null
stage: baseline_reproduction
origin_claim_candidate_ids: [CLAIM-CAND-001]
prediction_ids: [PRED-001]
question: ""
hypothesis: ""
controls: []
baselines: []
statistical_unit: ""
metrics: {primary: [], secondary: []}
repetitions: {design: "", rationale: ""}
analysis_plan:
  plan_id: ANALYSIS-PLAN-001
  version: 1
  content_hash: null
  primary_estimand: ""
  inclusion_criteria: []
  exclusion_criteria: []
  method: ""
  uncertainty: ""
resources: {compute: "", time: "", hardware: ""}
safety_constraints: []
stop_criteria: []
kill_criteria: []
```

## Run registry

Write one JSON object per line. Required groups are:

- run ID, experiment ID, experiment spec version/hash, and method contract ID/version/hash;
- parent/superseded run lineage and timestamps;
- separate `execution_status` and `scientific_outcome`;
- repository/commit, dirty patch artifact/hash, exact command/arguments/cwd;
- resolved config artifact/hash and randomization record;
- profile ID/version/hash and data/environment ID/version/split/hash;
- hardware, OS, software lock/hash, and container digest;
- output artifact IDs/paths/checksums;
- structured failure diagnosis and analysis inclusion/exclusion.

Never delete failed or invalidated records.

## Experiment decision log

For every diagnosis or controlled change, record a decision ID, input run/evidence IDs, failure category, minimal reproduction, root cause, exactly what factor changed, outcome, next action, reopened stages, reason, and impacted artifact IDs.
