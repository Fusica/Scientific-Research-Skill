# Research workflow glossary

This glossary distinguishes current runtime behavior from accepted design intent and deferred ideas. Terms marked **current** are implemented now; **intended** terms are accepted but not yet fully encoded; **deferred** terms must not be presented as current capability.

## Core workflow

**Artifact — current**
A canonical research file registered under one `stage.role` with a stable artifact ID.

**Revision — current**
The next provenance version of the same artifact identity. A content change or intentional source relocation appends a revision; an identical registration is idempotent.

**Snapshot — current**
An immutable full-file copy and Hash captured when a revision is registered.

**Stage — current**
One of Idea, Literature, Method, Experiment and Results, Paper, or Revision. `current_stage` is the default conversational focus, not a claim that all work is strictly linear.

**Gate — current**
A mandatory human review boundary that blocks a protected transition until an explicit decision is recorded.

**GateRef — current**
The exact approval identity: an untargeted Gate such as `idea_freeze`, or a targeted Gate such as `release/initial_submission`.

**Gate cascade — current**
Audited reverse-order invalidation of currently approved downstream GateRefs after an upstream GateRef is reopened.

**Research mainline and lifecycle terms — current**
Use `policy.workspace_lifecycle` for mainline identity, terminal behavior, inactivity, supervision, and cross-workspace reuse; use `runtime-contract.json` for their machine fields and enums.

**Human sovereignty — current policy**
Use `policy.authority_boundary` and the repository scientific boundaries.

**Structured decision defense — current**
Use `policy.workspace_lifecycle.decision_review` for semantics and `runtime-contract.json` `decision` for field shape.

## Exploration and experiments

**Exploration — current**
Pre-Gate generation and comparison of incomplete or conflicting candidates without requiring every candidate to satisfy the final commitment standard.

**Commitment — accepted**
The point at which a candidate is selected and resources, claims, or release actions become governed by a strict Gate review.

**Experiment matrix — current**
The canonical registry of experiment specifications, hypotheses, variables, metrics, analysis plans, resources, and stop or kill criteria.

**Run registry — current**
The append-only authoritative index of every attempt, including failed, null, negative, excluded, cancelled, preempted, pruned, and contradictory outcomes.

**Decision log — current**
The append-only history of experiment judgments, corrections, rationale, outcomes, and next actions. It now also owns cumulative experiment review records.

**Analysis registry — current**
The append-only index of analysis plans and executions, with provenance, included and excluded runs, statistical units, estimands, and output references.

**Claim ledger — current**
The evidence-bounded map from claims to runs, analyses, limitations, status, and allowed or forbidden manuscript wording.

**Cumulative review, direction judgment, and Retry — current stage semantics**
Use the owning procedures and templates in `skills/research/references/04-experiment-results.md`; this glossary does not restate or extend them.

**Run Contract — deferred**
A proposed immutable binding of code Hash, configuration, data scope, seed set, resource limit, and expected observation for one approved run. Current experiment specifications are artifacts, not first-class per-launch permits.

**Pilot — deferred**
A proposed pre-formal-experiment path for bounded exploratory execution. The current Hook instead uses the stage-level method and experiment approval Gate.

## Statistical analogies and non-capabilities

**Trust-region-like iteration — analogy only**
The practice of recommending a bounded next change around the current best direction. The Plugin does not calculate a surrogate model, acquisition function, or trust-region radius.

**Sequential hypothesis testing — deferred**
A formal statistical procedure with predefined hypotheses, observation units, error control, and stopping boundaries. Current stop criteria and cumulative reviews do not implement such a test.

**Bayesian-style belief update — qualitative only**
An evidence-linked change in direction judgment or claim status. The Plugin does not calculate priors, likelihoods, Bayes factors, or posteriors.

**Process quality — current target**
Ordered, evidence-linked, recoverable research decisions. It can reduce preventable process failures but does not guarantee novelty, correctness, acceptance, or a high-quality paper.
