# Stage 3: Method formalization

Compare implementable methods for the approved idea, preserve their evidence and lineage, and formalize exactly one selected method into a testable contract.

## Establish scope and assumptions

From the approved idea and closest-work evidence, define:

- agents or systems, inputs, observations, latent variables, outputs, and constraints;
- observable scope, data-generating process, uncertainty sources, and success criteria;
- assumptions, exclusions, boundary conditions, and failure behavior;
- the exact contribution claim and what the method does not model.

Apply `policy.gates.idea_freeze.reopen_when_changed` if a proposed method changes the selected idea's central mechanism or contribution.

## Maintain one method portfolio

Keep one canonical Markdown approval package with a stable artifact ID, normally at `.research/artifacts/method/method-approval-package.md`. Maintain all serious method candidates and the selected method contract inside it rather than creating a chain of `method-v2` files or parallel workflow states.

Give every method candidate a stable `method_id` and optional `parent_id`. Create a new ID when the central objective, mechanism, or module composition changes materially; use the parent link to preserve lineage. Keep the same ID for bounded clarification or implementation detail that does not change the scientific mechanism.

Use exactly these dispositions: `active`, `shortlisted`, `selected`, `rejected`, or `falsified`. Never delete a rejected or falsified method. For every candidate record:

- scientific role, mechanism, dependencies, and relation to the selected Idea;
- assumptions, objective, modules, interfaces, shapes or units, and expected failure behavior;
- supporting and opposing evidence IDs, derivations, implementation findings, and unresolved unknowns;
- prediction IDs, cheapest discriminating test, stop conditions, and kill criteria;
- feasibility, resource and safety constraints;
- status, parent lineage, and the selection, rejection, or falsification reason.

Before `method_experiment_approval`, several candidates may remain active or shortlisted, but exactly one may be marked selected for Gate review. After approval, apply `policy.gates.method_experiment_approval.reopen_when_changed` before changing the selected method or its material contract.

## Decompose candidates into atomic concepts

For each material concept or module, record its candidate and scientific role, dependencies, definition, dimensions/domain, implementation interface, shapes, invariant, test, and expected failure behavior. Link non-obvious choices to evidence or derivations.

Maintain a bidirectional equation-to-code map for the selected method:

| Method ID | Concept ID | Equation ID | Meaning | Planned code symbol/path | Shapes or units | Test or invariant |
| --- | --- | --- | --- | --- | --- | --- |
| `METHOD-001` | `CONCEPT-001` | `EQ-001` | | | | |

## Specify objectives and algorithms

For each shortlisted method, define enough detail to compare its objective, constraints, optimization or inference, stochastic components, complexity, and feasibility. For the selected method, fully derive non-obvious transitions and align pseudocode with planned symbols and interfaces. Mark heuristics and state the support behind theorem-like, optimality, stability, safety, or guarantee language.

## Convert mechanisms into predictions

For every material claim candidate, create a stable prediction ID and specify its originating method ID, observable outcome, falsifying outcome, necessary baseline or intervention, boundary conditions, and planned experiment IDs.

| Prediction ID | Method ID | Origin claim candidate IDs | Observable | Falsifying outcome | Baseline or intervention | Boundary conditions | Experiment IDs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `PRED-001` | `METHOD-001` | `CLAIM-CAND-001` | | | | | |

Experiment rows must carry the method and prediction IDs they test.

## Prepare the approval package

The single stable working artifact should contain at least:

```yaml
artifact_id: METHOD-PORTFOLIO-001
gate_ref: method_experiment_approval
source_idea_id: IDEA-003
method_candidates:
  - method_id: METHOD-001
    parent_id: null
    status: active
    mechanism: ""
    supporting_evidence_ids: []
    opposing_evidence_ids: []
    assumptions: []
    modules: []
    interfaces: []
    prediction_ids: []
    feasibility: {code: "", compute: "", hardware: "", time: ""}
    risks: []
    kill_criteria: []
    disposition_reason: ""
selected_method_id: null
selection_reason: ""
selected_method_contract:
  scope_and_claim: ""
  problem_setting: ""
  assumptions_and_exclusions: []
  notation_shapes_units: []
  objectives_modules_interfaces: []
  training_or_estimation: ""
  inference_or_deployment: ""
  complexity_resources_safety: []
  equation_to_code_map: []
  prediction_to_experiment_map: []
  unresolved_questions_and_risks: []
experiment_contract:
  method_id: METHOD-001
  prediction_ids: []
  baselines: []
  metrics: []
  statistical_unit: ""
  repetitions_rationale: ""
  resources: {}
  safety_constraints: []
  stop_and_kill_criteria: []
```

Exactly one internal method candidate may be `selected`, and `selected_method_id` must match it. The runtime verifies the registered package revision and Gate structure, not the scientific correctness of the Markdown or whether the internal candidate is present.

## Hand off for approval

Use `policy.stages.method.exit_criteria` and `policy.gates.method_experiment_approval` as the sole completion, required-role, selection, and reopen contract; the package above is scientific execution guidance, not a second Gate definition. Register the roles named there and, after explicit human approval, record `researchctl gate approve method_experiment_approval --selected-id METHOD-002 --reason "..."` with the policy-required decision review fields.
