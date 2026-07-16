# Stage 5: Paper production and submission preparation

Assemble the manuscript from frozen evidence and claims.

## Keep stable working paths

Continue editing the repository's real manuscript source, claim map, change map, bibliography provenance, build log, render inspection record, and checklist under the shared `policy.artifact_layout` contract.

Use a small registered manifest for source directories, oversized files, figure collections, and other large outputs. The manifest must retain stable IDs, paths, checksums, and enough generation or verification information to audit the referenced material.

## Establish the paper contract

Record the audience, contribution hierarchy, venue/format constraints, required sections, anonymization and ethics/artifact requirements, release target, and executable paper toolchain. Route material evidence gaps upstream and apply the earliest affected policy Gate's `reopen_when_changed` contract when the manuscript needs a claim beyond the frozen boundary.

For LaTeX projects, prefer the repository's existing `Makefile`, `.latexmkrc`, or documented build command; otherwise use an explicitly declared build such as `latexmk`. Do not assume `pdflatex`, BibTeX, or a fixed directory layout, and do not auto-modify source to make compilation pass. Record the minimum contract:

```yaml
release_target: initial_submission
paper_toolchain:
  source_format: latex
  entrypoint: main.tex
  working_directory: .
  template: {source_url: "", version: "", content_hash: null}
  build:
    command: ""
    clean_command: ""
    engine: ""
    bibliography_backend: null
  bibliography_files: []
  figure_roots: []
  tool_versions: {}
  outputs: {pdf: "", log: "", render_dir: ""}
  verification:
    clean_build: false
    fatal_errors: []
    undefined_citations: []
    undefined_references: []
    missing_assets: []
    warnings: []
    pdf_content_hash: null
    visual_review: {status: pending, reviewed_by: "", reviewed_at: null, notes: ""}
```

Declare `bibtex`, `biber`, or `none` and the bibliography files actually used. In `bibliography_provenance`, retain each bibliography source or export tool, retrieval/export time, file hash, citation-key mapping, and content-verification status. Non-LaTeX sources use an equivalent reproducible build and rendered-output contract rather than pretending to be TeX.

When an external builder, renderer, bibliography exporter, or other declared Reference Stack performs the work, persist and verify the operation through the shared `adapter_exchange` contract, then register the attempt's first `accepted` receipt before the external side effect. Register outputs and logs before referencing them from later factual receipts. Provider-reported success does not replace clean-build checks, visual inspection, claim review, or release approval.

## Map claims before drafting

Assign every central claim and material numerical statement one primary manuscript location. Maintain a claim map entry such as:

```yaml
paper_claim_id: PAPER-CLAIM-001
claim_id: CLAIM-001
claim_record_id: CLAIM-RECORD-001
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
4. run a clean build through the declared bibliography pipeline and retain commands, tool versions, logs, output path, and hash;
5. render the PDF to pages or a contact sheet and record visual inspection with reviewer identity, not only source-text checks;
6. check venue limits, required sections, anonymization, acknowledgments, metadata, ethics, and artifact requirements;
7. verify every paper change and promised check.

Maintain one stable working artifact for each of `paper.manuscript`, `claim_map`, `change_map`, `bibliography_provenance`, `compilation_log`, `rendered_output`, `render_inspection_record`, and `submission_checklist`, then register their current revisions for the release package. Registration snapshots files, not directories; use a manifest where a role represents a collection.

Keep figure source data, generation code or configuration, publication output, preview, and QA notes traceable through the existing paper and experiment manifests. Mechanical checks may report missing assets, undefined references, build failures, and warnings; they cannot certify scientific writing or visual quality.

## Hand off for external release

Use `policy.stages.paper.exit_criteria` and the paper stage's GateRef in `policy.workflow_graph.stage_exit_requirements` as the sole completion, binding, mutability, and reopen contract. After explicit human approval, approve that GateRef through `researchctl` with the policy-required decision review fields. Gate approval does not itself authorize external submission.
