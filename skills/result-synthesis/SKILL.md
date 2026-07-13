---
name: result-synthesis
description: Validate registered experimental outputs, perform appropriate statistical analysis, create publication artifacts, and promote only supported findings into an auditable claim ledger. Use when aggregating runs, comparing methods, estimating uncertainty, interpreting negative results, making figures or tables, or deciding which conclusions are defensible.
---

# Result Synthesis

Turn registered runs into calibrated claims. Analysis cannot repair an invalid experimental unit, missing provenance, or selective run inclusion.

## Audit inputs

Confirm that included and excluded runs follow the registered criteria. Check code/data/config versions, metric definitions, statistical unit, missingness, failure handling, and independence assumptions. Stop and report if provenance is insufficient for the requested claim.

## Analyze at the correct level

Choose summaries and tests from the data-generating process and domain profile. Report uncertainty intervals and effect sizes where meaningful; handle repeated measures, multiple comparisons, non-normality, and high-variance settings explicitly. Distinguish pre-specified from exploratory analyses.

Show distributions or paired changes when an average hides important variation. Preserve null, negative, and conflicting results.

## Generate traceable artifacts

Every table cell and plotted value must resolve to analysis code, input run IDs, and an output artifact. Captions state the statistical unit, aggregation, uncertainty, and number of independent repetitions. Avoid visual encodings that imply unsupported precision.

## Promote claims

Use `references/claim-ledger.md`. Classify each candidate as:

- **unassessed:** registered but not yet audited for promotion;
- **supported:** directly backed by adequate registered evidence;
- **bounded:** supported only under stated settings or assumptions;
- **exploratory:** observed after flexible analysis and requiring confirmation;
- **unsupported or contradicted:** not available for affirmative manuscript claims.

Record origin claim/prediction/experiment IDs, allowed wording, forbidden
stronger wording, evidence IDs, uncertainty, limitations, and linked
figures/tables. A new claim always starts as `unassessed`. Human approval is
required to freeze the ledger before manuscript assembly.

## Deliver artifacts

Produce `analysis_registry.yaml`, `artifact_manifest.yaml`, a concise
results report, and `claim_ledger.yaml`. Each analysis record fixes included
and excluded runs, analysis code/config, statistical unit, estimand, and output
artifact IDs. List missing experiments separately from writing improvements.
