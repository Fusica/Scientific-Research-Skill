# Stage 1: Idea generation and freeze

Build a portfolio of falsifiable, evidence-grounded candidates, compare them honestly, and select one mainline only when the evidence is sufficient. Scores may organize judgment but cannot replace it.

## Build the research brief

Record the problem and setting, target user or system, contribution and audience, available data/code/compute/hardware/time/expertise, known work, constraints, safety limits, and missing inputs. Mark each item as verified, assumed, or unknown; link verified items to evidence or artifact IDs.

## Maintain one candidate portfolio

Keep one canonical Markdown artifact, normally `.research/artifacts/idea/idea-portfolio.md`, with a stable artifact ID and preferably one working path. Generate three to seven candidates that differ in mechanism or defensible claim and maintain them in that artifact rather than creating `idea-v2`, `idea-v3`, or one workflow branch per candidate.

Give every candidate a stable ID and an optional `parent_id`. Use a new candidate ID when a revision changes the central mechanism or contribution object; use `parent_id` to preserve its lineage. Keep the same ID for bounded clarification of scope or wording. Never delete a rejected or falsified candidate.

Use exactly these candidate dispositions:

- `active`: still being developed or tested;
- `shortlisted`: remains a serious finalist;
- `selected`: chosen as the proposed mainline for Gate review;
- `rejected`: not selected for a recorded scientific, feasibility, safety, or scope reason;
- `falsified`: contradicted by retained evidence or a recorded kill criterion.

For every candidate record:

- research question, mechanism, expected contribution, and smallest plausible delta from closest work;
- testable predictions, falsifying outcomes, minimum evidence, and simplest competitive baseline;
- supporting and opposing evidence IDs, including contradictory or null evidence;
- feasibility, resource cost, risks, boundary conditions, and kill criteria;
- value of a null or negative result;
- status, parent lineage, and the selection, rejection, or falsification reason.

## Iterate with literature evidence

Use the literature stage to test mechanisms, closest claims, alternate terminology, contradictory findings, and simpler explanations. Update candidate records in place and preserve prior decisions through their evidence links and lineage. State novelty only as a confidence-bounded comparison against evidence actually read.

If new evidence removes the selected candidate's smallest defensible difference or feasibility, change its disposition honestly. Before `idea_freeze`, another shortlisted candidate may be selected after updating the comparison and reasons. After approval, apply `policy.gates.idea_freeze.reopen_when_changed` before changing the mainline.

## Stress-test independently

Apply three lenses:

1. a domain expert checking scientific value and assumptions;
2. a skeptical reviewer checking novelty, confounds, and overclaim;
3. an implementation and experiment owner checking feasibility and falsifiability.

Vary candidate order and test simpler baselines, alternate terminology, dataset artifacts, and evaluation choices. Compare scientific value, evidence-backed novelty, feasibility, testability, impact, safety, and failure risk; attach confidence and evidence IDs to judgments.

## Prepare the idea portfolio

The stable working artifact should contain at least:

```yaml
artifact_id: IDEA-PORTFOLIO-001
gate_ref: idea_freeze
research_brief_ref: BRIEF-001
literature_evidence_base_ref: EVIDENCE-BASE-001
candidates:
  - idea_id: IDEA-001
    parent_id: null
    status: active
    research_question: ""
    mechanism: ""
    smallest_defensible_difference: ""
    closest_work_evidence_ids: []
    supporting_evidence_ids: []
    opposing_evidence_ids: []
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
    disposition_reason: ""
comparison_summary: ""
selected_idea_id: null
selection_reason: ""
open_questions: []
```

Exactly one internal candidate may be `selected` when requesting the Gate, and `selected_idea_id` must match it. The runtime does not parse this scientific content or decide whether the choice is sound; that remains a human review responsibility.

## Hand off for approval

Use `policy.stages.idea.exit_criteria` and `policy.gates.idea_freeze` as the sole completion, required-role, selection, and reopen contract; the checks above describe scientific review, not a second Gate definition. Register the roles named there and, after explicit human approval, record `researchctl gate approve idea_freeze --selected-id IDEA-003 --reason "..."` with the policy-required decision review fields.
