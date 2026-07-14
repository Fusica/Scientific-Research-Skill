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

Progress from integrity and smoke checks to baseline parity, the minimal mechanism, and the approved primary comparison. Give each phase an explicit attempt budget, fallback, and stop condition derived from the experiment contract; do not hard-code universal parity or variance thresholds. Add ablations, robustness, generalization, safety, scaling, or efficiency tests only when they resolve a registered claim or risk.

## Register every run

Write one immutable run record per attempt, including:

- run identity, lineage, time, operator, execution status, and scientific outcome;
- experiment and method IDs, versions, and hashes;
- repository/commit/dirty patch, exact command, configuration, and randomization;
- data/split/environment/simulator, hardware/runtime, and dependency/container identity;
- output IDs, paths, checksums, logs, metrics, failure diagnosis, and inclusion/exclusion decision with reason.

Distinguish technical failures from negative or falsifying outcomes and retain every attempt.

The local `experiment_results.run_registry` is the authoritative audit record. For computational experiments, prefer W&B when the project already supports it or the researcher selects it; use it for live metrics, groups, sweeps, artifacts, and collaboration, not as the only provenance store. Add optional tracker references to each run:

```yaml
tracker_refs:
  - backend: wandb
    entity: ""
    project: ""
    run_id: ""
    run_url: ""
    group: ""
    job_type: ""
    sweep_id: null
    export:
      path: ""
      content_hash: null
      exported_at: null
```

After a run or sweep, export the resolved configuration, summary, required metric history, and artifact manifest to stable local files and record their hashes. Version and hash sweep search spaces, objectives, budgets, schedulers, and stop rules before launch; retain failed, cancelled, preempted, and pruned trials. Never persist tracker credentials in research artifacts. If W&B is unavailable, an existing tracker or `backend: local` is valid when the same local provenance contract is satisfied; physical experiments may instead link sample batches, instruments, raw media, and anomaly records.

## Diagnose controlled changes

Classify failures, reproduce the smallest failing case, and vary one causal factor where practical. Log the rationale, outcome, next action, impacted artifacts, and any reopened stage. Return upstream when an assumption or mechanism fails; stop when a kill criterion is met.

## Audit before analysis

Audit inclusion/exclusion, provenance, tracker-to-local identity mapping where used, metrics, statistical unit, missingness, dependence, failure handling, leakage, and test-set use before claim promotion. Distinguish pre-specified from exploratory analysis. Choose summaries and tests from the data-generating process; where material, report effect size and uncertainty and handle pairing, repeated measures, multiplicity, distributional assumptions, censoring, and missingness.

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
