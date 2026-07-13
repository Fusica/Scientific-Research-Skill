# Architecture

## Four project-state layers

The repository separates stable scientific authority from noisy execution state:

```text
runtime memory/todos (advisory cache)
        ↓
.research/project-overview.md (derived navigation)
        ↓ verify against
.research/project-state.yaml + .research/** (scientific authority)
        ↑ verified promotion
.planning/<task-id>/** (default execution coordination)
```

Runtime memory and todo tools may help recover context, but project-local files win. Planning produces candidate results; verification promotes them into research artifacts; only project state records scientific Gate decisions.

### 1. Composition layer: `skills/`

These are the maintained Codex-native skills. They define routing, scientific gates, artifact contracts, and safe defaults. They must remain usable without executing vendored scripts.

### 2. Contract/profile layer: `contracts/` and `profiles/`

Contracts make stage handoffs explicit. Profiles specialize protocols for a domain, venue, project, or agent runtime without rewriting the core workflow.

`contracts/artifact-catalog.yaml` is the canonical role/path catalog.
`.research/project-state.yaml` is the sole gate authority; artifact-local
`gate_ref` fields are references, not approvals.

`.research/project-overview.md` is a derived navigation view. It mirrors current stage, Gate decision IDs, artifact pointers, bounded claims, constraints, terminology, open decisions, and active planning tasks. It stores no independent approval or raw evidence.

`.planning/<task-id>/` is the default execution layer for non-trivial research tasks. Its plan, findings, and progress files may be provisional and recoverable; they cannot approve a Gate or replace a scientific artifact.

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
- **Default file-based execution state:** non-trivial tasks use a recoverable planning bundle without mixing transient work into the scientific record.
- **Derived project overview:** one bounded landing page accelerates resumption while preserving project state as the authority.
- **Human-supervised gates:** agents prepare evidence and recommendations; the researcher owns high-impact decisions.
- **No universal experimental threshold:** domain profiles define units, repetitions, uncertainty, and safety requirements.
- **Traceability before automation:** automation may be added only when its outputs retain source/run/config provenance.

## Memory boundary

Project facts, current runs, decisions, and claims belong in `.research/`. Current task steps, temporary diagnostics, and agent handoffs belong in `.planning/`. Cross-project memory should contain distilled, reviewed lessons and preferences, not project status, raw logs, open todos, or unverified results. Promoting an observation into cross-project memory should remain an explicit human-reviewed action.
