# Artifact chain and approval gates

Use a project-local `.research/` directory unless the user or repository already defines an equivalent location. In this repository, `contracts/artifact-catalog.yaml` is the machine-readable catalog.

| Stage | Canonical artifacts | Produced by | Approval |
| --- | --- | --- | --- |
| Intake/state | `project-state.yaml` | research-orchestrator | sole gate authority |
| Intake/navigation | `project-overview.md` | research-orchestrator | derived view; no approval authority |
| Idea | `idea/idea_card.yaml` | idea-evolution | Gate 1: idea freeze |
| Literature | `literature/search_protocol.yaml`, `paper_registry.jsonl`, `evidence_matrix.jsonl`, `closest_work.md` | literature-evidence | supports Gate 1 |
| Method | `method/method_contract.md` | method-formalization | Gate 2 |
| Experiment | `experiments/experiment_matrix.yaml`, `run_registry.jsonl`, `decision_log.yaml` | experiment-lifecycle | Gate 2 |
| Results | `results/analysis_registry.yaml`, `artifact_manifest.yaml`, `claim_ledger.yaml` | result-synthesis | Gate 3 |
| Paper | `paper/paper_claim_map.yaml`, `paper_change_map.yaml` | paper-production | Gate 4: initial release |
| Revision | `revision/review_map.yaml`, `revision_change_log.yaml` | review-revision | Gate 4: revision release |

## Gate authority

`.research/project-state.yaml` is the only source of truth for gate status. Artifacts may carry a `gate_ref`, but never an approval flag that can drift from project state.

Each approve or reopen decision records:

- a stable decision ID, gate, action, release target where relevant, actor, and timestamp;
- every artifact ID, version, and content hash used for the decision;
- the reason, reopened artifacts, and downstream impacts.

Gate summaries point to the latest decision ID. Allowed states are `pending`, `approved`, `reopened`, and `not_applicable`. Gate 4 supports independent initial-submission and revision/rebuttal release decisions.

`project-overview.md` mirrors project identity, scope, current stage, Gate decision IDs, canonical artifact pointers, bounded claim summaries, terminology, constraints, open decisions, and active planning tasks. It never creates approval, stores raw evidence, or replaces the claim ledger. Refresh it after material project-state or active-task changes; project state wins on conflict.

## Artifact and stage state

The project state maps authoritative scientific roles to their actual paths and stores artifact schema version, artifact version, content hash, and status. It also records active stages and transition history. Existing project files can satisfy a role without being renamed.

The derived overview remains cataloged but is not hash/version registered back into project state. This avoids a circular update where a state change regenerates overview and the overview hash then forces another state version.

## Execution state

Every non-trivial research task uses `.planning/<task-id>/task_plan.md`, `findings.md`, and `progress.md`. This bundle is the task execution authority but has no scientific Gate authority. Verified results move from planning into the canonical `.research/` artifact; they do not become evidence by remaining in a planning file.

## Feedback edges

- New closest work can reopen the idea and method.
- A failed integrity or baseline check blocks the proposed-method stage.
- Results that contradict assumptions return to method formalization or idea evolution.
- A claim gap returns to experiment design; wording alone cannot close it.
- Reviewer feedback can reopen literature, analysis, experiment, method, or paper stages.

Keep stable IDs when an artifact is revised. Increment its version, recompute its content hash, record upstream/downstream impacts, and reopen any gate whose approved content changed.
