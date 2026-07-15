# Scientific Research Skill development rules

## Mission

Maintain one small Codex plugin that turns an explicitly enabled repository into a
traceable research workspace. Prefer a clear scientific boundary over additional
workflow machinery.

This `AGENTS.md` is repository-scoped maintainer guidance. It is not an installed
Plugin component, a system-wide instruction file, or a source of runtime policy.

## Runtime architecture

- `skills/research/SKILL.md` is the only public Skill entry point.
- `skills/research/references/policy.yaml` is the only workflow and Gate policy.
- The six numbered references describe stage-specific execution; they do not define
  independent policy or register additional Skills.
- `scripts/researchctl.py` is the only supported writer for `.research/state.json`
  artifact, Gate, and checkpoint metadata.
- `hooks/` may read project state and deny supported tool calls, but must never claim
  universal interception or scientific correctness.
- A project is active only when `.research/state.json` exists and `enabled` is true.
  Outside that boundary every Hook must emit `{}`.

Do not introduce project-overview files, a second state format, or Codex-global
memory. Project memory belongs only in `.research/memory.md` and is local to the
current worktree by default. The generated `.research/dashboard.html` is an
explicit exception: it is a disposable, read-only projection of canonical state,
not an overview authority, state format, or memory store.

## Scientific boundaries

- Never infer human approval for `idea_freeze`,
  `method_experiment_approval`, `claim_freeze`, or `release`.
- Preserve negative, failed, excluded, and contradictory evidence with reasons.
- Do not fabricate citations, results, metadata, code behavior, verification, or
  completed actions.
- Keep judgment-based checks honest: Hooks can enforce mechanical boundaries and
  request semantic review, but cannot guarantee novelty, validity, or paper quality.
- External submission, publishing, destructive data operations, costly compute, and
  safety-relevant hardware execution still require the authority implied by the
  user's request or explicit confirmation.

## Repository changes

- Do not vendor upstream repositories or keep local source snapshots. External
  reference repositories belong only as direct links in `README.md`; maintained
  behavior belongs in `skills/research`, `scripts`, and `hooks`.
- Keep `SKILL.md` concise and load only the current stage reference.
- Change the shared policy rather than duplicating a rule in a stage reference,
  Hook, command, or README.
- Use parallel agents only for independent paths, then review the integrated diff.
- Preserve the root license. Re-express externally inspired behavior locally rather
  than copying upstream source into this repository.

## Required validation

Run all of the following before handing off a change:

```bash
python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -v
node --test tests/hooks.test.js
```

For plugin-facing changes, also run the official plugin validator and validate
`skills/research` with the official Skill quick validator.
