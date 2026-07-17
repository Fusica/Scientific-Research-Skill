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

**Scientific record — current**
A stable mechanical identity for one research object, bound to an exact registered artifact revision and an opaque locator in that artifact. It contains no scientific judgment payload.

**Record manifest — current**
An append-only registered artifact that declares stage-local Scientific records and their typed links without storing them in workflow state.

**Record relation — current**
A typed directional link between stable record IDs. Target existence and endpoint-kind compatibility are mechanically validatable, but its scientific truth and sufficiency remain Research judgment.

**Project-local Trace Graph — current read-only projection**
The deterministic forward and reverse projection rebuilt from registered record-manifest snapshots by `researchctl trace`, Dashboard, doctor, and audit. It is never persisted as graph state. `derived_from` ancestry cycles are errors; other cycles and structurally orphaned records are warnings, not scientific verdicts.

**Offline Audit Bundle — current external hand-off**
A deterministic, no-clobber tar exported to a new path outside the research project. It contains canonical state and contracts, non-authoritative memory, every historical snapshot, and a same-evidence trace projection for bundled-contract verification.

**Audit evidence root — current integrity handle**
The bundle-manifest Hash that must be retained independently to authenticate the expected hand-off. It does not by itself prove origin, human identity, authorization, provider truth, or scientific correctness.

**Research judgment — current policy**
Researcher-authored interpretation of novelty, mechanism, evidence strength, exclusions, causal meaning, adequacy, or scientific value. It remains in canonical research artifacts and is never certified by record validation.

**Conforming Adapter — current**
A replaceable external tool wrapper that consumes a Core-verified request, durably registers the attempt's first `accepted` receipt before any side effect, then consumes immutable snapshots, performs the effect outside Core, and reports factual observations through the Adapter Exchange. It has no Gate or scientific-judgment authority.

**Adapter Exchange — current**
An append-only registered artifact containing immutable Adapter Requests and factual Adapter Receipts. It is operation provenance, not workflow or provider job state.

**Adapter Request — current**
A pre-dispatch record that names the operation, exact payload and input revisions, applicable Gate decision, effect class, action-specific human authorization declaration, and bounded retry policy.

**Operation Binding — current**
The mechanical binding recorded by an Adapter Request. It proves which registered revisions and authority declarations the supported dispatch named; it does not prove scientific completeness, provider truth, or a Run Contract.

**Adapter Receipt — current**
An append-only observation bound to one request Hash and attempt lineage. The first `accepted` receipt is the durable pre-side-effect journal and must pass current authority checks; later receipts remain factual imports. `succeeded` is provider-reported mechanical success, not evidence that a scientific claim is valid.

**Dispatch verification — current**
The read-only, time-of-check validation performed by `researchctl adapter verify` before a conforming Adapter journals a new attempt. It does not itself authorize a side effect: the first `accepted` receipt must next be registered while authority is current. A stale Gate, resolved or active attempt, exhausted budget, or unreconciled unknown outcome blocks verification.

**Action-specific human authorization declaration — current policy**
The request-scoped audit declaration required for every non-low-risk effect. Core validates its shape but does not authenticate the actor, infer consent, or replace the actual authority required by the user and policy.

## Capability claims

**Process capability — accepted**
The observed ability to complete a declared research-workflow boundary with traceable, recoverable records. It is not a judgment of scientific outcome quality.

**Core — accepted**
The provider-neutral Plugin boundary: one public Skill, one policy, one state writer, project-local state and memory, and mechanical Hooks and validators.

**Reference Stack — accepted**
The smallest named set of external adapters, tools, and human actions required to close an end-to-end capability that Core deliberately does not perform alone.

**End-to-end capability — accepted**
A process capability closed by Core plus its declared Reference Stack, including failure recovery and auditable hand-offs across their authority boundary.

**Current capability status — accepted**
Implemented behavior available in the named release. Current does not imply that a comparative or end-to-end benchmark has passed.

**Target capability status — accepted**
An acceptance objective that is not yet a claim about the current release. A
`target_contract_valid` result proves only that the objective is shaped correctly;
its declared evidence is explicitly unverified.

**Benchmark-verified capability status — accepted**
A capability demonstrated by a named version against a frozen harness, corpus, date, and retained report.

**Acceptance evidence pack — current maintainer contract**
A report-adjacent, independently hashed manifest of regular retained files. Current
and Benchmark-verified claims resolve evidence and provenance by artifact ID, verify
path, size, and content Hash, and derive outcome fields from the retained bytes.

**High — accepted capability level**
The declared scope is process-complete, evidence-traceable, recoverable at material failures, and validated by deterministic and representative-scenario checks.

**Very high — accepted capability level**
High plus end-to-end closure within the declared boundary, with cross-stage, adapter, failure-recovery, adversarial, and offline-audit acceptance evidence where applicable.

**Approaches Evo — accepted comparison result**
A statistical non-inferiority result from the frozen two-track benchmark. It is neither a synonym for Very high nor a guarantee of real novelty.

**Innovation protocol — current stage semantics**
The bounded Idea procedure in `01-idea.md`: at least three declared seed passes, typed variation with multi-parent lineage, position-swapped pairwise review, three adversarial lenses, repair or failure feedback, selector diagnostics, and preservation of unsuccessful work. Runtime validation does not prove context isolation, novelty, or review quality.

**Independent seed pass — current semantic declaration**
A candidate-generation pass that sees the common brief, evidence, and constraints but no peer candidate or generator rationale. `isolated` is valid only when that context boundary exists; otherwise the portfolio records `declared_nonisolated`.

**Selector recommendation — current stage semantics**
The retained internal top one and top three with reasons, coverage, and ordering limitations. It organizes human review but is neither novelty evidence, candidate selection authority, nor Idea Gate approval.

**Project-local warm cycle — current stage semantics**
A related Idea cycle inside the same paper-bound mainline that may reuse registered portfolio history, evidence, failures, and project navigation hints. It creates no Codex-global or implicit cross-workspace research memory, and memory remains non-evidence.

**Paper production semantic contract — current stage semantics**
The source, build, venue, verification, render, claim, citation, number, anonymity, package, and revision-consistency procedure in `05-paper.md` and `06-revision.md`. It defines required research artifacts and reviews but does not mean a named Reference Paper Adapter has passed end-to-end acceptance.

**Venue Profile — current stage semantics**
A source-cited, dated declaration inside the applicable release checklist that records venue requirements, applicability, conflicts, and unknowns. Registration and Gate binding identify the reviewed declaration; they do not certify that an external venue rule is current or true.

**Verification check class — current stage semantics**
One of `mechanical`, `researcher_review`, or `venue_fact`. The class states whether a result is reproducible tool output, scientific or visual human judgment, or a sourced external requirement; it prevents a successful build or text scan from masquerading as paper correctness.

**Reference Isolated Command Adapter — current**
The shipped `scripts/reference_stack.py` Adapter for `experiment_execution` and `paper_production`. It consumes exact registered inputs in a clean temporary directory and journals through public `researchctl` calls; its network declaration is not enforced, and its mechanical result is not scientific, venue, Gate, or submission authority.

**Reference Paper Adapter — current implementation surface, Target acceptance**
The paper-production mode of the Reference Isolated Command Adapter. It materializes exact registered paper inputs, runs declared tool probes and clean/build argument vectors, retains logs and output provenance, and reports factual mechanical results. It does not perform or replace researcher-review and venue-fact checks, approve release, or authorize submission; representative paper field trials remain required for the capability target.

**Semantic acceptance harness — current maintainer tool**
The strict `scripts/validate_acceptance.py` evaluation surface that requires class-specific immutable evidence provenance; prevents one retained build report or identical bytes from satisfying incompatible classes; keeps venue facts, human review, Adapter runs, recovery, offline audit, representative scenarios, adversarial checks, cross-stage checks, and benchmarks distinct; and computes any bounded Pareto exception from same-run observations. It is not workflow state, Gate policy, a status promoter, or proof of scientific correctness.

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
A proposed immutable binding of code Hash, configuration, data scope, seed set, resource limit, and expected observation for one approved run. Current experiment specifications and Operation Bindings are artifacts, not first-class per-launch permits. Promotion remains gated by the three-experiment Pilot and an accepted decision; current retry semantics must not be described as a Run Contract.

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
