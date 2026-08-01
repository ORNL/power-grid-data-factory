# Environment and Setup Guide

## Prerequisites

- Python 3.10+ (3.11 recommended on this system).
- Git.
- Julia for PowerModels workflows.
- Sufficient filesystem quota for run artifacts.

## Create a Python environment

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

If your system `python` is older, use a known Python 3.11 interpreter.

## Validate the base scaffold

```bash
PYTHONPATH=src python scripts/validate_run_layout.py --runs-root data/runs
PYTHONPATH=src python scripts/audit_preservation.py --runs-root data/runs
```

Expected result for a fresh scaffold is `ok: true` with zero attempts.

## Julia setup for PowerModels

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
julia --project=julia julia/setup_environment.jl
```

If your environment has precompile instability, use conservative runtime settings:

```bash
OPENBLAS_NUM_THREADS=1 JULIA_NUM_THREADS=1 JULIA_PKG_PRECOMPILE_AUTO=0 julia --project=julia --compiled-modules=no julia/run_opf.jl <case_json> <payload_json> <out_json>
```

## ExaGO source

ExaGO source is cloned at:

`external/ExaGO`

Current pinned commit in this workspace:

`545a8deb6fa35552f0ee402ca83672fe1255f61a`

Building ExaGO binaries is a separate step and is site-specific.

For the exact successful Frontier command sequence used in this workspace, see:

- `docs/exago_frontier_build.md`

## Recommended first checks

1. Confirm config files parse and contain expected solver IDs.
2. Run one local attempt with a tiny synthetic case.
3. Finalize and verify attempt integrity.
4. Inspect generated manifest and checksum files.
