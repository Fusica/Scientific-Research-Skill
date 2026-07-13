# Operating workflow

## 1. Intake

Inspect existing files and determine whether the request is a bounded task or a multi-stage project. For a multi-stage project, create or map `.research/project-state.yaml`. This file is the sole gate authority and binds decisions to artifact IDs, versions, and content hashes.

## 2. Idea and literature loop

Generate distinct candidate mechanisms, then search for closest work and falsifying evidence. Revise until one candidate has a defensible delta, testable predictions, feasible resources, and explicit kill criteria. The researcher approves Gate 1.

## 3. Method and experiment contract

Formalize assumptions, equations, modules, interfaces, invariants, and predictions. Convert predictions into registered experiment rows with baselines, metrics, units, analysis plans, cost, and safety. The researcher approves Gate 2 before expensive execution.

## 4. Progressive execution

Run integrity checks and baseline reproduction before the proposed method. Register every run against an immutable experiment spec version/hash, including failures and exclusions. Keep execution failure separate from scientific outcome. Diagnose the simplest failing case, log the controlled change, and reopen the method or idea when evidence invalidates an assumption.

## 5. Result and claim promotion

Analyze the registered population of runs at the correct statistical unit. Record included/excluded runs and analysis code/config in the analysis registry; generate checksummed figure/table manifests and maintain null or negative findings. Claims begin unassessed and are promoted only to supported or explicitly bounded wording when evidence permits. The researcher approves Gate 3.

## 6. Paper production

Map frozen claims to sections, citations, results, figures, limitations, and appendix material in a paper claim map; track actual edits in a separate change map. Compile and visually inspect the deliverable; verify terminology, numbers, cross-references, bibliography, and anonymization. The researcher approves Gate 4 for initial submission.

## 7. Review and revision

Map each atomic reviewer concern to current evidence, required action, linked experiment/run/analysis/change IDs, changed file/location, verification, and reply. Perform and verify the change before declaring the response ready. The researcher approves a separate Gate 4 release decision for the revision or rebuttal.

## Recovery and resumption

At resumption, read project state and artifact versions, then inspect source-control changes and current files. Do not repeat completed work merely because earlier chat context is absent. Reopen downstream artifacts only when their inputs or assumptions changed.
