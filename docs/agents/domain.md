# Domain docs

This repository has one maintainer-domain context and already has an established
documentation layout. These files guide engineering work; they are not Plugin
runtime policy or project research state.

## Before exploring

Read:

- `decisions/glossary.md` for canonical terms and the distinction between current,
  intended, deferred, and explicitly unsupported capability;
- the numbered Markdown files under `decisions/` whose scope touches the work;
- `AGENTS.md` for repository architecture and scientific boundaries.

## Canonical layout

- **Glossary**: `decisions/glossary.md`.
- **ADRs**: `decisions/<NNNN>-<slug>.md`.

Do not create `CONTEXT.md`, `CONTEXT-MAP.md`, or `docs/adr/`. They would duplicate
the existing glossary and ADR authority and conflict with this repository's
single-authority rule.

When `/domain-modeling` resolves a term, update `decisions/glossary.md`. When a
decision meets the ADR threshold, write the next numbered file under `decisions/`.

## Use the glossary vocabulary

Use the canonical term in issue titles, specifications, tests, code, and review
findings. If a needed term is absent, either reconsider the new wording or record
the genuine gap through `/domain-modeling`.

## Flag ADR conflicts

If proposed work contradicts an accepted ADR, surface the conflict explicitly and
decide whether to preserve, reopen, or supersede that ADR before implementation.
Never silently override it in a ticket or code change.
