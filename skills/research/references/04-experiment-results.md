# Stage 4: Experiment and result lifecycle

Treat experiments as registered tests of explicit predictions and treat analysis as calibrated claim assessment. Do not search for favorable numbers or let analysis repair invalid provenance.

## Design the experiment contract

Read the approved method contract, idea artifact, literature evidence, domain profile, and resource and safety constraints. For every experiment record:

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

Derive repetitions, uncertainty, effect thresholds, tests, and evaluation protocols from the data-generating process, domain evidence, and available resources. Do not import generic seed counts or significance thresholds without justification.

Lock the experiment matrix with an artifact ID, version, and hash before execution. Label any later unregistered analysis or design change as exploratory and record why it changed.

## Execute progressively

Use the smallest stage that can invalidate the next expensive step:

1. Check data, environment, metrics, leakage, interfaces, and deterministic smoke behavior.
2. Reproduce baselines and evaluation parity.
3. Test the minimal proposed mechanism.
4. Run the approved primary comparison.
5. Isolate claimed mechanisms with ablations or causal probes.
6. Test robustness, generalization, safety, scaling, or efficiency only where relevant to a claim.

Obtain required authority before costly compute, destructive data operations, physical hardware, safety-relevant execution, or irreversible actions.

## Register every run

Write one immutable run record per attempt. Include:

- run ID, parent or superseded run lineage, timestamps, and operator;
- experiment ID/spec version/hash and method contract ID/version/hash;
- separate execution status and scientific outcome;
- repository, commit, dirty patch artifact/hash, exact command, arguments, and working directory;
- resolved configuration artifact/hash and randomization or seed record;
- profile, data, split, environment, simulator, and version/hash identifiers;
- hardware, OS, dependency lock/hash, container digest, and runtime;
- output artifact IDs, paths, checksums, logs, and metric records;
- structured failure diagnosis and analysis inclusion/exclusion decision with reason.

Never overwrite, delete, or silently replace failed or invalidated runs. Never retain only the best seed. A technical failure is distinct from a negative or falsifying scientific outcome.

## Diagnose controlled changes

Classify failures as data, implementation, optimization, evaluation, resource, safety, or hypothesis failures. Reproduce the smallest failing case, change one causal factor where practical, and log the rationale, outcome, next action, reopened stages, and impacted artifact IDs.

Return to method or idea work when results invalidate an assumption or mechanism. Do not continue open-ended tuning after a kill criterion is met.

## Audit before analysis

Verify included and excluded runs against the registered criteria. Check provenance, metric definitions, statistical unit, missingness, failure handling, dependence, data leakage, and test-set usage. Stop claim promotion when provenance or statistical-unit validity is insufficient.

Distinguish pre-specified from exploratory analysis. Choose summaries and tests from the data-generating process. Where meaningful, report effect sizes and uncertainty intervals and address repeated measures, pairing, multiple comparisons, non-normality, censoring, missingness, and high variance. Show distributions or paired changes when averages hide important behavior.

Preserve null, negative, and conflicting outcomes. Do not infer causal mechanism from an uncontrolled aggregate comparison.

## Generate traceable result artifacts

Every table cell and plotted value must resolve to:

1. an analysis ID and exact analysis code/configuration;
2. included and excluded run IDs with reasons;
3. the statistical unit and estimand;
4. a checksummed output artifact.

State aggregation, uncertainty representation, and independent repetition count in captions. Avoid visual encodings or decimal precision that imply unsupported certainty.

Maintain canonical equivalents of an experiment matrix, run registry, decision log, analysis registry, artifact manifest, and claim ledger. Map existing files to these roles instead of duplicating them.

## Promote claims conservatively

Start every new claim as `unassessed`. After the audit, assign exactly one status:

- `supported`: adequate registered evidence directly supports the wording;
- `bounded`: support holds only under stated settings or assumptions;
- `exploratory`: flexible or post-hoc analysis observed it and confirmation is needed;
- `unsupported`: evidence is inadequate for an affirmative manuscript claim;
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

Link every analysis ID to its run population, code/configuration, statistical unit, estimand, uncertainty method, and output artifacts. Link every figure/table ID to a checksummed manifest record.

## Request claim freeze

Separate missing experiments from writing improvements. Request `claim_freeze` only when the run population is auditable, all material outcomes are retained, analyses match the registered or clearly labeled exploratory plan, and each affirmative claim has calibrated wording and limitations.

Use `researchctl gate approve claim_freeze --reason "..."` only after explicit human approval. Reopen the Gate when run inclusion, the estimand, statistical unit, analysis, material evidence, or permitted wording changes.
