# Power Grid Data Factory

Power-grid simulation data factory for PF, DC-OPF, AC-OPF, and SCOPF campaigns with full provenance and preservation.

This repository is initialized from the planning document:
`Power_Grid_Foundation_Model_Data_Generation_and_Preservation_Plan.docx`.

## Documentation

Full index: [docs/README.md](docs/README.md).

- [Project Purpose and Scope](docs/project_scope.md)
- [Architecture and Data Layout](docs/architecture.md)
- [Environment and Setup Guide](docs/setup.md)
- [External Dependencies and Data Sources](docs/external_sources.md)
- [ExaGO Frontier Build Notes](docs/exago_frontier_build.md)
- [ExaGO Andes CPU Build and Run](docs/exago_andes_cpu_build.md)
- [Reproducibility Workflow](docs/reproducibility.md)
- [Script Reference](docs/scripts_reference.md)
- [First Reproducible Run Walkthrough](docs/first_reproducible_run.md)
- [First Real-Case Input Run with Registry Append](docs/first_real_case_run.md)
- [Three-Solver Runbook (ExaGO + pandapower + PowerModels)](docs/three_solver_runbook.md)
- [Production Readiness Checklist](docs/production_readiness_checklist.md)
- [Adaptive Campaign Strategy (Default)](docs/adaptive_campaign_strategy.md)
- [Resumable Campaigns and the Top-Level Driver](docs/resumable_campaigns.md)
- [Enumeration-Time Feasibility Prefiltering](docs/feasibility_prefiltering.md)
- [Configuration Reference](docs/configuration_reference.md)
- [Schema Contracts](docs/schema_contracts.md)
- [GO Challenge MATPOWER Duplicate Audit](docs/go_challenge_duplicate_audit.md)
- [Evolution Log](docs/evolution_log.md)

## Core principles

- Keep task outputs separated under `data/runs/pf`, `data/runs/dc_opf`, `data/runs/ac_opf`, and `data/runs/scopf`.
- Preserve complete attempt artifacts (inputs, raw outputs, logs, intermediate files, normalized outputs, validation, manifests, checksums).
- Never overwrite attempts; append immutable `attempt_<six-digit-index>` directories.
- Keep DC outputs explicitly marked as approximations (`physical_fidelity: dc_approximation`).
- Do not use pandapower in production paths.

## Default data-generation strategy

The default strategy is an adaptive, coverage-constrained, multi-fidelity campaign.

- Candidate selection uses independent acquisition queues with explicit quota budgets.
- DC calculations are treated as screening features, not unilateral accept/reject gates.
- Screening rejects are audited with stratified AC samples.
- Post-solve diversity, active-constraint, and security-boundary ledgers are first-class artifacts.

See `docs/adaptive_campaign_strategy.md` and `configs/campaign_default.yaml`.

## Current scaffold scope

This initial codebase provides:

- Deterministic naming and run-layout APIs.
- Attempt directory creation with atomic finalization conventions.
- Artifact manifest and checksum generation.
- Integrity verification and preservation audit scripts.
- Global run registry append interface.
- Solver interface with adapters:
  - `PowerModelsAdapter` (implemented as process wrapper stub)
  - `ExaGOAdapter` (stub)
- Julia project skeleton for PowerModels runners.

MATPOWER is used in this project as a case-data format and external reference source, not as an active solver backend.

## Quick start

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
python -m venv .venv
source .venv/bin/activate
pip install -e .

python scripts/validate_run_layout.py --runs-root data/runs
```

## Required hierarchy

- `data/runs/<task>/<case_id>/topologies/<topology_id>/operating_points/<operating_point_id>/solvers/<solver_id>/attempts/<attempt_id>/`
- SCOPF inserts `contingency_sets/<contingency_set_id>/` before solver level.

## Terminal markers

Exactly one marker file must exist per finalized attempt:

- `SUCCESS`
- `FAILED`
- `TIMEOUT`
- `CANCELED`
- `NODE_FAILURE`
- `INFEASIBLE`
- `NONCONVERGENT`
- `INVALID_INPUT`
- `ISLANDED`
- `SOLVER_SUCCESS_PRESERVATION_FAILED`

## Scripts

- `scripts/finalize_attempt.py`
- `scripts/verify_attempt_integrity.py`
- `scripts/archive_attempt.py`
- `scripts/verify_archive.py`
- `scripts/audit_preservation.py`
- `scripts/validate_run_layout.py`

## Notes

- Solver execution wrappers are intentionally conservative and preservation-first.
- Add site-specific HPC launch logic in `configs/slurm/` and `src/grid_data_factory/solvers/`.
- Keep all schema/data model changes backward-compatible and versioned.

## External solver sources

- ExaGO cloned under `external/ExaGO`
- Remote: `https://github.com/ORNL/ExaGO`
- Current commit: `545a8deb6fa35552f0ee402ca83672fe1255f61a`

## GO Challenge source provenance

- GO Challenge Challenge 1 zip archives under `external/go_challenge1/raw/` are preserved original downloads from the official DOE Data Catalog entry:
  - https://catalog.data.gov/dataset/arpa-e-grid-optimization-go-competition-challenge-1?utm_source=chatgpt.com
- These archives are converted into MATPOWER `.m` files via `scripts/convert_go_challenge_to_matpower.py`.
- Conversion diagnostics and summary counts are written to `data/analysis/go_challenge1_conversion_report.json`.
