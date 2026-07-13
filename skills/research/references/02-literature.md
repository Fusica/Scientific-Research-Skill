# Stage 2: Literature evidence and idea refinement

Build a reproducible evidence base and match reading depth to the statement being supported.

## Register the search contract

Record:

- research questions, candidate claims, concepts, synonyms, and alternate terminology;
- databases, venue repositories, preprint sources, date/venue/language boundaries, and search date;
- inclusion and exclusion criteria;
- exact queries and per-query result counts;
- deduplication rule and stopping rule;
- access limitations, update schedule, and peer-review status where relevant.

## Search in evidence-producing rounds

Search from terminology and seminal work toward the proposed mechanism and closest claims; traverse citations; search adversarially for conflicts, negative results, alternate names, and simpler methods. Refresh only when recency matters. Deduplicate by persistent identifier and normalized title, retaining exclusions with reasons.

## Separate discovery from evidence

Track sources as discovered, screened, included, or deeply read. Metadata and abstracts support discovery or provisional background, not detailed technical comparisons. Record unavailable full text, code, or data.

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

Use `supports`, `contradicts`, `qualifies`, or `background` for direction and `metadata`, `abstract`, `full_text`, `code`, or `data` for reading depth. Maintain source identity, status, persistent links, access/query provenance, screening state, and BibTeX provenance.

## Determine closest work and novelty boundaries

Compare closest sources on:

- problem and assumptions;
- inputs, outputs, observations, and constraints;
- mechanism, objective, supervision, and training signal;
- data, environment, evaluation protocol, and baselines;
- claims, limitations, and released implementation.

State the smallest defensible difference and when it disappears. Report novelty with confidence and search limitations.

## Deliver and hand back

Register one `literature.evidence_base` artifact that links the search protocol, source registry, passage-level evidence matrix, closest-work comparison, and synthesis of consensus, conflicts, gaps, and unknowns. Map existing files instead of duplicating them. Give manuscript-intended external statements evidence IDs or label them as author hypotheses, results, or interpretations.

Return to the idea stage when evidence changes the proposed contribution. Reopen `idea_freeze` when the frozen mechanism or smallest defensible difference no longer holds. Return to the method, experiment, paper, or revision stage with explicit evidence IDs when the search resolves a bounded downstream question.
