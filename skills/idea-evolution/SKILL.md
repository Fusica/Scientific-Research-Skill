---
name: idea-evolution
description: Generate, challenge, compare, and refine research ideas using literature evidence, feasibility constraints, falsification tests, and human judgment. Use when brainstorming a new direction, testing novelty, improving a rough idea, selecting among candidate contributions, or deciding whether an idea is ready to freeze.
---

# Idea Evolution

Turn a research direction into a small set of evidence-grounded, falsifiable candidates. Scores organize judgment; they never establish novelty or scientific value by themselves.

## Build the research brief

Capture the problem, target users or scientific setting, available data/code/hardware, compute and time budget, relevant domain profile, desired contribution type, and known constraints. Label missing information explicitly.

## Generate distinct candidates

Create three to seven candidates that differ in mechanism or claim, not merely wording. For each candidate state:

- the problem and why it matters;
- the proposed mechanism or insight;
- the expected contribution and closest competing claim;
- the minimum evidence that could support it;
- likely failure modes, resource cost, and kill criteria.

## Run the evidence loop

Invoke `$literature-evidence` to find the closest work, conflicting evidence, and missing background. Revise candidates after each meaningful discovery. Do not call a candidate novel from title, abstract, citation count, or model intuition alone.

## Stress-test candidates

Use at least three independent lenses: domain expert, skeptical reviewer, and implementation/experiment owner. Randomize candidate order when doing pairwise comparisons and record the rationale. Test:

- whether the contribution already exists under different terminology;
- whether the claimed benefit follows from the proposed mechanism;
- whether a simpler baseline could explain the expected gain;
- whether the idea is falsifiable within available resources;
- whether negative results would still yield useful knowledge.

## Compare without false precision

Rate interestingness, evidence-backed novelty, feasibility, testability, expected impact, and risk. Attach confidence and evidence IDs to every rating. Use qualitative bands or ranges unless repeated independent evaluation justifies finer numbers.

## Produce the gate artifact

Write an approval-ready `idea_card.yaml` using `references/idea-card.md`, plus a short candidate comparison and rejection log. Recommend freeze, revise, or stop, but require the user to approve the idea freeze.

Assign a stable idea ID when the candidate is first recorded. Approval changes
its status but not its ID. Later changes that alter the main mechanism or
central claim create a new version and reopen literature and experiment
assumptions.
