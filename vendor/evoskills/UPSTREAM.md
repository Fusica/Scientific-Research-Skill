# EvoSkills upstream snapshot

- Repository: https://github.com/EvoScientist/EvoSkills
- Commit: `29e2c67f12858829ad0900645432b340c3f77522`
- License: Apache License 2.0; the upstream `LICENSE` is preserved in this directory.
- Selection SHA-256: `27a08462e4ba07ca56428925f3c8963f5ac6cd376e89b5f4405b483aab6d11c8`

## Vendored skills

- `research-ideation`
- `paper-navigator`
- `paper-planning`
- `experiment-pipeline`
- `experiment-craft`
- `experiment-iterative-coder`
- `evo-memory`

The skill directories above are copied verbatim from the pinned upstream commit.

## Known caveats

- Memory path drift: the vendored `evo-memory` workflow uses `/memory/...`, while current EvoScientist persistent memory is mounted at `/memories/...`. Do not assume these paths share a backend; reconcile them in the integration layer.
- Do not use the citation-count-based novelty classifier in `paper-navigator/scripts/literature_report.py`. Citation counts do not establish a paper's contribution type or novelty; replace it with evidence-based closest-work and claim comparison.
- Generic experiment thresholds and attempt budgets in `experiment-pipeline` are not universal scientific defaults. Domain and project profiles must override them, especially for RL, LLM evaluation, and UAV/control experiments.
