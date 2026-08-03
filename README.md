# Power Grid Data Factory

Power-grid simulation data factory for PF, DC-OPF, AC-OPF, and SCOPF campaigns with full provenance and preservation.

This repository is initialized from the planning document:
`Power_Grid_Foundation_Model_Data_Generation_and_Preservation_Plan.docx`.

## Documentation

- `docs/README.md`
- `docs/project_scope.md`
- `docs/architecture.md`
- `docs/setup.md`
- `docs/external_sources.md`
- `docs/reproducibility.md`
- `docs/scripts_reference.md`
- `docs/production_readiness_checklist.md`
- `docs/first_reproducible_run.md`
- `docs/first_real_case_run.md`

## Core principles

- Keep task outputs separated under `data/runs/pf`, `data/runs/dc_opf`, `data/runs/ac_opf`, and `data/runs/scopf`.
- Preserve complete attempt artifacts (inputs, raw outputs, logs, intermediate files, normalized outputs, validation, manifests, checksums).
- Never overwrite attempts; append immutable `attempt_<six-digit-index>` directories.
- Keep DC outputs explicitly marked as approximations (`physical_fidelity: dc_approximation`).
- Do not use pandapower in production paths.

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
  - `MatpowerAdapter` (optional stub)
- Julia project skeleton for PowerModels runners.

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
