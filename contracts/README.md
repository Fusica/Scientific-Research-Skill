# Research artifact contracts

`artifact-catalog.yaml` is the canonical path and ownership catalog. Copy the relevant templates into a project's `.research/` directory, or map equivalent existing artifacts in `project-state.yaml`.

The files are intentionally human-readable. YAML stores decisions and bounded registries; JSONL stores append-friendly literature and run records; Markdown stores mathematical and narrative contracts. Stable IDs, artifact versions, and content hashes connect stages.

## Gate authority

`.research/project-state.yaml` is the sole authority for gate status. Scientific artifacts contain a `gate_ref` but do not approve themselves. Each approval/reopen decision binds the gate to artifact IDs, versions, and content hashes in `project-state.gate_decisions`.

## Catalog

| Template | Canonical destination |
| --- | --- |
| `project-state.template.yaml` | `.research/project-state.yaml` |
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

Templates define a minimum, not a ceiling. Domain profiles may add required fields but should not rename stable IDs, remove provenance, or create a second gate authority.
