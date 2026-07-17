# ADR 0005: Use a bounded, adversarial innovation protocol and frozen Evo acceptance

- Status: Accepted
- Date: 2026-07-16
- Scope: Idea-stage exploration and comparative innovation claims

## Context

The current Idea portfolio preserves candidates, evidence, lineage, dispositions,
predictions, and kill criteria, but those fields alone do not establish that the
search was independent, diverse, adversarial, budget-bounded, or resistant to its
own ordering and selection bias. Conversely, making novelty scores or an automated
tournament part of Gate authority would let a model approve its own scientific
judgment.

The public target that the workflow approaches Evo is also comparative. Feature
parity, copied terminology, internal Elo values, or historical paper results cannot
show that the current Plugin produces non-inferior innovation behavior.

## Decision

### Project Idea protocol

Idea work remains one canonical portfolio. Before generating candidates, declare a
bounded exploration budget for seed passes, candidate count, refinement, pairwise
review, tools or retrieval, and a stopping condition. Run at least three seed
passes against the same research brief, evidence base, and resource constraints.
A pass is `isolated` only when it cannot see peer candidates or generator rationale;
otherwise record `declared_nonisolated` and do not claim independent generation.

Every non-seed candidate declares one of five variation operators:

- `transfer`: apply a supported mechanism across a defensible domain boundary;
- `assumption_mutation`: change one material assumption and expose its consequence;
- `mechanism_recombination`: combine mechanisms with all parent IDs retained;
- `subtraction`: remove a component or claim to test the smallest sufficient idea;
- `pivot`: replace the failed mechanism while preserving the question or recorded
  boundary that still motivates the search.

Seeds have no parents; derived candidates use `parent_ids`, including multiple
parents for recombination. Candidate records retain the operator, expected gain,
new risk, evidence, result, and repair or failure feedback. Optional registered
Scientific records may express the same lineage through one or more existing
`derived_from` relations; no Idea attempt store or second workflow state is added.

Pairwise review is budget-aware but cannot silently privilege presentation order.
Each scheduled pair is presented in both left-right orders, records the reviewer
context, Novelty, Feasibility, Relevance, and Clarity judgments, evidence IDs,
flaws, winner or tie, and any order disagreement. Incomplete comparison coverage
and unresolved cycles remain explicit. Internal scores organize review only.

Active candidates receive adversarial review, and finalists receive all three
independent lenses: domain and closest-prior-work collision; falsifiability,
confounds, and overclaim; and implementation, resource, and safety feasibility.
Each finding records its evidence, disposition, repair candidate where applicable,
and whether it exposed a duplicate, rejection reason, falsification, evidence gap,
or changed boundary. Original candidates and findings remain in the portfolio even
after repair, rejection, falsification, deduplication, or budget exhaustion.

The selector retains its internal top one, top three, reasons, comparison coverage,
and ordering limitations as a recommendation. It never approves `idea_freeze`,
establishes novelty, or substitutes for the human-selected candidate ID and Gate
decision. Project-local warm cycles may reuse registered portfolio history,
evidence, failures, and navigation hints from `.research/memory.md` only within the
same paper-bound mainline. Memory is not evidence or Gate authority, and no Codex
global or implicit cross-workspace memory is introduced.

### Frozen comparative acceptance

The comparative targets remain the frozen decisions from GitHub issue #5:

- EvoSkills `29e2c67f12858829ad0900645432b340c3f77522`;
- EvoScientist `01845f43110ad444b7e2a61b920effdf7e719029`, host
  version `0.2.2`;
- Track A compares both Skills on the same host with empty host memory;
- Track B compares Scientific-Research-Skill plus Codex with EvoSkills plus
  EvoScientist, counting auxiliary models and memory workers;
- both tracks use the same exact primary model, total token and tool budget,
  worker limit, resource constraints, and frozen 30 to 50 paper evidence pack per
  query;
- evaluation uses 30 public calibration queries, at least 20 preregistered held-out
  queries across at least four disciplines, at least 15 adversarial controls, and
  exactly three frozen runs per query;
- domain experts perform blinded, position-swapped pairwise review with verified
  closest prior work, and inference uses query-clustered paired-bootstrap 95 percent
  confidence intervals.

The acceptance input retains row-level data rather than only these summaries. A
canonical preregistration Hash binds the calibration and held-out query IDs, and
every held-out query has its own 30 to 50 paper-ID evidence pack. Each
query/run/reviewer row carries both position-swapped Scientific-Research-Skill and
Evo scores plus the frozen execution binding. Candidate counts retain both
systems' yield denominators and the denominators for novelty, flaw, repair,
pruning, and top-k measures; ranking rows retain regret and Kendall tau. Cost rows
retain only tokens and money: quality is derived from blinded paired ratings, not
copied from a cost record. A budget exception requires position-swapped blinded
ratings for every third-system run under the same binding.

Track B retains an empty cold-start memory snapshot, a three-revision
project-local memory lineage bound to one workspace and paper mainline, and at
least 20 matched query/run/dead-end-opportunity rows per cycle. Those rows carry
cold and warm recurrence, quality, pruning, and false-prune observations; every
cycle must reuse the exact cold baseline rows. The Harness deterministically
recomputes every aggregate and rejects any reported field that does not match. A
self-reported scalar, Boolean boundary claim, or precomputed summary cannot satisfy
either track's retained-data contract.

Track A passes only when all frozen thresholds hold: the lower confidence bound for
Novelty and the four-dimension composite is at least `-0.05`; every individual
dimension is at least `-0.10`; valid-diverse yield is at least 90 percent of Evo
with duplicate rate at most 10 percent; false novelty is at most 5 percent and no
more than two percentage points above Evo; flaw recall is at least 85 percent,
precision at least 75 percent, repair success at least 70 percent, and false prune
at most 5 percent; internal top one enters expert top three in at least 80 percent
of cases, normalized regret is at most `0.05`, and Kendall tau is at least `0.70`;
and total token and cost is at most 1.25 times Evo unless a superior quality-cost
Pareto point is mechanically demonstrated from at least three structured system
observations over the same frozen run IDs. The reported ratios must equal observed
token and monetary costs, the Scientific-Research-Skill point must have strictly
higher quality than Evo and be non-dominated on quality and both costs, and neither
ratio may exceed the bounded exception cap of `2.0`.

Track B additionally requires that by the third related project-local warm cycle,
confirmed-dead-end recurrence falls at least 50 percent against cold start without
reducing overall idea quality, while false prune remains at most 5 percent. Passing
Track A permits only the Core wording that the innovation workflow approaches
EvoSkills. Native-ecosystem wording requires both Track A and Track B.

The protocol is current stage semantics after this ADR lands. The comparative
capability remains Target until the frozen benchmark is actually run and its
versioned corpus, date, costs, judgments, statistics, and report are retained. The
protocol does not guarantee real novelty or scientific value. The local Harness
has no external trust root: it verifies retained bytes and recomputation, not that
a maintainer actually executed the declared rows or that reviewer identities are
authentic. Real comparative capability remains an externally reviewed claim even
after the retained benchmark contract passes.

## Consequences

- Innovation depth increases through bounded search, explicit variation,
  adversarial repair, and selector diagnostics without adding a Skill or Gate.
- Context isolation is an auditable declaration and benchmark condition, not a
  property Core can infer from prose or universal tool interception.
- Multiple-parent lineage reuses the existing candidate relation vocabulary; the
  runtime does not parse pairwise or adversarial scientific judgments.
- Failed, duplicate, rejected, and falsified directions remain useful negative
  evidence rather than disappearing behind the winner.
- A failed benchmark narrows the public comparison claim; it does not authorize
  changing the threshold or calling process completeness novelty.

## Rejected alternatives

- Automatic novelty scoring or Idea Gate approval would collapse research
  judgment into model self-evaluation.
- A mandatory Elo implementation would confuse one selector with the observable
  capability being measured and would copy a provider-specific mechanism.
- A global innovation memory would violate project-local provenance and leak
  judgments across unrelated workspaces.
- Matching candidate counts, persona names, or workflow steps without blinded
  output evaluation would measure imitation rather than non-inferiority.
