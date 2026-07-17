# Stage 4: Experiment and result lifecycle

Treat experiments as registered prediction tests and analysis as calibrated claim assessment.

## Use stable working artifacts and append-only records

Maintain the canonical experiment-result roles under the shared `policy.artifact_layout` contract; keep this reference focused on their scientific content and update sequence.

Keep the run registry and decision log append-only. Every line needs a unique `record_id`; a metadata correction or revised judgment appends a new record with `supersedes: <prior-record-id>` and a reason. Never delete or overwrite failed, null, negative, excluded, cancelled, preempted, pruned, or contradictory outcomes. Analyses and their corrections follow the same stable-ID and supersedes discipline in the analysis registry.

## Design the experiment contract

For every experiment, record:

```yaml
record_id: EXP-SPEC-RECORD-001
supersedes: null
experiment_id: EXP-001
method_id: METHOD-001
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
  record_id: ANALYSIS-PLAN-RECORD-001
  supersedes: null
  plan_id: ANALYSIS-PLAN-001
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

Derive repetitions, uncertainty, effect thresholds, tests, and evaluation protocols from the data-generating process and resources. Register the experiment matrix before execution so the approved design has an immutable revision and hash. Later design or analysis changes update the same working file, append a decision record, register the next revision, and label the change exploratory with reasons when appropriate.

## Execute progressively

Progress from integrity and smoke checks to baseline parity, the minimal mechanism, and the approved primary comparison. Give each phase an explicit attempt budget, fallback, and stop condition derived from the experiment contract; do not hard-code universal parity or variance thresholds. Add ablations, robustness, generalization, safety, scaling, or efficiency tests only when they resolve a registered claim or risk.

When a declared Reference Stack executes or imports work, persist its operation through the shared `adapter_exchange` contract, verify the registered request for each new attempt, then append and register that attempt's first `accepted` receipt before any side effect. Only after this durable journal exists may the Adapter execute the bound immutable inputs; append later observations as superseding receipts. A receipt complements but never replaces the scientific run registry: retain `unknown` outcomes, reconcile the same attempt before a non-idempotent retry, and make every later scientific inclusion or exclusion judgment in the canonical experiment artifacts.

The shipped `scripts/reference_stack.py` is one narrow Reference Stack for deterministic isolated commands. Build its exact registered JSON payload from `assets/reference-stack-payload.template.json`, list every non-payload Adapter Request input exactly once in `materials`, and use `adapter request-append` before invoking it. Every declared publish path must be new and live under `.research/artifacts/<stage>/reference-stack/<attempt-id>/`; a retry reuses stable artifact IDs but gets another attempt directory. It runs declared tool probes and command steps without a shell in a clean temporary directory, rechecks materialized inputs, and submits all present outputs, logs, and its result to one no-clobber `artifact publish-batch` before preserving the terminal receipt. Child output reaches a bounded parent-owned pipe and unnamed log spool; the log is frozen outside the command sandbox after process-group cleanup, and Core rejects the entire batch unless every source still matches its first observed Hash and size. State advances only after every final and snapshot verifies. A failure never auto-deletes those paths because portable conditional unlink is unavailable; any confirmed unregistered attempt orphan must be reconciled before retry. Its network field is a retained declaration, not an enforced sandbox; its result is mechanical execution provenance, not the deferred Run Contract, scientific identity, inclusion judgment, or evidence that the experiment is sufficient. Use a separately declared conforming Adapter when a cluster, tracker, hardware system, or stronger isolation boundary is required.

## Register every run

Append one immutable run record per attempt, including:

- unique `record_id` and `run_id`, `supersedes: null`, lineage, time, operator, execution state, and scientific outcome;
- experiment and method IDs plus the exact registered revisions and hashes used;
- repository/commit/dirty patch, exact command, configuration, and randomization;
- data/split/environment/simulator, hardware/runtime, and dependency/container identity;
- output IDs, paths, checksums, logs, metrics, failure diagnosis, and inclusion/exclusion decision with reason.

Distinguish technical failures from negative or falsifying outcomes and retain every attempt. If a run record's metadata was wrong, append a correction with a new record ID, the same run ID, `supersedes` pointing to the prior record, and an explicit correction reason; never mutate the original line.

### Classify retries by scientific identity

Treat a restart, resume, or recovery as a retry only when the research question, method parameters, and expected estimand remain unchanged. Execution parameters such as GPU count, mixed precision, batch size, and worker count may differ when the researcher judges them operational rather than scientific for this experiment and declares every difference. Append a new run record for every attempt; never replace the original attempt or hide its outcome.

Record at least:

```yaml
retry_of_run_id: RUN-001
retry_reason: ""
execution_differences:
  gpu_count: {before: null, after: null}
  mixed_precision: {before: null, after: null}
  batch_size: {before: null, after: null}
  workers: {before: null, after: null}
  other: []
all_execution_differences_declared: true
scientific_identity_justification:
  research_question_unchanged: true
  method_parameters_unchanged: true
  expected_estimand_unchanged: true
  rationale: ""
```

If an execution change instead alters a scientific variable, effective method parameter, evaluated population, or expected estimand, register a new experiment rather than a retry. The runtime may preserve these fields and lineage, but it does not mechanically prove scientific identity or the completeness and truth of the declaration. Include all original and retry attempt run IDs when the underlying experiment is cumulatively reviewed.

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

After a run or sweep, export the resolved configuration, summary, required metric history, and artifact manifest to stable local files and record their hashes. Register the sweep search space, objective, budget, scheduler, and stop rule before launch; update the same working specification and register its next revision when a material change is authorized. Retain failed, cancelled, preempted, and pruned trials. Never persist tracker credentials in research artifacts. If W&B is unavailable, an existing tracker or `backend: local` is valid when the same local provenance contract is satisfied; physical experiments may instead link sample batches, instruments, raw media, and anomaly records.

## Diagnose controlled changes

Classify failures, reproduce the smallest failing case, and vary one causal factor where practical. Log the rationale, outcome, next action, impacted artifacts, and any reopened stage. Return upstream when an assumption or mechanism fails; stop when a kill criterion is met.

## Run a cumulative experiment review

After every non-retry experiment reaches a terminal outcome, run a cumulative review before planning or recommending the next experiment. Until that review exists, limit work to recording the outcome, diagnosing it, or recovering the same experiment; do not propose a new experiment or parameter change. A declared restart, resume, or infrastructure retry under the same scientific experiment identity does not add a separate review boundary, but the underlying experiment still requires one review when it produces a terminal scientific outcome or is abandoned.

Read the current canonical revisions of the experiment matrix, run registry, decision log, analysis registry, artifact manifest, and claim ledger. Treat the current append-only registries as the complete index: include every relevant non-superseded successful, failed, null, negative, excluded, cancelled, preempted, pruned, and contradictory record rather than reasoning from only the latest run. Read historical snapshots or raw outputs only when resolving a correction, conflict, or audit question. Before offering the next experiment, verify that the latest relevant terminal run IDs appear in the newest cumulative review's `reviewed_run_ids`.

Append one lightweight cumulative review record to the decision log; never rewrite an earlier review merely because the direction changed. Link the prior review for chronology, and use `supersedes` only when correcting an erroneous review record. A review should contain at least:

```yaml
record_id: EXP-REVIEW-RECORD-001
supersedes: null
review_id: EXP-REVIEW-001
previous_review_id: null
reviewed_run_ids: []
reviewed_analysis_ids: []
current_direction_judgment: ""
supporting_evidence_ids: []
opposing_evidence_ids: []
eliminated_directions:
  - direction_id: ""
    reason: ""
    evidence_ids: []
unresolved_risks: []
decision: continue # continue | adjust | return_upstream | stop
next_experiment:
  experiment_id: ""
  question: ""
  bounded_change: ""
  rationale: ""
  expected_observation: ""
  falsifying_observation: ""
stop_conditions: []
```

Base the direction judgment on the full reviewed set and identify which new evidence changed or preserved the prior judgment. Keep the next experiment as the smallest discriminating recommendation, not as an approval or a claim. If the review changes the selected method, a material experiment contract, or the permitted claim boundary, apply the earliest affected policy Gate's `reopen_when_changed` contract. Register the updated decision log, and update and register the claim ledger separately when claim status, scope, evidence, or allowed wording changes.

This is a mandatory Skill-level planning boundary, not background automation: registering an artifact, generating the Dashboard, or running a Hook does not invoke the model or create the review record automatically.

## Audit before analysis

Audit inclusion/exclusion, provenance, tracker-to-local identity mapping where used, metrics, statistical unit, missingness, dependence, failure handling, leakage, and test-set use before claim promotion. Distinguish pre-specified from exploratory analysis. Choose summaries and tests from the data-generating process; where material, report effect size and uncertainty and handle pairing, repeated measures, multiplicity, distributional assumptions, censoring, and missingness.

## Generate traceable result artifacts

Every table cell and plotted value must resolve to:

1. an analysis ID and exact analysis code/configuration;
2. included and excluded run IDs with reasons;
3. the statistical unit and estimand;
4. a checksummed output artifact.

State aggregation, uncertainty, and independent repetition count in captions. Maintain one stable working artifact for each canonical role `experiment_matrix`, `run_registry`, `decision_log`, `analysis_registry`, `artifact_manifest`, and `claim_ledger` under `experiment_results`; register their current revisions instead of duplicating or hand-versioning the files. Use a registered manifest for directories, oversized artifacts, and large output collections.

## Promote claims conservatively

Start each claim as `unassessed`, then assign one status:

- `supported`: registered evidence supports the wording;
- `bounded`: support holds only under stated conditions;
- `exploratory`: post-hoc or flexible analysis needs confirmation;
- `unsupported`: evidence is inadequate for an affirmative claim;
- `contradicted`: registered evidence conflicts with the claim.

Maintain entries such as:

```yaml
record_id: CLAIM-RECORD-001
supersedes: null
claim_id: CLAIM-001
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

When claim wording, scope, evidence, or disposition changes, append or retain a superseding claim record with a new `record_id`; do not erase the prior claim assessment.

## Hand off for claim freeze

Use `policy.stages.experiment_results.exit_criteria` and `policy.gates.claim_freeze`, including its current `approval_modes`, as the sole completion, required-role, waiver, and reopen contract. After explicit human approval, record the policy-selected mode through `researchctl` with the required decision review fields. Load `references/retrospective-revision-import.md` only when the user explicitly requests that policy mode and confirms both eligibility facts.
