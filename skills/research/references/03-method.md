# Stage 3: Method formalization

Translate the approved idea into an implementable, testable contract without silently changing its scientific claim.

## Establish scope and assumptions

Read the approved idea and closest-work evidence. Define:

- agents or systems, inputs, observations, latent variables, outputs, and constraints;
- observable scope, data-generating process, uncertainty sources, and success criteria;
- assumptions, exclusions, boundary conditions, and failure behavior;
- the exact contribution claim and what the method does not model.

If the method requires a different central mechanism, reopen `idea_freeze` instead of retrofitting the idea artifact.

## Decompose into atomic concepts

For each concept or module, record its scientific role, dependencies, mathematical definition, dimensions and domains, implementation interface, tensor or data shapes, invariant, test, and expected failure behavior. Link non-obvious choices to evidence or derivations.

Maintain a bidirectional equation-to-code map:

| Concept ID | Equation ID | Meaning | Planned code symbol/path | Shapes or units | Test or invariant |
| --- | --- | --- | --- | --- | --- |
| `CONCEPT-001` | `EQ-001` | | | | |

Treat a formula without executable meaning and code without a scientific role as unresolved.

## Specify objectives and algorithms

Define every objective term, constraint, normalization, optimization or inference procedure, initialization, termination rule, complexity, and stochastic component. Derive non-obvious transitions. Use pseudocode whose symbols, interfaces, and control flow match the implementation plan.

Mark heuristics as heuristics. Do not use theorem-like language, optimality, stability, safety, or guarantees without explicit definitions, assumptions, and a valid proof or verification path.

## Convert mechanisms into predictions

For every claim candidate, create a stable prediction ID and specify the observable outcome, falsifying outcome, necessary baseline or intervention, boundary conditions, and planned experiment IDs.

| Prediction ID | Origin claim candidate IDs | Observable | Falsifying outcome | Baseline or intervention | Boundary conditions | Experiment IDs |
| --- | --- | --- | --- | --- | --- | --- |
| `PRED-001` | `CLAIM-CAND-001` | | | | | |

Experiment rows must carry prediction IDs; prose similarity is not sufficient traceability.

## Maintain the method contract

Give the contract an artifact ID, version, content hash, source idea ID/version, and `gate_ref: method_experiment_approval`. Include:

1. scope and research claim;
2. problem setting;
3. assumptions and exclusions;
4. notation with meanings, domains/shapes, units, and first use;
5. inputs, observations, outputs, and constraints;
6. objectives with term-by-term rationale;
7. atomic modules and interfaces;
8. algorithm or pseudocode;
9. training, optimization, or estimation procedure;
10. inference or deployment procedure;
11. complexity, latency, and resource expectations;
12. safety, stability, and validity conditions;
13. equation-to-code and prediction-to-experiment maps;
14. unresolved questions and implementation risks.

Validate notation, units, shapes, pseudocode, interfaces, and planned code symbols against any existing implementation. Record mismatches rather than assuming that paper and code agree.

## Request Gate approval

Prepare the method and experiment approval package with baselines, metrics, statistical unit, repetitions rationale, resources, safety constraints, and stop/kill criteria. Require explicit human approval before expensive or safety-relevant execution.

Use `researchctl gate approve method_experiment_approval --reason "..."` only after approval. Increment the contract version and assess downstream impact when assumptions, objectives, interfaces, or the central mechanism change; reopen the Gate when the approved contract is no longer the one being executed.
