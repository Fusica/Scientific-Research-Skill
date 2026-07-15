# Stage 2: Literature evidence and idea refinement

Build a reproducible, append-only evidence base and match reading depth to the statement being supported.

## Use stable working paths and append-only records

Keep one evidence-base artifact with a stable ID, normally at `.research/artifacts/literature/evidence-base.md`, plus append-only registries or JSONL files for search runs, sources, screening decisions, and passage evidence. Prefer stable working paths; an intentional relocation is a new provenance revision under the same ID, not a reason to create `evidence-base-v2`.

Every appended record needs a unique `record_id`. Corrections and changed judgments append a new record with `supersedes: <prior-record-id>` and a reason; never edit or delete the prior record. Preserve supporting, opposing, negative, null, failed, excluded, and contradictory material with its disposition reason. Raw provider snapshots are immutable inputs and receive stable paths and hashes.

## Register the search contract

Use the scholarly retrieval systems available to the project, choosing providers by domain and capability rather than hard-coding one database. Record:

- research questions, candidate claims, concepts, synonyms, and alternate terminology;
- provider, interface, tool version, databases or repositories, date/venue/language boundaries, and search time;
- inclusion and exclusion criteria;
- exact queries, filters, pagination, requested limits, per-query result counts, and provider failures or truncation;
- deduplication rule and stopping rule;
- access limitations, update schedule, and peer-review status where relevant.

Keep a provider-neutral search-run manifest. Store raw provider output under the policy artifact root where permitted, then record its path and hash; never store credentials.

```yaml
record_id: SEARCH-RECORD-001
supersedes: null
search_run_id: SEARCH-RUN-001
purpose: discovery
research_question_ids: []
started_at: ""
completed_at: ""
provider_calls:
  - provider_call_id: SEARCH-CALL-001
    provider: ""
    interface: api
    tool_version: ""
    query: ""
    filters: {}
    pagination: {}
    requested_limit: null
    reported_total: null
    retrieved_count: 0
    status: success
    limitation_or_error: ""
    raw_snapshot:
      retention_status: retained
      path: ""
      content_hash: null
      nonretention_reason: ""
dedup:
  identity_order: [doi, pmid, arxiv_id, provider_id, normalized_title_author]
  input_count: 0
  output_count: 0
  ambiguous_pairs: []
coverage: {unresolved_gaps: [], round_yields: [], stop_reason: ""}
```

## Search in evidence-producing rounds

Search from terminology and seminal work toward the proposed mechanism and closest claims; traverse citations; search adversarially for conflicts, negative results, alternate names, and simpler methods. Make each round answer an explicit information gap, retain query history, and stop with a recorded coverage or resource reason rather than a paper-count target. Provider failures are independent: preserve successful results and record partial coverage.

Deduplicate first by normalized persistent identifiers. Treat title and author similarity as a review candidate, not an automatic deletion; link preprints and published versions instead of silently merging them. Record merge decisions, exclusions, and ambiguous pairs.

## Separate discovery from evidence

Track sources as discovered, screened, included, or deeply read. Metadata and abstracts support discovery or provisional background, not detailed technical comparisons. Record unavailable full text, code, or data. Separate provider output, normalized source records, screening decisions, and passage-level evidence so each derived layer can be regenerated.

Append one material evidence record per line in an evidence matrix. A correction gets a new `record_id` and `supersedes` link rather than replacing the earlier line:

```json
{
  "record_id": "EVIDENCE-RECORD-001",
  "supersedes": null,
  "evidence_id": "EVD-001",
  "source_id": "SRC-001",
  "claim_or_question_id": "CLAIM-CAND-001",
  "evidence_type": "direct",
  "reading_depth": "full_text",
  "locator": {"section": "4.2", "page": 7, "figure": null, "url": null},
  "paraphrase": "",
  "relevance": "",
  "direction": "supports",
  "limitations": [],
  "confidence": "medium",
  "verified_at": "YYYY-MM-DD",
  "verified_by": "agent_or_human"
}
```

Use `supports`, `contradicts`, `qualifies`, or `background` for direction and `metadata`, `abstract`, `full_text`, `code`, or `data` for reading depth. Append source identity, disposition, persistent links, originating search-run and provider-call IDs, screening state, full-text hash where retained, and BibTeX provenance. Exclusion changes require a superseding record; they never erase the original decision.

## Determine closest work and novelty boundaries

Compare closest sources on:

- problem and assumptions;
- inputs, outputs, observations, and constraints;
- mechanism, objective, supervision, and training signal;
- data, environment, evaluation protocol, and baselines;
- claims, limitations, and released implementation.

State the smallest defensible difference and when it disappears. Report novelty with confidence and search limitations.

## Deliver and hand back

Update the evidence-base working document to link the search-run manifests, retained raw-snapshot hashes or documented non-retention reasons, append-only source registry, screening log, passage-level evidence matrix, closest-work comparison, and synthesis of consensus, conflicts, gaps, and unknowns. Register it as `literature.evidence_base` under `policy.artifact_layout`; map existing files instead of duplicating them. Retrieval adapters are optional and no provider is Gate authority. Give manuscript-intended external statements evidence IDs or label them as author hypotheses, results, or interpretations.

Return to the idea stage when evidence changes the proposed contribution. Apply the relevant policy Gate's `reopen_when_changed` contract before altering an approved boundary. Return downstream only through `policy.workflow_graph.stage_transitions` and with evidence IDs for the bounded question it resolves.
