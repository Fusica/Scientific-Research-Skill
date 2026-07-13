# Paper traceability

Use two artifacts with distinct responsibilities.

## Paper claim map

One entry per central manuscript claim or material numerical statement:

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

## Paper change map

One entry per actual edit. Link its `change_id` to stable
`paper_claim_id` placement IDs and scientific claim IDs, file and anchor,
before/after summary, evidence/run/analysis/artifact IDs, citations,
consistency targets, source-control ref, and verification status.

Use manuscript anchors when possible; line numbers drift. Source-control diffs remain the authoritative proof of what changed.

Before release, audit title/abstract/contributions, terminology and notation, every figure/table value and caption, bibliography support, anonymization, venue limits, and the rendered PDF. Gate 4 must bind the released manuscript artifacts and hashes.
