# Project Purpose and Scope

Power Grid Data Factory is intended to orchestrate large simulation campaigns for:

- PF
- DC-OPF
- AC-OPF
- SCOPF

The goal is to produce datasets for foundation-model training while preserving full provenance and reproducibility.

## What the code is meant to do

The project provides infrastructure to:

1. Register and materialize grid cases and scenario variants.
2. Generate and track topology perturbations and operating-point perturbations.
3. Run solver workflows across multiple solver frameworks.
4. Preserve complete run artifacts for successful and unsuccessful runs.
5. Validate physical consistency and record validation status.
6. Maintain queryable run registries and integrity manifests.

## Explicitly out of scope for current scaffold

The current scaffold is not yet a full production runner for all solver workflows. It includes:

- Core directory and naming APIs.
- Preservation and integrity utilities.
- Solver adapter interfaces and placeholders.
- Script entrypoints and configuration templates.

Additional implementation work is still required for complete solver execution and high-scale HPC workflows.

## Governing principles

- Never mix results across tasks.
- Never overwrite prior attempts.
- Keep DC approximations explicitly labeled as DC approximations.
- Keep raw solver-native files intact.
- Separate raw, intermediate, normalized, and derived products.
