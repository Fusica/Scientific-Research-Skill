# nature-skills upstream provenance

- Upstream repository: https://github.com/Yuan1z0825/nature-skills
- Vendored commit: `4170a8a6262642841699c55d468e21ff70a2fe34`
- Upstream license: Apache License 2.0 (preserved as `LICENSE` in this directory)
- Selection SHA-256: `a556ffcdf782f962ee6c0cd996698ddd12fc79f00b10b652ff2a39cb6f301a3a`

## Included verbatim

- `skills/_shared/`
- `skills/nature-writing/`
- `skills/nature-response/`
- `skills/nature-statistics/`

The directories above were copied without local modifications from the pinned
upstream commit. This file records provenance and the selection policy only.

## Intentionally excluded

- `skills/nature-figure/` and its large figure assets, to keep the vendored
  baseline lightweight.
- `skills/nature-proposal-writer/`, because the public upstream version depends
  on materials and companion skills that are not fully included.
- `skills/nature-literature-pipeline/`, because it is not a self-contained
  executable pipeline and relies on external skills and delivery systems.
- All other upstream skills and repository-level assets are outside the current
  focused writing, response, statistics, and shared-contract baseline.

When updating this vendor snapshot, review upstream changes and licenses first,
then update the pinned commit and this inclusion/exclusion record together.
