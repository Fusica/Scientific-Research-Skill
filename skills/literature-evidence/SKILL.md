---
name: literature-evidence
description: Search, screen, read, and organize academic literature into traceable evidence records, closest-work comparisons, and novelty assessments. Use when doing background research, related-work discovery, systematic or scoped reviews, claim verification, bibliography building, or stress-testing whether a research idea is genuinely differentiated.
---

# Literature Evidence

Build a reproducible evidence base rather than a list of plausible papers.

## Define the search contract

Record research questions, concepts and synonyms, date/venue boundaries, inclusion and exclusion criteria, target databases, stopping rule, and search date. Adapt sources to the domain; for CS and robotics, include preprints and venue repositories while distinguishing peer-reviewed status.

## Search in rounds

1. Seed search for terminology, surveys, and seminal work.
2. Precision search for the proposed mechanism and closest claims.
3. Backward and forward citation traversal.
4. Adversarial search for conflicting results, negative evidence, and alternate terminology.
5. Update search near submission or revision when recency matters.

Keep the exact queries and result counts. Deduplicate by persistent identifier and normalized title.

## Screen and read at the right depth

Separate discovered, screened, included, and deeply read papers. Abstract-only evidence may support discovery or provisional context, but not a strong method comparison or claim about detailed findings. Record access limitations.

## Create evidence records

For each material source, write a record using `references/evidence-record.md`. Include a locator to the supporting passage, table, figure, theorem, or code, plus a paraphrase, relevance, limitations, and confidence. Preserve contradictory evidence.

## Assess closest work and novelty

Compare problem, assumptions, mechanism, training signal, data, evaluation setting, and claimed contribution. State the smallest defensible delta and the conditions under which it disappears. Novelty is a reasoned, confidence-labeled assessment, not a binary fact.

Never classify a paper's contribution type from citation counts. Do not use popularity, stars, or citation volume as a substitute for reading and technical comparison.

## Deliver artifacts

Produce:

- `search_protocol.yaml`;
- `paper_registry.jsonl`;
- `evidence_matrix.jsonl`;
- `closest_work.md`;
- a synthesis that separates consensus, conflict, gaps, and unknowns.

Every statement intended for a paper must point to an evidence ID or be marked as the authors' own result/interpretation.
