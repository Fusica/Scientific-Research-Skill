# Literature evidence records

Write one JSON object per line in `evidence_matrix.jsonl`.

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

Allowed directions are `supports`, `contradicts`, `qualifies`, and `background`. Allowed reading depths are `metadata`, `abstract`, `full_text`, `code`, and `data`.

The paper registry should include title, authors, year, venue/status, DOI/arXiv/OpenReview or other persistent identifier, canonical URL, access date, search query IDs, inclusion state, and BibTeX provenance.

A closest-work comparison should use technical axes such as problem, assumptions, inputs, mechanism, objective, supervision/training signal, outputs, data, evaluation, and claims. Never derive contribution type from citation count.
