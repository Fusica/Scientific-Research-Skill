# ADR 0004: Bind Adapter dispatch through registered exchanges

- Status: Accepted
- Date: 2026-07-16
- Scope: Core, Adapter, and human authority at external-operation boundaries

## Context

Experiments, tracker imports, paper production, and external release may depend on
replaceable local or remote tools. A Gate approval alone does not prove that an
external action consumed the approved revisions, and an Adapter receipt cannot be
allowed to approve a Gate or turn provider success into scientific validity. At
the same time, putting provider jobs, retries, credentials, or execution state in
`.research/state.json` would expand Core into an operation engine and create a
second workflow state model.

The workflow also needs to preserve late, failed, cancelled, and unknown outcomes.
Rejecting a receipt merely because its Gate was reopened after dispatch would
erase material provenance; accepting that receipt as fresh authorization would be
equally wrong.

## Decision

Core owns a provider-neutral Adapter authority contract, not Adapter execution.
Each stage may register one append-only `adapter_exchange` JSON artifact through
the existing artifact writer. Its machine shape and enums live in
`runtime-contract.json`; the operation-to-stage, GateRef, effect-class, and human
authority mapping lives only in `policy.adapter_authority`.

An Adapter Request is persisted before a conforming Adapter performs a side
effect. It binds an operation kind, an exact payload locator and immutable input
artifact revisions, the exact currently approved Gate decision and artifact refs
when policy requires one, an effect class, an action-specific human authorization
declaration for every non-low-risk effect, and a bounded retry policy. This
mechanical binding is an Operation Binding, not the deferred Run Contract: it does
not prove that every scientifically material code, data, seed, environment,
resource, variable, or estimand field was declared.

Every artifact revision approved by a required Gate is also an operational input,
not detached metadata. For external release, the operational-input list must equal
the ordered approved package with no extra artifact, and the payload must itself
be one exact revision in that package. An unrelated release instruction or
attachment cannot borrow the package's Gate binding.

`researchctl artifact register` validates a new request before publishing its
immutable snapshot. `researchctl adapter verify <request-id> --attempt-id <id>`
then revalidates the current lifecycle, stage, exact Gate decision, artifact
integrity, attempt lineage, retry limit, and unknown-outcome rule. It emits a
time-of-check request envelope and never invokes an Adapter. Before any side
effect, the Adapter appends the attempt's first receipt with status `accepted` and
registers that exchange revision. Registration revalidates current authority and
makes the accepted receipt the durable pre-side-effect attempt journal. Only after
that registration succeeds may the Adapter consume the bound immutable snapshots
and perform the operation outside Core. Later observations are appended and
registered as superseding receipts for the same attempt. The accepted journal and
a later observation for that attempt cannot first appear in the same revision.
The request/receipt append commands serialize supported `researchctl` writers. If
working exchange bytes are replaced but artifact registration fails, the dirty
source remains for explicit reconciliation; the command never performs an unsafe
automatic unlink or restore, and canonical state and prior snapshots remain the
authority. The state lock is not a filesystem security boundary against a
non-cooperating same-user process.

This order closes the supported crash window: a crash before the accepted journal
has no authorized side effect, while a crash after it leaves an active attempt that
must be reconciled rather than blindly redispatched under a fresh ID. A first
non-`accepted` receipt is still preservable as a nonconforming factual import, but
it never serves as dispatch authority.

Receipts use only factual statuses: `accepted`, `running`, `succeeded`, `failed`,
`cancelled`, or `unknown`. They identify the Adapter and protocol, bind the exact
request Hash and attempt lineage, and reference registered outputs and logs. Their
machine schema grants no field for a Gate decision, scientific conclusion,
inclusion judgment, or action approval. A conforming Adapter keeps its free-text
message factual; Core validates shape and lineage, not the truth or semantics of
that message. Provider-reported `succeeded` means only that the provider reported
mechanical success.

Receipt import is not dispatch authorization, except that a newly registered first
`accepted` receipt is the explicit pre-side-effect journal described above and
must pass current authority checks. Later receipts for that already journaled
attempt remain importable after its Gate is reopened so failures and unknown
outcomes are not lost, but a new dispatch or retry must pass the current
verification and journal sequence again. Under
`reconcile_before_retry`, `unknown` must be reconciled on the same attempt before
retry; under `idempotent`, a retry may proceed only within the declared attempt
budget and stable idempotency key. Prior requests and receipts remain immutable
append-only prefixes.

Gate and lifecycle decisions remain human. Costly compute, destructive or
safety-relevant work, and external release additionally require a request-scoped
human authorization declaration. Core validates its presence and shape but does
not authenticate identity, infer consent, or treat a Gate as sufficient authority
for an external send. Credentials are prohibited in state, requests, receipts, and
registered payloads; Core does not claim semantic secret detection.

For tool calls the Hook can mechanically identify, it denies direct experiment
launch and external release even after the relevant Gate is approved and routes
the supported action through this exchange. Core does not add top-level Adapter,
operation, attempt, or receipt state; does not
select a provider; does not maintain capability discovery; does not hold a state
lock across external work; and does not claim to intercept tools that bypass this
supported path. `researchctl doctor` reconstructs and revalidates every immutable
exchange revision offline, including the Gate and stage authority in force when
each historical accepted journal was first registered.

## Consequences

- Core, Adapter, and human responsibilities are independently auditable without
  making a provider a Gate authority.
- Exact approved revisions, current authority, and retry eligibility are
  mechanically checkable in a durable attempt journal before a supported side
  effect, while actual provider truth remains an external claim.
- Gate reopening blocks new work but does not erase observations from work already
  attempted.
- Reference Stacks can replace providers without changing workflow state or
  copying provider SDKs into Core.
- The supported seam is stronger than lexical Hook detection but remains a
  cooperative protocol, not universal interception or exactly-once execution; an
  `accepted` attempt left by a crash requires reconciliation.
- A later Run Contract may deepen the bound scientific inputs without changing
  the public request/register/verify/receipt sequence.

## Rejected alternatives

- A Core operation broker that launches and recovers every provider would make the
  Plugin an execution platform, enlarge the lock and crash surface, and decide
  #11 before Pilot evidence exists.
- Provider-specific commands and state fields would duplicate authority logic and
  make one Reference Stack canonical.
- Treating release Gate approval as sufficient permission to send would violate
  human sovereignty and confuse artifact approval with action authorization.
- Rejecting late observations for an already journaled attempt would hide real
  failures and side effects; they must be preserved without being endorsed. A new
  accepted journal is different because it authorizes a future side effect and
  therefore requires current authority.
- Blind retry after an unknown outcome would risk duplicate costly, destructive,
  safety-relevant, or external effects.
