# Architecture and Data Layout

## Repository structure

- `configs/`: campaign and solver configuration files.
- `external/`: cloned or downloaded external repositories and datasets.
- `data/`: canonicalized inputs, registries, run artifacts, and derived outputs.
- `src/grid_data_factory/`: Python package implementing layout, adapters, validation, and preservation logic.
- `julia/`: Julia environment and runner scripts for PowerModels workflows.
- `scripts/`: executable workflow commands and preservation utilities.

## Run hierarchy

For PF, DC-OPF, and AC-OPF:

`data/runs/<task>/<case_id>/<topology_id>/<operating_point_id>/<solver_id>/attempts/<attempt_id>/`

For SCOPF:

`data/runs/scopf/<case_id>/<topology_id>/<operating_point_id>/<contingency_set_id>/<solver_id>/attempts/<attempt_id>/`

Layout note:

- Run artifacts use the compact hierarchy by default.

## Attempt lifecycle

1. Create a unique in-progress attempt directory.
2. Materialize resolved inputs.
3. Execute solver in isolated `work/` subdirectories.
4. Collect full stdout, stderr, and native outputs.
5. Generate normalized views without mutating raw outputs.
6. Build artifacts manifest and checksums.
7. Verify integrity.
8. Write exactly one terminal marker.
9. Atomically finalize attempt directory.

## Core modules

- `storage/layout.py`: deterministic paths and attempt directory creation/finalization.
- `storage/naming.py`: stable naming conventions for topology, operating-point, and attempt IDs.
- `storage/registry.py`: append run records to JSONL and Parquet registries.
- `preservation/artifacts.py`: artifact manifest generation.
- `preservation/checksums.py`: checksum generation and verification.
- `preservation/archive.py`: archive creation and archive verification.
- `preservation/audit.py`: preservation audit across run trees.
- `solvers/base.py`: common solver protocol.
- `solvers/powermodels_adapter.py`: Julia process wrapper for PowerModels tasks.
- `solvers/exago_adapter.py`: ExaGO adapter scaffold.

MATPOWER in this codebase is treated as an input case format and reference dataset source, not a maintained solver execution backend.
