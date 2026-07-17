# ADR 0003: Use registered manifests for typed scientific records

- Status: Accepted
- Date: 2026-07-16
- Scope: Mechanical scientific-record identity and relation validation

## Context

Stable candidate, search, evidence, experiment, attempt, analysis, claim, paper,
and review IDs are needed for traceability. Putting those records in
`.research/state.json` would create a second research-state model, while parsing
all scientific Markdown would make prose rigid and make the runtime pretend it can
understand research judgment.

## Decision

Each stage may register one append-only `record_manifest` JSON artifact through the
existing artifact writer. A record contains only a globally stable `record_id`, a
machine enum `record_kind`, an exact source artifact role, ID, revision and opaque
locator, an optional same-kind `supersedes` link, and typed directional relations.
The manifest contains no novelty, evidence-strength, exclusion, causal, adequacy,
or value judgment.

The machine fields and enums live in the existing `runtime-contract.json`.
`researchctl artifact register` validates a pending manifest before creating its
snapshot, and `researchctl doctor` revalidates every immutable manifest revision.
The validator enforces exact shape, registered source revisions, project-unique
record IDs, linear correction lineage, relation target resolution, endpoint-kind
compatibility, and append-only manifest history. Workflow state stores only the
normal artifact revision pointer; it never copies the record graph.

The supported `record append` command serializes cooperating `researchctl` writers
under the state transaction lock. If it writes the working manifest but artifact
registration then fails, it leaves that dirty source visible for explicit
reconciliation while canonical state and prior snapshots remain unchanged. It
does not automatically unlink or restore the path because portable POSIX has no
inode-conditional delete or replace that could not harm a concurrent replacement.
The lock is not a filesystem security boundary against non-cooperating same-user
processes.

This ADR originally deferred orphan and graph-cycle diagnostics. ADR 0008 now
accepts the project-local Trace Graph boundary: `derived_from` ancestry cycles are
hard errors, while other relation cycles and structurally orphaned records are
warnings. Relation completeness and the semantic truth of a declared relation
remain Research judgment. A `record_manifest` is optional until the owning policy
Gate explicitly requires that artifact role.

## Consequences

- Natural-language research artifacts remain the authority for scientific content.
- Mechanical links can be reconstructed offline from immutable registered
  manifests without depending on an external provider.
- Corrections append a new record with `supersedes`; prior record declarations
  cannot be silently rewritten in a later manifest revision.
- Adding stronger graph completeness rules later does not require moving records
  into state or changing the public artifact writer.

## Rejected alternatives

- A top-level record registry in `.research/state.json` would create another state
  model and expand the writer contract unnecessarily.
- Mandatory parsing of every Markdown/YAML scientific template would couple prose
  structure to runtime correctness and overstate machine understanding.
- Unregistered sidecar indexes would lack revision, Hash, Gate binding, and offline
  audit provenance.
