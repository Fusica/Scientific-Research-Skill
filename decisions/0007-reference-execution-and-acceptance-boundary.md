# ADR 0007: Ship reference execution and evidence-only acceptance outside Core

- Status: Accepted
- Date: 2026-07-16
- Scope: Reference Stack execution and capability-claim evaluation

## Context

The capability matrix requires an executable experiment and paper-production path,
but Core intentionally owns workflow authority and immutable bindings rather than
provider jobs. It also requires one repeatable way to decide whether retained
acceptance evidence satisfies the labels in ADR 0002 and the frozen innovation
thresholds in ADR 0005. Putting either execution state or benchmark conclusions in
`.research/state.json` would create a second workflow model and let maintainer
evaluation masquerade as project research authority.

## Decision

Ship `scripts/reference_stack.py` as a named Reference Stack outside
`researchctl_core`. It supports only `experiment_execution` and
`paper_production`, consumes one strict registered `isolated_command` payload, and
calls the public `researchctl` executable for verification, atomic receipt append,
artifact registration, and terminal journaling. It never imports the state writer.

The Adapter registers `accepted` before any declared command, consumes the payload
separately, requires materials to cover every other request input exactly once,
materializes those exact snapshots into a clean temporary directory, rechecks their Hashes before and
after execution, invokes bounded argument vectors without a shell and in a fresh
POSIX process group, and kills remaining group descendants after every step even
when the leader exits successfully. Children receive only stdout/stderr pipes;
the parent writes bounded output to an unnamed spool and freezes it in a separate
private evidence directory after group cleanup, so a command cannot replace or
truncate the retained log or use that Adapter control file as an expected output.
The first observed Hash and size of every expected output are required fields in
the publication manifest; Core verifies the whole batch against them before it
creates any final path. The Adapter publishes
every present output, log, and mechanical result as one no-clobber batch, and then
appends `succeeded`, `failed`, or `unknown`. It does not support external release.
Its network field is an audit declaration, not an enforced sandbox, and its
mechanical success cannot certify a Run Contract, scientific identity, inclusion,
researcher review, venue truth, Gate approval, or submission authority. Stronger
container, cluster, tracker, hardware, or network guarantees remain replaceable
Adapters behind the same exchange.

Process-group cleanup prevents ordinary background children from writing after a
step. It is not a universal containment claim: a process that deliberately escapes
its group, and platforms without equivalent group control, require a stronger
container or job-object Adapter.

The public writer owns `artifact publish-batch`. It accepts only fresh paths under
`.research/artifacts/<stage>/reference-stack/<attempt-id>/`, validates the entire
manifest and one-canonical-artifact rule under the state lock, rejects symlinks and
all existing unrelated paths, creates attempt-scoped final files and immutable
snapshots through exclusive no-replace opens, verifies their bytes, and advances
state once. Every newly created POSIX directory entry, final file, and snapshot is
fsynced before the state commit. It never automatically unlinks a final or snapshot after failure:
portable POSIX has no conditional unlink, and `stat` followed by `unlink` could
delete an unrelated concurrent replacement. A catchable failure, interrupt, kill,
or power loss before state commit may therefore leave an unregistered, possibly
partial, attempt-scoped final or snapshot orphan because Core deliberately has no
second pending transaction state. Reconciliation may remove one only after
confirming canonical state does not reference it; retries use the stable artifact
ID with a new attempt path. If state replace completes before an interrupt or
output failure, exact path/Hash/size reconciliation preserves the committed batch
and the Adapter recovers that exact historical revision rather than assuming the
current revision. No failure or reconciliation path may overwrite or reuse a path.

Ship `scripts/validate_acceptance.py` and its functional module as a
maintainer-only Semantic Acceptance Harness. It accepts one strict retained report,
keeps zero-tolerance authority and provenance invariants separate from
deterministic, representative, human, recovery, offline-audit, adversarial,
cross-stage, Adapter, and benchmark evidence, and evaluates the exact six declared
boundaries. Each evidence class has its own immutable content Hash and structured
provenance; one retained report or identical bytes cannot satisfy incompatible
classes, and paper acceptance separately requires sourced, dated venue facts.
`Target` validation checks only the declaration contract and returns
`target_contract_valid`; self-reported evidence remains `declared_unverified` and
cannot qualify a capability. `Current` and `Benchmark-verified` require a hashed
evidence-pack manifest whose regular non-symlink files are resolved by artifact ID,
rehashed, and dereferenced. Evidence outcomes come from retained JSON rather than
the report copy; each non-bundle provenance reference must itself resolve to a
class- and scenario-bound strict JSON result; offline bundles undergo their
independent verifier; and the frozen representative corpus sets both minima and
mandatory failure/authority cases. Stable descriptor reads and repeated Hash/size
checks prevent a later path mutation from changing the evaluated bytes.

It encodes the frozen Track A and Track B design and thresholds from ADR 0005.
Benchmark-verified innovation additionally requires retained row-level observations;
the Harness recomputes query-cluster bootstrap bounds, rates, rankings, same-run
costs, Pareto eligibility, and warm-cycle measures before comparing every reported
summary field. Aggregate values alone therefore fail closed. The result never
changes workflow state, approves a Gate, authenticates a reviewer, proves that a
declared command or human review actually occurred, proves scientific truth, or
promotes a declared `Target` to `Current` or `Benchmark-verified`. Because the
repository has no external trust root, even a passing `Current` report means
`current_retained_evidence_contract_valid`, and a passing comparative report means
`benchmark_rows_recomputed`; `capability_qualified` remains false.

These implementations make the Reference Stack and acceptance surfaces current.
The public capability ratings remain `Target` until the applicable deterministic,
representative field-trial, human-review, failure-recovery, offline-audit, and
comparative reports are actually retained for a named version.

## Consequences

- Core remains one policy, one state writer, and no provider job store.
- The shipped local Adapter gives experiments and paper builds one reproducible
  end-to-end reference path while preserving crash and authority boundaries.
- A failed or interrupted reference attempt remains observable instead of being
  collapsed into a successful command exit or silently retried.
- Acceptance evidence classes cannot substitute for each other, and comparative
  wording fails closed at the frozen thresholds.
- A passing Target report validates only the target contract. A passing materialized
  report validates retained evidence consistency for its declared status and
  boundary; it does not independently qualify the real-world capability and is not
  scientific evidence inside a research workspace.

## Rejected alternatives

- Launching commands inside Core would make the state writer a provider broker and
  prematurely decide the deferred Run Contract.
- Treating a clean build, provider success, or deterministic test suite as a human
  semantic review would overstate experiment and paper capability.
- Persisting acceptance results in project state would mix release engineering
  claims with research evidence and Gate authority.
- Claiming network isolation from a declaration alone would be false; environments
  requiring enforcement must name a stronger Adapter.
