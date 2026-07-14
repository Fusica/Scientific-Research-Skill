# Stage 2: Literature evidence and idea refinement

Build a reproducible evidence base and match reading depth to the statement being supported.

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

Write one material evidence record per line in an evidence matrix:

```json
{
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

Use `supports`, `contradicts`, `qualifies`, or `background` for direction and `metadata`, `abstract`, `full_text`, `code`, or `data` for reading depth. Maintain source identity, status, persistent links, originating search-run and provider-call IDs, screening state, full-text hash where retained, and BibTeX provenance.

## Determine closest work and novelty boundaries

Compare closest sources on:

- problem and assumptions;
- inputs, outputs, observations, and constraints;
- mechanism, objective, supervision, and training signal;
- data, environment, evaluation protocol, and baselines;
- claims, limitations, and released implementation.

State the smallest defensible difference and when it disappears. Report novelty with confidence and search limitations.

## Deliver and hand back

Register one `literature.evidence_base` artifact that links the search-run manifests, retained raw-snapshot hashes or documented non-retention reasons, source registry, screening log, passage-level evidence matrix, closest-work comparison, and synthesis of consensus, conflicts, gaps, and unknowns. Map existing files instead of duplicating them. Retrieval adapters are optional and only need to emit this contract; no provider is Gate authority. Give manuscript-intended external statements evidence IDs or label them as author hypotheses, results, or interpretations.

Return to the idea stage when evidence changes the proposed contribution. Reopen `idea_freeze` when the frozen mechanism or smallest defensible difference no longer holds. Return to the method, experiment, paper, or revision stage with explicit evidence IDs when the search resolves a bounded downstream question.
