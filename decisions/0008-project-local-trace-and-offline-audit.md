# ADR 0008: Derive project-local trace and export no-clobber offline audits

- Status: Accepted
- Date: 2026-07-16
- Scope: Scientific-record trace diagnostics and offline audit hand-off

## Context

ADR 0003 made typed record manifests reconstructible but deferred orphan and cycle
diagnostics to a later Trace Graph decision. Project-level audit also needs more
than the current state pointer: a recipient must be able to replay every immutable
snapshot and the contracts that interpreted it without consulting the source
workspace. Neither need justifies a persisted graph, another state format, or an
audit command that may overwrite research material.

## Decision

`researchctl trace` and the Dashboard derive one deterministic, read-only
projection from registered record-manifest snapshots. The projection is never
stored as workflow state or a sidecar index. Existing structural errors, dangling
or incompatible endpoints, duplicate identities, and invalid correction lineage
remain hard errors. A `derived_from` cycle is also a hard mechanical error because
it contradicts derivation ancestry. Other relation cycles and structurally orphaned
records are warnings: they remain visible without pretending that relation
completeness or scientific truth is mechanically decidable.

This decision supersedes only ADR 0003's deferral of orphan and graph-cycle
diagnostics. It does not change the optional record-manifest role or transfer
scientific judgment from canonical artifacts to the projection.

`researchctl audit export` creates a deterministic tar containing canonical state,
the bundled policy, runtime contract and Plugin manifest, non-authoritative local
memory, every historical registered snapshot, and a trace projection derived from
the same evidence. Verification uses the bundled contracts and rebuilds semantic
checks offline. Export accepts only a new destination outside the research project,
publishes it atomically without replacement, and therefore cannot overwrite state,
the lock, memory, snapshots, registered live sources, or an unrelated existing
file.

The manifest's `evidence_root` authenticates the expected bundle only when retained
through an independent channel and supplied during verification. Internal Hash
consistency alone does not prove origin, researcher identity or authorization,
provider truth, scientific correctness, paper quality, or publication outcome.

## Consequences

- Knowledge queries and reverse traceability improve without a second graph store.
- `doctor`, CLI trace, Dashboard, and offline audit consume one record-inspection
  seam and preserve the same hard-error versus warning boundary.
- Terminal projects may export a new external audit hand-off without mutating the
  research workspace; malformed audit subcommands remain blocked by the Hook.
- Re-export uses a new path. The caller must deliberately manage old external
  bundles instead of relying on a clobbering command.

## Rejected alternatives

- Persisting a graph beside state would introduce another authority and stale-index
  recovery problem.
- Treating every cycle or orphan as invalid would make mechanical completeness a
  proxy for research judgment.
- Exporting inside the workspace or replacing an existing path would make a
  read-only audit feature a destructive writer.
- Trusting a self-consistent bundle without an externally pinned root would confuse
  integrity with authenticated origin.
