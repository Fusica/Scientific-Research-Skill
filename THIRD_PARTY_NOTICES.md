# Third-party notices

This repository contains selective, pinned upstream snapshots under `vendor/`. The local runtime composition under `skills/research/`, `hooks/`, and `scripts/` is maintained by this project. Vendored material is provenance reference only and is not registered as a runtime Skill.

## Galaxy-Dawn/claude-scholar

- Source: https://github.com/Galaxy-Dawn/claude-scholar
- Snapshot: Codex branch commit `6fa4540f2ceafeaa5c610532906fec5810ee4e19`
- License: MIT
- Preserved license: `vendor/claude-scholar/LICENSE`
- Provenance and selected paths: `vendor/claude-scholar/UPSTREAM.md`

The upstream `ml-paper-writing/templates/` tree is not redistributed because
it contains files governed by separate venue and LPPL terms. Obtain current
templates from each venue's official source.

## EvoScientist/EvoSkills

- Source: https://github.com/EvoScientist/EvoSkills
- Snapshot: commit `29e2c67f12858829ad0900645432b340c3f77522`
- License: Apache License 2.0
- Preserved license: `vendor/evoskills/LICENSE`
- Provenance, selected paths, and caveats: `vendor/evoskills/UPSTREAM.md`

The EvoScientist runtime repository was audited at commit `49770949daa7ca4ef4744a2f089100f8b872b869`, but no runtime source is included. Its official companion EvoSkills contains the research skills selected for this repository. See `vendor/evoscientist/NOTICE.md`.

## Yuan1z0825/nature-skills

- Source: https://github.com/Yuan1z0825/nature-skills
- Snapshot: commit `4170a8a6262642841699c55d468e21ff70a2fe34`
- License: Apache License 2.0
- Preserved license: `vendor/nature-skills/LICENSE`
- Provenance and selected paths: `vendor/nature-skills/UPSTREAM.md`

## lingzhi227/agent-research-skills

No source files from the audited snapshot are included because no repository-level license was found at commit `9e6c085d65e313e475e921fdfe795ac11eb7589e`. General research-workflow ideas were independently re-expressed in the local composition layer; see `vendor/agent-research-skills/NOTICE.md`.

## Updating

Before changing a vendored snapshot, verify the upstream license and commit, keep the copied files unmodified, update `upstreams.lock.yaml`, and review local composition behavior separately.
