# Stage 1: Idea generation and freeze

Produce a falsifiable, evidence-grounded idea whose central mechanism and evaluation cost are explicit. Use scores only to organize judgment; never treat them as novelty evidence.

## Build the research brief

Record:

- problem, scientific setting, target user or system, and why it matters;
- available data, code, simulators, hardware, compute, time, and human expertise;
- target contribution type and expected venue or audience;
- domain constraints, safety limits, known prior work, and missing inputs.

Mark each entry as verified, assumed, or unknown and link verified entries to source or artifact IDs.

## Generate mechanism-distinct candidates

Generate three to seven candidates that differ in scientific mechanism or defensible claim, not merely in naming. Assign each candidate a stable idea ID and preserve rejected candidates.

For every candidate specify:

- research question and proposed mechanism;
- expected contribution and smallest plausible delta from closest work;
- testable predictions and falsifying outcomes;
- minimum evidence and simplest competitive baseline;
- feasibility, resource cost, risks, boundary conditions, and kill criteria;
- value of a null or negative result.

## Iterate with literature evidence

Use the literature stage to search for the mechanism, closest claim, alternate terminology, contradictory findings, and simpler explanations. Revise the candidate after each material discovery. Reuse the stable idea ID when refining wording or scope; increment its version when the mechanism or central claim changes.

Do not call an idea novel from model intuition, a title, an abstract, popularity, repository stars, or citation volume. State novelty as a confidence-bounded comparison against the evidence actually read.

## Stress-test independently

Apply at least three lenses:

1. a domain expert checking scientific value and assumptions;
2. a skeptical reviewer checking novelty, confounds, and overclaim;
3. an implementation and experiment owner checking feasibility and falsifiability.

When comparing candidates, vary their order and test whether a simpler baseline, alternate terminology, dataset artifact, or evaluation choice could explain the expected improvement.

Compare interestingness, evidence-backed novelty, feasibility, testability, expected impact, safety, and failure risk. Attach confidence and evidence IDs to every judgment. Prefer qualitative bands or ranges over false numerical precision.

## Prepare the idea artifact

Maintain a versioned idea card with at least:

```yaml
artifact_id: IDEA-CARD-001
artifact_version: 1
content_hash: null
idea_id: IDEA-001
idea_version: 1
status: candidate
gate_ref: idea_freeze
research_question: ""
mechanism: ""
smallest_defensible_difference: ""
closest_work_evidence_ids: []
claim_candidates:
  - claim_candidate_id: CLAIM-CAND-001
    text: ""
    required_evidence: []
predictions:
  - prediction_id: PRED-001
    observable: ""
    falsifying_outcome: ""
    baseline_or_intervention: ""
    boundary_conditions: []
feasibility: {data: "", code: "", compute: "", hardware: "", time: ""}
risks: []
kill_criteria: []
open_questions: []
recommendation: revise
```

Allow `freeze`, `revise`, or `stop` as recommendations. Keep approval only in `.research/state.json`; the idea artifact carries a Gate reference but cannot approve itself.

## Request Gate approval

Request `idea_freeze` only when:

- closest work and the smallest defensible difference are explicit;
- the central claim is falsifiable within resources;
- predictions, baselines, boundary conditions, risks, and kill criteria are recorded;
- rejected candidates and contradictory evidence remain visible.

Use `researchctl gate approve idea_freeze --reason "..."` only after explicit human approval and bind the decision to the artifact ID, version, and content hash. Reopen the Gate if closer work, feasibility evidence, the mechanism, or the central claim changes materially.
