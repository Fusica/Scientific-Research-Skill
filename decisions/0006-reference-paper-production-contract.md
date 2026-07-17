# ADR 0006: Use a classified, source-bound paper production contract

- Status: Accepted
- Date: 2026-07-16
- Scope: Paper production, revision verification, and submission preparation

## Context

A successful manuscript build does not prove that citations support their text,
numbers match registered analyses, reviewer promises were applied, anonymity is
complete, or a venue rule is current and applicable. At the same time, treating
all checks as human prose would make Reference Stack execution irreproducible and
would provide no clean boundary between provider facts, research judgment, the
release Gate, and actual external submission.

The existing release packages and Adapter Exchange already bind registered
artifacts, Gate decisions, attempts, receipts, and external-action authority. Paper
production should deepen that seam without adding a paper state machine, a venue
database, another artifact role, or a provider-specific Gate.

## Decision

### Source and venue contract

The existing `paper.submission_checklist` or `revision.release_checklist` contains
one source-cited Venue Profile for the named release target. It records the venue
and track, source URL or registered file, source version or publication date,
retrieval time, content Hash when retained, applicable manuscript category and
round, quantitative limits, required sections and files, anonymity, metadata,
ethics and artifact rules, conflicts, unknowns, and the reviewer who confirmed
applicability. A profile is a dated research input, not a built-in venue fact. Its
registration and release binding prove which declaration was reviewed, not that
the venue has not changed.

The paper toolchain binds the exact registered source or source manifest,
entrypoint, working directory, clean and build argument vectors, bibliography
backend and files, figure roots, environment and network declaration, resource and
time limits, tool versions, expected outputs, and retained command, log, render,
and content Hash locations. A conforming Reference Paper Adapter materializes those
inputs into a clean isolated working directory, verifies their Hashes, and runs the
declared argument vectors in the declared working directory. It never edits the
canonical source merely to make a build pass.

### Classified verification

Every promised check has a stable check ID and exactly one semantic class:

- `mechanical`: reproducible observations such as process exit, source and output
  Hashes, tool versions, produced files, undefined keys or references, missing
  assets, page rendering, declared quantitative limits, and package membership;
- `researcher_review`: judgments such as whether a citation supports the precise
  sentence, a number is derived from the correct estimand, wording remains within
  the Claim ledger, a rendered page is legible, or anonymization is substantively
  complete;
- `venue_fact`: the source, retrieval date, applicability, conflict, and uncertainty
  of a venue requirement before a mechanical or researcher check applies it.

Each check records `pending`, `pass`, `fail`, or `not_applicable`, its evidence and
reviewer or tool, and a factual finding. Every failure and warning requires an
explicit disposition; warnings are not silently equated with either success or
failure. A disposition does not turn a blocking failure into a pass: resolve it,
route the affected claim or artifact upstream, or stop the release. Mechanical
scanners may identify a candidate anonymity leak, citation
key, number, or rendering defect, but they cannot certify citation support,
scientific consistency, visual quality, venue truth, or complete anonymity.

For initial submission, verification covers clean build and render, bibliography
provenance and support review, material claim and number mapping, cross-document
and cross-section consistency, figures and tables, anonymity and metadata, venue
requirements, and exact release-package membership. For revision, the same
contract additionally binds each atomic reviewer concern to the promised action,
registered source diff, exact manuscript location, response location, decisive
evidence, and verification result. A promised action is not complete until the
source and diff show it, and the response cannot claim a new result, number,
citation, location, or wording that disagrees with the revised manuscript.

### Adapter, Gate, and external action boundary

An external builder or renderer uses the existing `paper_production` Adapter
operation. It verifies the registered request, durably registers the first
`accepted` receipt before execution, then registers outputs and logs before a later
factual receipt references them. Provider-reported `succeeded` establishes only
mechanical provider completion. Human review remains required for the applicable
semantic checks and for release approval.

The existing release Gate binds the exact registered package and checklist
revisions. It neither authenticates the Venue Profile nor authorizes submission.
Actual sending remains a separate `external_release` request whose ordered inputs
exactly equal the approved package and whose action-specific human authorization,
verification, accepted journal, and later receipts follow ADR 0004. Submission
preparation therefore ends at an auditable, human-approved package.

This ADR defines current stage semantics and the contract a Reference Paper
Adapter must satisfy. It does not claim that a named adapter, venue family, or
end-to-end benchmark has already passed. Any public capability claim remains
Target until it names the Reference Stack and retains the applicable deterministic,
field-trial, researcher-review, and failure-recovery evidence.

## Consequences

- One checklist revision carries dated venue facts and their uncertainty without
  adding a canonical venue service or artifact role.
- Clean builds and mechanical defects become reproducible while scientific and
  visual judgments remain explicit human reviews.
- Initial submission and revision share one deep production contract; revision
  adds concern, action, diff, and response consistency rather than a second
  workflow.
- Reference adapters remain replaceable because the contract binds inputs,
  commands, outputs, checks, and receipts rather than a LaTeX engine or provider.
- Release approval and external submission retain separate evidence and authority.

## Rejected alternatives

- Treating a zero exit code as paper verification would ignore claim, citation,
  numerical, venue, anonymity, and render failures.
- Shipping built-in venue facts would become stale, provider-specific policy and
  would misstate Core's knowledge.
- Letting an Adapter auto-fix source would destroy the distinction between the
  approved source, the executed input, and an unreviewed repair.
- Treating release approval as permission to send would violate the external-action
  authority boundary.
- Adding a paper job store or another Gate would duplicate the existing artifact,
  Adapter Exchange, and release contracts.
