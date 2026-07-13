---
name: method-formalization
description: Translate an approved research idea into explicit assumptions, notation, mathematical objectives, algorithms, interfaces, and testable predictions with math-to-code traceability. Use when designing a method, formalizing an ML or robotics problem, deriving equations, writing pseudocode, specifying system modules, or auditing consistency between a paper and implementation.
---

# Method Formalization

Convert the frozen idea into a contract that can be implemented, tested, and written without silently changing the claim.

## Establish the problem

Read the approved idea card and closest-work evidence. Define scope, agents or systems, inputs, outputs, observable and latent variables, assumptions, constraints, uncertainty sources, and success criteria. State what the method does not model.

## Decompose into atomic concepts

For each concept or module, record:

- its scientific role and dependency;
- mathematical definition with dimensions and domains;
- implementation interface and tensor/data shapes;
- expected invariant and failure behavior;
- evidence or derivation supporting the choice.

Keep a bidirectional equation-to-code map. A formula with no executable interpretation and code with no stated scientific role are both unresolved items.

## Specify objectives and algorithms

Define objective terms, constraints, normalization, optimization or inference procedure, initialization, termination, complexity, and stochastic elements. Derive non-obvious transitions. Use pseudocode whose names and control flow match the planned implementation.

Do not add theorem-like claims without assumptions and a valid proof path. Mark heuristics as heuristics.

## Turn the method into predictions

For each claimed mechanism, state a stable prediction ID, origin claim-candidate IDs, an observable prediction, a falsifying outcome, the baseline or intervention needed to test it, and the expected boundary conditions. Experiment rows must carry these prediction IDs rather than relying on prose similarity.

## Produce the method contract

Write `method_contract.md` using `references/method-contract.md`. Give the contract an artifact ID, version, and content hash. Include unresolved questions and implementation risks. Validate notation, units, tensor shapes, pseudocode, and proposed interfaces against any existing code before requesting the method/experiment gate.

Substantive changes to assumptions, objectives, or the central mechanism increment the contract version and trigger impact review of experiments and claims.
