# Stage 5: Paper production and submission preparation

Assemble the manuscript from frozen evidence and claims.

## Keep stable working paths

Continue editing the repository's real manuscript source, claim map, change map, bibliography provenance, build log, render inspection record, and checklist under the shared `policy.artifact_layout` contract.

Use a small registered manifest for source directories, oversized files, figure collections, and other large outputs. The manifest must retain stable IDs, paths, checksums, and enough generation or verification information to audit the referenced material.

## Establish the paper contract

Record the audience, contribution hierarchy, venue/format constraints, required sections, anonymization and ethics/artifact requirements, release target, and executable paper toolchain. Keep one source-cited Venue Profile inside the submission checklist rather than treating built-in knowledge or an Adapter as venue authority. Route material evidence gaps upstream and apply the earliest affected policy Gate's `reopen_when_changed` contract when the manuscript needs a claim beyond the frozen boundary.

The Venue Profile records the venue and track, source URL or registered file, source version or publication date, retrieval time, retained content Hash, applicable manuscript category and round, quantitative limits, required sections and files, anonymity, metadata, ethics and artifact rules, conflicts, unknowns, and the reviewer who confirmed applicability. Registration proves which dated declaration was reviewed; it does not prove that an external venue has not changed.

For LaTeX projects, prefer the repository's existing `Makefile`, `.latexmkrc`, or documented build command; otherwise use an explicitly declared build such as `latexmk`. Do not assume `pdflatex`, BibTeX, or a fixed directory layout, and do not auto-modify source to make compilation pass. Record the minimum contract:

```yaml
release_target: initial_submission
venue_profile:
  venue: ""
  track: ""
  manuscript_category: ""
  round: initial_submission
  sources:
    - {url_or_registered_file: "", version_or_published_at: "", retrieved_at: null, content_hash: null}
  quantitative_limits: {}
  required_sections: []
  required_files: []
  anonymity_rules: []
  metadata_rules: []
  ethics_and_artifact_rules: []
  conflicts: []
  unknowns: []
  applicability_review: {status: pending, reviewed_by: "", reviewed_at: null, notes: ""}
paper_toolchain:
  source_format: latex
  entrypoint: main.tex
  working_directory: .
  source_artifact_refs: []
  source_manifest_hash: null
  template: {source_url: "", version: "", content_hash: null}
  build:
    command_argv: []
    clean_command_argv: []
    cwd: .
    engine: ""
    bibliography_backend: null
    timeout_seconds: null
    resource_limits: {}
    network_access: disabled
  isolation:
    mode: clean_isolated_materialization
    writable_roots: []
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
    warning_dispositions: []
    pdf_content_hash: null
    visual_review: {status: pending, reviewed_by: "", reviewed_at: null, notes: ""}
checks:
  - check_id: PAPER-CHECK-001
    class: mechanical # mechanical | researcher_review | venue_fact
    subject: clean_build
    status: pending # pending | pass | fail | not_applicable
    performed_by: ""
    evidence_refs: []
    finding: ""
    disposition: ""
```

Declare `bibtex`, `biber`, or `none` and the bibliography files actually used. Use argument vectors rather than an implicit shell command, run them in the declared `cwd` after materializing and Hash-checking the registered source in a clean isolated working directory, and retain tool versions, standard output and error, exit status, output Hashes, and the declared environment, network, time, and resource boundaries. Do not edit the canonical source merely to make a build pass. In `bibliography_provenance`, retain each bibliography source or export tool, retrieval/export time, file hash, citation-key mapping, and content-verification status. Non-LaTeX sources use an equivalent reproducible build and rendered-output contract rather than pretending to be TeX.

When an external builder, renderer, bibliography exporter, or other declared Reference Stack performs the work, persist and verify the operation through the shared `adapter_exchange` contract, then register the attempt's first `accepted` receipt before the external side effect. Register outputs and logs before referencing them from later factual receipts. Provider-reported success does not replace clean-build checks, visual inspection, claim review, or release approval, and it grants no authority to rewrite source or submit externally.

The shipped `scripts/reference_stack.py` is the minimum local Reference Paper Adapter. Derive its registered payload from `assets/reference-stack-payload.template.json`, materialize every non-payload request input exactly once, name tool-version probes and separate `clean` and `build` steps where applicable, and declare every expected output, log, and result artifact under the exact `.research/artifacts/<stage>/reference-stack/<attempt-id>/` directory. The Adapter uses a clean temporary directory, parent-owned bounded command logging, Core-enforced first-observation output Hash bindings, the public request/accepted/receipt protocol, and one no-clobber artifact batch; a retry uses fresh paths with the same stable artifact IDs and starts only after `unknown` or orphan reconciliation. It does not enforce the declared network setting and its `succeeded` result covers only the retained mechanical contract. Keep all `researcher_review` and `venue_fact` checks in the canonical checklist, and use another conforming Adapter when the toolchain requires a stronger container or network boundary.

## Classify every verification check

Give every promised check a stable ID and exactly one class:

- `mechanical`: reproducible observations such as process exit, source and output Hashes, tool versions, produced files, undefined keys or references, missing assets, page rendering, declared quantitative limits, and exact package membership;
- `researcher_review`: judgments such as whether a citation supports the precise sentence, a material number comes from the correct registered analysis and estimand, wording stays within the Claim ledger, a rendered page is legible, or anonymization is substantively complete;
- `venue_fact`: the source, retrieval date, applicability, conflict, and uncertainty of a venue rule before another check applies it.

Record `pending`, `pass`, `fail`, or `not_applicable`, the responsible tool or reviewer, evidence refs, factual finding, and disposition. Every failure and warning needs an explicit disposition, but disposition does not turn a blocking failure into a pass: resolve it, route the affected claim or artifact upstream, or stop the release. A mechanical candidate-anonymity scan, text match, citation-key lookup, or number comparison can focus review but cannot certify complete anonymity, citation support, numerical scientific consistency, venue truth, or paper quality.

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

1. reproduce every material number from the correct registered analysis, estimand, included runs, and output artifact;
2. audit claim wording, title, abstract, contribution list, conclusion, and limitations against the ledger;
3. verify citation-key resolution mechanically and exact sentence support through researcher review;
4. audit terminology, notation, cross-references, figures, tables, captions, appendix, supplement, and all consistency targets;
5. materialize the exact registered source in a clean isolated directory, run the declared argument vectors, and retain `cwd`, tool versions, logs, exit status, output path, and Hash;
6. render the PDF to pages or a contact sheet and record visual inspection with reviewer identity, not only source-text checks;
7. apply the source-cited Venue Profile to limits, required sections and files, anonymization, acknowledgments, metadata, ethics, and artifact requirements;
8. verify every paper change, warning disposition, promised check, and exact release-package member.

Maintain one stable working artifact for each of `paper.manuscript`, `claim_map`, `change_map`, `bibliography_provenance`, `compilation_log`, `rendered_output`, `render_inspection_record`, and `submission_checklist`, then register their current revisions for the release package. Registration snapshots files, not directories; use a manifest where a role represents a collection.

Keep figure source data, generation code or configuration, publication output, preview, and QA notes traceable through the existing paper and experiment manifests. Mechanical checks may report missing assets, undefined references, build failures, warnings, candidate anonymity leaks, and declared venue-limit violations; they cannot certify citation support, scientific or numerical validity, complete anonymity, current venue truth, writing quality, or visual quality.

## Hand off for external release

Use `policy.stages.paper.exit_criteria` and the paper stage's GateRef in `policy.workflow_graph.stage_exit_requirements` as the sole completion, binding, mutability, and reopen contract. After explicit human approval, approve that GateRef through `researchctl` with the policy-required decision review fields. Gate approval binds the reviewed package and checklist revisions; it neither authenticates the Venue Profile nor authorizes external submission. Actual sending remains a separate `external_release` operation with the exact approved package and action-specific human authorization.
