# Claude Scholar upstream provenance

- Source: https://github.com/Galaxy-Dawn/claude-scholar
- Branch: `codex`
- Commit: `6fa4540f2ceafeaa5c610532906fec5810ee4e19`
- License: MIT; see [`LICENSE`](LICENSE).
- Selection SHA-256: `a360eea729534895a2c506435f4f8ed1939142526c0bf705e89781281f4e841b`

## Selected modules

The following upstream skill directories are vendored without modification:

- `skills/research-ideation/`
- `skills/results-analysis/`
- `skills/results-report/`
- `skills/publication-chart-skill/`

For `ml-paper-writing`, only the following upstream files are vendored
without modification:

- `skills/ml-paper-writing/SKILL.md`
- `skills/ml-paper-writing/references/`

The upstream `skills/ml-paper-writing/templates/` directory is intentionally
excluded. It contains venue files with independent redistribution terms,
including LPPL-governed files whose required source bundles are not present in
the upstream selection. Venue templates should be obtained from the official
venue source and verified for the active submission year.

## Selection boundary

Other upstream modules were intentionally not vendored in this pass. They were
outside the requested research core, overlapped with local or planned
capabilities, focused on general software/UI/plugin workflows, depended on
optional Zotero/Obsidian or Claude-specific integration surfaces, or were better
treated as reference material rather than part of the initial maintained base.

This directory records a selective snapshot rather than a full mirror of the
upstream repository. Every included upstream file remains byte-for-byte
unchanged.
