# ADR 0002: Use evidence-qualified, boundary-specific capability claims

- Status: Accepted
- Date: 2026-07-16
- Scope: Public capability comparisons and vNext acceptance targets

## Context

The labels High, Very high, and Approaches Evo are ambiguous when they do not say
whether they describe Core, Core plus external tools, process integrity, or the
quality of a scientific outcome. A bare six-dimension score would allow a target
to be mistaken for current capability and a process benchmark to be mistaken for
a promise of novelty, correctness, paper quality, or acceptance.

The Plugin must retain its small provider-neutral boundary while still supporting
strong end-to-end targets through explicit Reference Stack contracts.

## Decision

Capability levels describe evidence-qualified process capability, never scientific
outcome quality. Every public capability claim must name:

1. the system boundary: Core or Core plus a named Reference Stack;
2. the evidence status: Current, Target, or Benchmark-verified;
3. for Benchmark-verified claims, the version, frozen harness or corpus, evaluation
   date, and retained report;
4. the applicable exclusions and human authority boundary.

High means that the declared scope is process-complete, evidence-traceable,
recoverable at material failures, and validated by deterministic and
representative-scenario checks. Very high additionally requires end-to-end closure
inside the declared boundary and applicable cross-stage, adapter,
failure-recovery, adversarial, and offline-audit evidence.

Approaches Evo is a separate statistical non-inferiority result. Track A compares
Core against EvoSkills on the same host. Passing Track A permits only the wording
that the Core innovation-elicitation process approaches EvoSkills. The
native-ecosystem wording requires both Track A and Track B, where the declared
Scientific-Research-Skill stack is compared with EvoSkills plus EvoScientist. The
frozen snapshots, budgets, corpus, evaluators, statistics, and thresholds remain
recorded in GitHub issue #5.

The accepted vNext targets are:

| Dimension | System boundary | Evidence status | Target wording |
| --- | --- | --- | --- |
| Workflow governance | Core | Target | Very high |
| Project-level audit | Core | Target | Very high |
| Experiment execution | Core + Reference Stack | Target | End-to-end Very high |
| Paper production and submission preparation | Core + Reference Stack | Target | End-to-end Very high |
| Knowledge management | Project-local Core | Target | High |
| Innovation elicitation | Track A: Core; Track A + B: declared native stack | Target | Approaches EvoSkills / Approaches the Evo native ecosystem |

Submission preparation ends with an auditable release package. It never authorizes
or performs external submission. External release, costly compute, destructive
operations, safety-relevant hardware, Gate decisions, and lifecycle decisions keep
their existing human authority requirements.

No capability label guarantees scientific correctness, statistical validity, real
novelty, paper quality, acceptance, or universal interception of external actions.
Until the applicable acceptance evidence exists, public wording must say Target;
it must not present the target level as Current or Benchmark-verified.

## Consequences

- Public comparison tables cannot contain an unqualified rating.
- Core remains small; end-to-end strength may depend on a declared, replaceable
  Reference Stack without making a provider canonical state or Gate authority.
- Acceptance work must retain evidence sufficient to reproduce each verified
  claim, rather than relying on feature counts or subjective positioning.
- A benchmark failure narrows or removes the public claim; it does not get hidden
  by changing the meaning of the level after evaluation.

## Rejected alternatives

- Treating all six labels as Core capability would misrepresent experiment and
  document execution that intentionally belongs to adapters and user tools.
- Treating the labels as observed scientific output quality would create promises
  that process controls cannot support.
- Collapsing EvoSkills and EvoSkills plus EvoScientist into one comparison would
  hide the host and memory advantage measured by Track B.
