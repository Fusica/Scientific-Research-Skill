---
name: paper-production
description: Plan, draft, revise, and verify an academic paper from approved claims, method contracts, literature evidence, and reproducible artifacts. Use when defining paper architecture, writing or improving sections, managing citations and LaTeX, integrating figures and tables, checking manuscript consistency, or preparing a CS or robotics submission.
---

# Paper Production

Build the manuscript from frozen evidence and claims. Clear prose cannot substitute for missing scientific support.

## Establish the paper contract

Read the venue profile, approved idea card, closest-work comparison, method contract, experiment artifacts, and frozen claim ledger. Define the audience, contribution hierarchy, page constraints, required sections, anonymization rules, and artifact policy.

## Map claims before drafting

Create a section-level `paper_claim_map.yaml`. Assign every central claim to
one primary manuscript location and link it to evidence, analyses,
figures/tables, limitations, and citations. Track actual edits separately in
`paper_change_map.yaml`. Use `references/paper-traceability.md`.

## Draft in dependency order

A practical order is methods and experiments, results, related work, introduction, discussion/limitations, conclusion, then abstract. Adapt when the existing manuscript already establishes a stable structure.

- State contribution deltas against the closest work precisely.
- Keep notation, terminology, dataset splits, metrics, and numbers consistent across text, equations, tables, and appendices.
- Cite external facts and prior methods; do not attach citations to authors' own results as if they were external evidence.
- Never invent bibliographic metadata or claim a source supports text that was not verified.
- Use bounded wording from the claim ledger and include relevant limitations.

## Verify the deliverable

Compile the paper, resolve citations and cross-references, inspect the rendered document, check venue constraints, run consistency searches, and verify that each number and artifact traces backward. Preserve double-blind safety where required.

## Deliver artifacts

Maintain `paper_claim_map.yaml`, `paper_change_map.yaml`, bibliography
provenance, compilation logs, and a submission checklist. If evidence is
missing, route the gap to the relevant stage instead of drafting around it.
Require Gate 4 human approval before an initial external submission; initial
submission does not depend on a reviewer-revision stage.
