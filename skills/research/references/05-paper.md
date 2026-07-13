# Stage 5: Paper production and submission preparation

Assemble the manuscript from frozen evidence and claims.

## Establish the paper contract

Record the audience, contribution hierarchy, venue/format constraints, required sections, anonymization and ethics/artifact requirements, and release target. Route material evidence gaps upstream and reopen the relevant Gate when the manuscript needs a claim beyond the frozen contract.

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

Track edits in a paper change map with a stable ID, file/anchor, before/after summary, related claims/artifacts/citations, consistency targets, source-control reference, and verification status.

## Draft in dependency order

For a new paper, draft from methods and results toward framing and abstract; adapt to an existing manuscript without unnecessary restructuring.

- State the smallest defensible contribution delta against closest work.
- Keep terminology, notation, dimensions, dataset splits, metrics, counts, and numbers consistent across text, equations, tables, figures, appendices, and supplements.
- Use only wording allowed by the claim ledger and carry relevant boundary conditions and limitations.
- Cite external facts and prior methods and verify the exact support for each citation.

## Verify the deliverable

Before release:

1. reproduce every material number from registered analyses and artifacts;
2. audit claim wording, title, abstract, contribution list, conclusion, and limitations against the ledger;
3. audit terminology, notation, citations, cross-references, figures, tables, captions, appendix, and supplement;
4. compile through the complete bibliography pipeline and retain logs;
5. inspect the rendered output, not only source text;
6. check venue limits, required sections, anonymization, acknowledgments, metadata, ethics, and artifact requirements;
7. verify every paper change and promised check.

Register `paper.manuscript`, `claim_map`, `change_map`, `bibliography_provenance`, `compilation_log`, `rendered_output`, `render_inspection_record`, and `submission_checklist` for the release package.

## Request external release

Gate approval binds exact artifact paths and hashes. Use release target `initial_submission` here; `revision_rebuttal` belongs to the revision package.

After explicit human approval for the named target, record `release` through `researchctl`. External submission still requires that authority. Reopen release after a material bound-artifact change or failed consistency, citation, number, render, or anonymization check.
