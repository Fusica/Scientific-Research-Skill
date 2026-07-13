# Stage 1: Idea generation and freeze

Define a falsifiable, evidence-grounded mechanism and its cheapest decisive evaluation. Use scores only to organize judgment.

## Build the research brief

Record the problem and setting, target user or system, contribution and audience, available data/code/compute/hardware/time/expertise, known work, constraints, safety limits, and missing inputs. Mark each item as verified, assumed, or unknown; link verified items to evidence or artifact IDs.

## Generate mechanism-distinct candidates

Generate three to seven candidates that differ in mechanism or defensible claim. Assign stable idea IDs and retain rejected candidates.

For every candidate specify:

- research question and proposed mechanism;
- expected contribution and smallest plausible delta from closest work;
- testable predictions and falsifying outcomes;
- minimum evidence and simplest competitive baseline;
- feasibility, resource cost, risks, boundary conditions, and kill criteria;
- value of a null or negative result.

## Iterate with literature evidence

Use the literature stage to test the mechanism, closest claim, alternate terminology, contradictory findings, and simpler explanations. Keep the idea ID for scope or wording refinements; increment its version when the mechanism or central claim changes. State novelty only as a confidence-bounded comparison against evidence actually read.

## Stress-test independently

Apply three lenses:

1. a domain expert checking scientific value and assumptions;
2. a skeptical reviewer checking novelty, confounds, and overclaim;
3. an implementation and experiment owner checking feasibility and falsifiability.

Vary candidate order and test simpler baselines, alternate terminology, dataset artifacts, and evaluation choices. Compare scientific value, evidence-backed novelty, feasibility, testability, impact, safety, and failure risk; attach confidence and evidence IDs to judgments.

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

Allow `freeze`, `revise`, or `stop` as recommendations. The artifact may reference the Gate but cannot approve itself; approval exists only in `.research/state.json`.

## Request Gate approval

Prepare `idea_freeze` only when:

- closest work and the smallest defensible difference are explicit;
- the central claim is falsifiable within resources;
- predictions, baselines, boundary conditions, risks, and kill criteria are recorded;
- rejected candidates and contradictory evidence remain visible.

After explicit human approval, record it with `researchctl gate approve idea_freeze --reason "..."`, bound to the artifact ID, version, and hash. Reopen the Gate when closer work, feasibility, the mechanism, or the central claim changes materially.
