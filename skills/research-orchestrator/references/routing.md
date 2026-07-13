# Routing rules

## Choose one primary stage

| User intent or observed state | Primary skill |
| --- | --- |
| Explore, judge, or improve a research direction | `$idea-evolution` |
| Find papers, verify claims, or determine closest work | `$literature-evidence` |
| Define equations, algorithms, assumptions, or interfaces | `$method-formalization` |
| Plan, run, debug, tune, or ablate experiments | `$experiment-lifecycle` |
| Aggregate results, perform statistics, or decide claims | `$result-synthesis` |
| Draft, revise, compile, or audit the manuscript | `$paper-production` |
| Respond to editors/reviewers or verify promised changes | `$review-revision` |

Use multiple skills only when their artifacts form a direct handoff. Do not invoke the entire pipeline for a bounded request.

## Stage precedence

1. Resolve missing or contradictory evidence before formalizing a central claim.
2. Resolve method ambiguity before expensive experiment design.
3. Resolve run provenance before statistical synthesis.
4. Freeze claims before optimizing manuscript rhetoric.
5. Apply and verify manuscript changes before marking a reviewer response ready.

## Existing projects

Prefer the user's current artifacts and repository conventions. Map them to the canonical contract instead of duplicating files. Record the mapping in `project-state.yaml`.

## Parallel work

Parallelize only independent searches, audits, implementations, or analyses. Assign non-overlapping write scopes and define the merge artifact. Scientific decisions still pass through a single evidence record and human gate.
