# Architecture

## Three layers

### 1. Composition layer: `skills/`

These are the maintained Codex-native skills. They define routing, scientific gates, artifact contracts, and safe defaults. They must remain usable without executing vendored scripts.

### 2. Contract/profile layer: `contracts/` and `profiles/`

Contracts make stage handoffs explicit. Profiles specialize protocols for a domain, venue, project, or agent runtime without rewriting the core workflow.

`contracts/artifact-catalog.yaml` is the canonical role/path catalog.
`.research/project-state.yaml` is the sole gate authority; artifact-local
`gate_ref` fields are references, not approvals.

Precedence is:

```text
project decision > active domain/venue profile > core skill rule > upstream example
```

A higher-precedence choice must not weaken citation truthfulness, run provenance, human release gates, or safety requirements.

### 3. Provenance layer: `vendor/`

Vendored modules are pinned, license-preserving reference snapshots. They are not silently patched. Local adaptations belong in `skills/`; provenance and known caveats belong in each `UPSTREAM.md`.

## Design decisions

- **Claude Scholar as outer skeleton:** it provides strong evidence, report, and writing contracts.
- **EvoSkills as loop donor:** it contributes iterative ideation, experiment staging, diagnosis, and memory concepts.
- **Nature Skills as publication donor:** it contributes statistical, writing, and reviewer-response discipline.
- **Clean-room formalization:** selected ideas from agent-research-skills are re-expressed locally because the audited snapshot had no repository license.
- **Project-local research state:** durable facts live with the project and can be version controlled.
- **Human-supervised gates:** agents prepare evidence and recommendations; the researcher owns high-impact decisions.
- **No universal experimental threshold:** domain profiles define units, repetitions, uncertainty, and safety requirements.
- **Traceability before automation:** automation may be added only when its outputs retain source/run/config provenance.

## Memory boundary

Project facts, current runs, decisions, and claims belong in `.research/`. Cross-project memory should contain distilled, reviewed lessons and preferences, not unverified project state. Promoting an observation into cross-project memory should remain an explicit human-reviewed action.
