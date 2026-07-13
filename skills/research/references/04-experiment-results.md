# Stage 4: Experiment and result lifecycle

Treat experiments as registered prediction tests and analysis as calibrated claim assessment.

## Design the experiment contract

For every experiment, record:

```yaml
experiment_id: EXP-001
spec_version: 1
spec_hash: null
stage: baseline_reproduction
origin_claim_candidate_ids: [CLAIM-CAND-001]
prediction_ids: [PRED-001]
question: ""
hypothesis: ""
independent_variables: []
controls: []
baselines: []
data_or_environment: ""
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

Derive repetitions, uncertainty, effect thresholds, tests, and evaluation protocols from the data-generating process and resources. Version and hash the experiment matrix before execution; label later design or analysis changes as exploratory with reasons.

## Execute progressively

Progress from integrity and smoke checks to baseline parity, the minimal mechanism, and the approved primary comparison. Add ablations, robustness, generalization, safety, scaling, or efficiency tests only when they resolve a registered claim or risk.

## Register every run

Write one immutable run record per attempt, including:

- run identity, lineage, time, operator, execution status, and scientific outcome;
- experiment and method IDs, versions, and hashes;
- repository/commit/dirty patch, exact command, configuration, and randomization;
- data/split/environment/simulator, hardware/runtime, and dependency/container identity;
- output IDs, paths, checksums, logs, metrics, failure diagnosis, and inclusion/exclusion decision with reason.

Distinguish technical failures from negative or falsifying outcomes and retain every attempt.

## Diagnose controlled changes

Classify failures, reproduce the smallest failing case, and vary one causal factor where practical. Log the rationale, outcome, next action, impacted artifacts, and any reopened stage. Return upstream when an assumption or mechanism fails; stop when a kill criterion is met.

## Audit before analysis

Audit inclusion/exclusion, provenance, metrics, statistical unit, missingness, dependence, failure handling, leakage, and test-set use before claim promotion. Distinguish pre-specified from exploratory analysis. Choose summaries and tests from the data-generating process; where material, report effect size and uncertainty and handle pairing, repeated measures, multiplicity, distributional assumptions, censoring, and missingness.

## Generate traceable result artifacts

Every table cell and plotted value must resolve to:

1. an analysis ID and exact analysis code/configuration;
2. included and excluded run IDs with reasons;
3. the statistical unit and estimand;
4. a checksummed output artifact.

State aggregation, uncertainty, and independent repetition count in captions. Register the canonical roles `experiment_matrix`, `run_registry`, `decision_log`, `analysis_registry`, `artifact_manifest`, and `claim_ledger` under `experiment_results`; map existing files instead of duplicating them.

## Promote claims conservatively

Start each claim as `unassessed`, then assign one status:

- `supported`: registered evidence supports the wording;
- `bounded`: support holds only under stated conditions;
- `exploratory`: post-hoc or flexible analysis needs confirmation;
- `unsupported`: evidence is inadequate for an affirmative claim;
- `contradicted`: registered evidence conflicts with the claim.

Maintain entries such as:

```yaml
claim_id: CLAIM-001
claim_version: 1
origin_claim_candidate_ids: [CLAIM-CAND-001]
prediction_ids: [PRED-001]
experiment_ids: []
text: ""
status: unassessed
scope: {settings: [], assumptions: []}
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

## Request claim freeze

Prepare `claim_freeze` only when the run population is auditable, all material outcomes are retained, analyses are registered or labeled exploratory, and affirmative claims have calibrated wording and limitations.

After explicit human approval, record `claim_freeze` through `researchctl`. Reopen it when run inclusion, estimand, statistical unit, analysis, material evidence, or permitted wording changes.
