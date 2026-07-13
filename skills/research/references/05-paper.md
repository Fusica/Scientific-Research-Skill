# Stage 5: Paper production and submission preparation

Assemble the manuscript from frozen evidence and claims. Treat prose quality as presentation, never as a substitute for scientific support.

## Establish the paper contract

Read the venue profile, approved idea, closest-work evidence, method contract, experiment and analysis artifacts, and frozen claim ledger. Record audience, contribution hierarchy, page and format constraints, required sections, anonymization rules, ethics or artifact requirements, and release target.

Route an evidence gap upstream rather than drafting around it. Reopen the relevant Gate when a necessary manuscript claim exceeds the frozen contract.

## Map claims before drafting

Assign every central claim and material numerical statement one primary manuscript location. Maintain a claim map entry such as:

```yaml
paper_claim_id: PAPER-CLAIM-001
claim_id: CLAIM-001
claim_version: 1
manuscript_locations:
  - {file: main.tex, anchor_or_section: sec:results}
role: primary_result
wording: ""
evidence_ids: []
run_ids: []
analysis_ids: []
artifact_ids: []
citation_keys: []
limitations_location: ""
consistency_targets: [appendix, abstract, conclusion]
verification:
  wording_within_ledger: pending
  numbers_reproduced: pending
  citations_verified: pending
  rendered_inspected: pending
```

Track actual edits separately in a paper change map. Give each change a stable ID and record file/anchor, before/after summary, related claims and artifacts, citations, consistency targets, source-control reference, and verification status. Prefer stable anchors to line numbers; use the source-control diff to prove what changed.

## Draft in dependency order

For a new paper, prefer methods and experiments, results, related work, introduction, discussion and limitations, conclusion, then abstract. Adapt to an existing manuscript rather than restructuring without need.

- State the smallest defensible contribution delta against closest work.
- Keep terminology, notation, dimensions, dataset splits, metrics, counts, and numbers consistent across text, equations, tables, figures, appendices, and supplements.
- Use only wording allowed by the claim ledger and carry relevant boundary conditions and limitations.
- Cite external facts and prior methods; do not cite the authors' own result as external evidence.
- Verify bibliographic metadata and the exact support for each citation.
- Never invent results, citations, source locations, or completed checks.

## Verify the deliverable

Before release:

1. reproduce every material number from registered analyses and artifacts;
2. audit claim wording, title, abstract, contribution list, conclusion, and limitations against the ledger;
3. audit terminology, notation, citations, cross-references, figures, tables, captions, appendix, and supplement;
4. compile through the complete bibliography pipeline and retain logs;
5. inspect the rendered output, not only source text;
6. check venue limits, required sections, anonymization, acknowledgments, metadata, ethics, and artifact requirements;
7. verify that every paper change and promised check has a completed record.

Maintain canonical equivalents of a paper claim map, paper change map, bibliography provenance, compilation logs, rendered-output inspection record, and submission checklist.

## Request external release

Gate approval must bind the exact manuscript and response artifact paths and content hashes. Use release target `initial_submission` for the first submission and `revision_rebuttal` for a revised manuscript and response package.

Use `researchctl gate approve release --reason "..."` only after explicit human approval for the named target. Never submit, upload, publish, or send externally merely because the files are ready. Reopen release approval after any material change to a bound artifact or any failed consistency, citation, number, render, or anonymization check.
