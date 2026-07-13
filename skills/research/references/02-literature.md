# Stage 2: Literature evidence and idea refinement

Build a reproducible evidence base, not a list of plausible papers. Match reading depth to the strength of the statement being supported.

## Register the search contract

Before searching, record:

- research questions, candidate claims, concepts, synonyms, and alternate terminology;
- databases, venue repositories, preprint sources, date/venue/language boundaries, and search date;
- inclusion and exclusion criteria;
- exact queries and per-query result counts;
- deduplication rule and stopping rule;
- access limitations and update schedule.

For CS and robotics, include relevant preprints, venue repositories, and released code while recording peer-review status separately.

## Search in evidence-producing rounds

1. Find terminology, surveys, and seminal work.
2. Search precisely for the proposed mechanism and closest claims.
3. Traverse backward and forward citations from material sources.
4. Search adversarially for negative results, conflicts, alternate names, and simpler methods.
5. Refresh the search near submission or revision when recency matters.

Deduplicate by persistent identifier and normalized title. Preserve excluded sources with reasons.

## Separate discovery from evidence

Track sources as discovered, screened, included, or deeply read. Metadata and abstracts may support discovery and provisional background, but not detailed comparisons of objectives, architectures, assumptions, experiments, or findings. Record when full text, code, or data was unavailable.

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

Use `supports`, `contradicts`, `qualifies`, or `background` for direction and `metadata`, `abstract`, `full_text`, `code`, or `data` for reading depth. Prefer a precise paraphrase and locator over long quotation.

Maintain source records with title, authors, year, venue/status, persistent identifier, canonical URL, access date, query IDs, screening state, and BibTeX provenance. Verify metadata from primary publication pages or records when possible.

## Determine closest work and novelty boundaries

Compare sources on the actual technical axes:

- problem and assumptions;
- inputs, outputs, observations, and constraints;
- mechanism, objective, supervision, and training signal;
- data, environment, evaluation protocol, and baselines;
- claims, limitations, and released implementation.

State the smallest defensible difference and the conditions under which it disappears. Preserve both confirming and conflicting evidence. Describe novelty as an assessment with confidence and search limitations, never as an established binary fact.

## Deliver and hand back

Maintain canonical equivalents of:

- a search protocol;
- a source or paper registry;
- a passage-level evidence matrix;
- a closest-work comparison;
- a synthesis separating consensus, conflicts, gaps, and unknowns.

Map existing project files to these roles instead of duplicating them. Give every manuscript-intended external statement an evidence ID; otherwise label it as the authors' hypothesis, result, or interpretation.

Return to the idea stage when evidence changes the proposed contribution. Reopen `idea_freeze` when the frozen mechanism or smallest defensible difference no longer holds. Return to the method, experiment, paper, or revision stage with explicit evidence IDs when the search resolves a bounded downstream question.
