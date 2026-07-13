# Stage 3: Method formalization

Translate the approved idea into an implementable, testable contract.

## Establish scope and assumptions

From the approved idea and closest-work evidence, define:

- agents or systems, inputs, observations, latent variables, outputs, and constraints;
- observable scope, data-generating process, uncertainty sources, and success criteria;
- assumptions, exclusions, boundary conditions, and failure behavior;
- the exact contribution claim and what the method does not model.

Reopen `idea_freeze` if the central mechanism changes.

## Decompose into atomic concepts

For each concept or module, record its scientific role, dependencies, definition, dimensions/domain, implementation interface, shapes, invariant, test, and expected failure behavior. Link non-obvious choices to evidence or derivations.

Maintain a bidirectional equation-to-code map:

| Concept ID | Equation ID | Meaning | Planned code symbol/path | Shapes or units | Test or invariant |
| --- | --- | --- | --- | --- | --- |
| `CONCEPT-001` | `EQ-001` | | | | |

## Specify objectives and algorithms

Define objective terms, constraints, normalization, optimization/inference, initialization, termination, complexity, and stochastic components. Derive non-obvious transitions and align pseudocode with planned symbols and interfaces. Mark heuristics and state the definitions and support behind theorem-like, optimality, stability, safety, or guarantee language.

## Convert mechanisms into predictions

For every claim candidate, create a stable prediction ID and specify the observable outcome, falsifying outcome, necessary baseline or intervention, boundary conditions, and planned experiment IDs.

| Prediction ID | Origin claim candidate IDs | Observable | Falsifying outcome | Baseline or intervention | Boundary conditions | Experiment IDs |
| --- | --- | --- | --- | --- | --- | --- |
| `PRED-001` | `CLAIM-CAND-001` | | | | | |

Experiment rows must carry prediction IDs.

## Maintain the method contract

Give the contract an artifact ID, version, content hash, source idea ID/version, and `gate_ref: method_experiment_approval`. Include:

1. scope and research claim;
2. problem setting;
3. assumptions and exclusions;
4. notation, domains/shapes, units, inputs, outputs, and constraints;
5. objectives, modules, interfaces, and aligned pseudocode;
6. training/estimation and inference/deployment procedures;
7. complexity, resources, safety, stability, and validity conditions;
8. equation-to-code and prediction-to-experiment maps;
9. unresolved questions, implementation risks, and mismatches with existing code.

## Request Gate approval

Register a `method.approval_package` containing the method contract, baselines, metrics, statistical unit, repetitions rationale, resources, safety constraints, and stop/kill criteria.

After explicit human approval, record `method_experiment_approval` through `researchctl`. Version changed assumptions, objectives, interfaces, or mechanisms and reopen the Gate whenever execution no longer matches the approved package.
