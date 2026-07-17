# Stage 1: Idea generation and freeze

Build a portfolio of falsifiable, evidence-grounded candidates, compare them honestly, and select one mainline only when the evidence is sufficient. Scores may organize judgment but cannot replace it.

## Build the research brief

Record the problem and setting, target user or system, contribution and audience, available data/code/compute/hardware/time/expertise, known work, constraints, safety limits, and missing inputs. Mark each item as verified, assumed, or unknown; link verified items to evidence or artifact IDs.

## Maintain one candidate portfolio

Keep one canonical Markdown artifact, normally `.research/artifacts/idea/idea-portfolio.md`, with a stable artifact ID and preferably one working path. Generate three to seven candidates that differ in mechanism or defensible claim and maintain them in that artifact rather than creating `idea-v2`, `idea-v3`, or one workflow branch per candidate.

Before generation, declare a bounded attempt budget for seed passes, candidate count, refinement, pairwise review, tools or retrieval, and a stopping condition. Budget exhaustion is a retained outcome: record what was attempted, the best unresolved candidates, duplicate or failed directions, open risks, and the stopping reason rather than silently extending the search.

Give every candidate a stable ID and `parent_ids`. Seeds use an empty list. Use a new candidate ID when a revision changes the central mechanism or contribution object; retain every parent when a new candidate transfers, mutates, recombines, subtracts from, or pivots away from prior mechanisms. Keep the same ID for bounded clarification of scope or wording. Never delete a duplicate, rejected, falsified, repaired, or budget-exhausted candidate or attempt.

`duplicate`, `repaired`, and `budget_exhausted` describe findings or attempt outcomes, not additional candidate dispositions. The candidate still uses one of the five dispositions below; for example, retain a duplicate as `rejected` with the matching candidate and reason.

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
- status, multi-parent lineage, generation pass and variation operator, and the selection, rejection, or falsification reason;
- repair or failure feedback, including the evidence, boundary, and resulting child candidate when one exists.

## Generate independent seeds and typed variations

Run at least three seed passes against the same brief, evidence base, and resource constraints before showing any pass the peer candidates or generator rationale. Record a pass as `isolated` only when its context actually excludes them. If the host cannot provide that isolation, record `declared_nonisolated`; sequential prompting or a different persona name alone is not evidence of independence.

After merging and marking duplicates without deleting them, derive bounded variations with exactly these operator labels:

- `transfer`: move a supported mechanism across a defensible domain boundary;
- `assumption_mutation`: change one material assumption and expose the consequence;
- `mechanism_recombination`: combine mechanisms and retain every parent ID;
- `subtraction`: remove a component or claim to test the smallest sufficient idea;
- `pivot`: replace a failed mechanism while preserving the still-supported question or boundary.

For every variation, record its parent IDs, expected gain, new risk, evidence, outcome, and repair or failure feedback. An optional `record_manifest` may represent multiple parents with multiple existing `derived_from` relations. The runtime validates those declared links, not whether the variation is scientifically meaningful or complete.

## Iterate with literature evidence

Use the literature stage to test mechanisms, closest claims, alternate terminology, contradictory findings, and simpler explanations. Update candidate records in place and preserve prior decisions through their evidence links and lineage. State novelty only as a confidence-bounded comparison against evidence actually read.

If new evidence removes the selected candidate's smallest defensible difference or feasibility, change its disposition honestly. Before `idea_freeze`, another shortlisted candidate may be selected after updating the comparison and reasons. After approval, apply `policy.gates.idea_freeze.reopen_when_changed` before changing the mainline.

## Compare and stress-test independently

Pairwise comparison is budget-aware, but every scheduled pair must be presented in both left-right positions. Record both presentation orders, the reviewer context, Novelty, Feasibility, Relevance, and Clarity judgments, evidence IDs, flaws, winner or tie, and any disagreement caused by position. Give every active candidate comparison coverage and state incomplete coverage or unresolved comparison cycles explicitly; do not treat an internal score as novelty evidence.

Apply three adversarial lenses to active candidates, and apply all three to finalists:

1. a domain and closest-prior-work reviewer checking scientific value, assumptions, and prior-art collision;
2. a falsifiability reviewer checking confounds, causal breaks, evidence gaps, and overclaim;
3. an implementation, resource, and safety reviewer checking feasibility, testability, and kill criteria.

Where the host supports it, keep these reviewers isolated from generator rationale and record the actual context boundary; otherwise declare the limitation. Test simpler baselines, alternate terminology, dataset artifacts, and evaluation choices. For every flaw, retain its target, evidence, severity, disposition, repair child where applicable, and whether it exposed a duplicate, rejection reason, falsification, evidence gap, or changed boundary. A repair never erases the original candidate or finding.

Keep the selector's internal top one, top three, reasons, comparison coverage, and ordering limitations as a recommendation only. The selector cannot establish novelty, mark a Gate approved, or replace the human-selected candidate ID. Related warm cycles may reuse registered portfolio history, evidence, failures, and project navigation hints only inside the same paper-bound workspace mainline. `.research/memory.md` is not scientific evidence or Gate authority, and this protocol introduces no Codex-global or implicit cross-workspace memory.

## Prepare the idea portfolio

The stable working artifact should contain at least:

```yaml
artifact_id: IDEA-PORTFOLIO-001
gate_ref: idea_freeze
research_brief_ref: BRIEF-001
literature_evidence_base_ref: EVIDENCE-BASE-001
innovation_protocol:
  protocol_version: "1.0"
  attempt_budget:
    seed_passes: 3
    max_candidates: 7
    max_refinement_rounds: null # set a finite project limit before generation
    max_pairwise_presentations: null # set a finite project limit before generation
    max_tool_calls: null # set a finite project limit before generation
    max_total_tokens: null # set a finite project limit before generation
    stopping_condition: ""
  seed_passes:
    - pass_id: SEED-PASS-001
      isolation: isolated # isolated | declared_nonisolated
      visible_inputs: [BRIEF-001, EVIDENCE-BASE-001]
      produced_candidate_ids: [IDEA-001]
    - pass_id: SEED-PASS-002
      isolation: isolated
      visible_inputs: [BRIEF-001, EVIDENCE-BASE-001]
      produced_candidate_ids: [IDEA-002]
    - pass_id: SEED-PASS-003
      isolation: isolated
      visible_inputs: [BRIEF-001, EVIDENCE-BASE-001]
      produced_candidate_ids: [IDEA-003]
  pairwise_comparisons:
    - comparison_id: PAIR-001
      candidate_ids: [IDEA-001, IDEA-002]
      reviewer_context: ""
      position_swap:
        - presentation_id: PAIR-001-A
          left_id: IDEA-001
          right_id: IDEA-002
          judgments: {novelty: "", feasibility: "", relevance: "", clarity: ""}
          evidence_ids: []
          flaws: []
          result: tie
        - presentation_id: PAIR-001-B
          left_id: IDEA-002
          right_id: IDEA-001
          judgments: {novelty: "", feasibility: "", relevance: "", clarity: ""}
          evidence_ids: []
          flaws: []
          result: tie
      order_disagreement: ""
      adjudication: null
  adversarial_reviews:
    - review_id: ADV-001
      lens: closest_prior_work
      isolation: isolated
      target_candidate_ids: [IDEA-001]
      evidence_ids: []
      findings: []
candidates:
  - idea_id: IDEA-001
    parent_ids: []
    generation_pass_id: SEED-PASS-001
    variation:
      operator: null # transfer | assumption_mutation | mechanism_recombination | subtraction | pivot
      expected_gain: ""
      new_risk: ""
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
    repair_or_failure_feedback:
      finding_ids: []
      outcome: ""
      repair_candidate_ids: []
    disposition_reason: ""
comparison_summary: ""
selector_recommendation:
  top_1_candidate_id: null
  top_3_candidate_ids: []
  comparison_coverage: ""
  ordering_limitations: []
selected_idea_id: null
selection_reason: ""
open_questions: []
```

Exactly one internal candidate may be `selected` when requesting the Gate, and `selected_idea_id` must match it. `selector_recommendation` is not Gate approval and does not itself change any candidate disposition. The runtime does not parse this scientific content, prove context isolation, or decide whether the choice is sound; those remain human review responsibilities.

## Hand off for approval

Use `policy.stages.idea.exit_criteria` and `policy.gates.idea_freeze` as the sole completion, required-role, selection, and reopen contract; the checks above describe scientific review, not a second Gate definition. Register the roles named there and, after explicit human approval, record `researchctl gate approve idea_freeze --selected-id IDEA-003 --reason "..."` with the policy-required decision review fields.
