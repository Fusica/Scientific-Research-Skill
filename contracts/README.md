# Research artifact contracts

`artifact-catalog.yaml` is the canonical path and ownership catalog. Copy the relevant templates into a project's `.research/` directory, or map equivalent existing artifacts in `project-state.yaml`. Use `planning/` templates to initialize the default task execution bundle under `.planning/<task-id>/`.

The files are intentionally human-readable. YAML stores decisions and bounded registries; JSONL stores append-friendly literature and run records; Markdown stores mathematical and narrative contracts. Stable IDs, artifact versions, and content hashes connect stages.

## Gate authority

`.research/project-state.yaml` is the sole authority for gate status. Scientific artifacts contain a `gate_ref` but do not approve themselves. Each approval/reopen decision binds the gate to artifact IDs, versions, and content hashes in `project-state.gate_decisions`.

`.research/project-overview.md` is a derived landing page. It mirrors pointers and bounded summaries for fast recovery, but cannot approve a Gate or serve as the sole basis of one. `.planning/` files are execution coordination records; verified content must be promoted to the canonical `.research/` artifact before it becomes scientific evidence.

Do not write the overview's content hash or artifact version back into `project-state.artifact_registry`; project state is an input to the overview, so that registration would create a circular update. The artifact catalog is sufficient to define its canonical location and producer.

## Catalog

| Template | Canonical destination |
| --- | --- |
| `project-state.template.yaml` | `.research/project-state.yaml` |
| `project-overview.template.md` | `.research/project-overview.md` |
| `gate-decision-record.template.yaml` | record appended to `project-state.gate_decisions` |
| `idea-card.template.yaml` | `.research/idea/idea_card.yaml` |
| `search-protocol.template.yaml` | `.research/literature/search_protocol.yaml` |
| `paper-record.template.json` | one line in `.research/literature/paper_registry.jsonl` |
| `evidence-record.template.json` | one line in `.research/literature/evidence_matrix.jsonl` |
| `method-contract.template.md` | `.research/method/method_contract.md` |
| `experiment-matrix.template.yaml` | `.research/experiments/experiment_matrix.yaml` |
| `run-record.template.json` | one line in `.research/experiments/run_registry.jsonl` |
| `experiment-decision-record.template.yaml` | record in `.research/experiments/decision_log.yaml` |
| `analysis-record.template.yaml` | record in `.research/results/analysis_registry.yaml` |
| `artifact-manifest-record.template.yaml` | record in `.research/results/artifact_manifest.yaml` |
| `claim-ledger.template.yaml` | `.research/results/claim_ledger.yaml` |
| `paper-claim-map.template.yaml` | `.research/paper/paper_claim_map.yaml` |
| `paper-change-map.template.yaml` | `.research/paper/paper_change_map.yaml` |
| `review-map.template.yaml` | `.research/revision/review_map.yaml` |
| `revision-change-record.template.yaml` | record in `.research/revision/revision_change_log.yaml` |

## Planning templates

| Template | Canonical destination |
| --- | --- |
| `planning/task-plan.template.md` | `.planning/<task-id>/task_plan.md` |
| `planning/findings.template.md` | `.planning/<task-id>/findings.md` |
| `planning/progress.template.md` | `.planning/<task-id>/progress.md` |

Planning files may use only execution states such as `pending`, `in_progress`, `awaiting_user`, `blocked`, `completed`, and `superseded`. They never use scientific `approved` status.

Templates define a minimum, not a ceiling. Domain profiles may add required fields but should not rename stable IDs, remove provenance, or create a second gate authority.
