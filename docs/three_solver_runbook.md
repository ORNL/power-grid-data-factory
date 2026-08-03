# Three-Solver Runbook (ExaGO + pandapower + PowerModels)

This runbook captures the working sequence used in this workspace to run and compare all three solver paths.

## What this runbook verifies

1. ExaGO OPFLOW runs with `IPOPT`, `HIOPSPARSE`, and `HIOP`.
2. pandapower runs AC-OPF on the same MATPOWER cases.
3. PowerModels adapter executes AC-OPF and returns solver output.
4. A single JSON report summarizes status and objective deltas.

## Prerequisites

1. ExaGO has been built and installed under a machine-scoped prefix, for example `external/ExaGO/builds/frontier/install`.
2. Python environment includes `pandapower`.
3. Julia environment for `julia/` has been instantiated.

Reference setup docs:

1. `docs/exago_frontier_build.md`
2. `docs/setup.md`

Optional helper to prepare machine-scoped ExaGO build/install directories:

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
PYTHONPATH=src python3.11 scripts/configure_exago_build.py \
  --exago-root external/ExaGO \
  --profile frontier \
  --cache buildsystem/clang-hip/cache.cmake
```

## One-time package step for pandapower MATPOWER parsing

pandapower requires `matpowercaseframes` for `.m` case ingestion.

```bash
PY=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/HydraGNN/HydraGNN-Installation-Frontier-ROCm713/hydragnn_venv_rocm713/bin/python
$PY -m pip install matpowercaseframes
```

## One-time Julia setup for PowerModels

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
module load julia
julia --project=julia julia/setup_environment.jl
```

## Run all three solver paths and generate comparison report

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/external/ExaGO
PY=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/HydraGNN/HydraGNN-Installation-Frontier-ROCm713/hydragnn_venv_rocm713/bin/python
SRCDIR=$PWD source buildsystem/clang-hip/frontierVariables.sh

cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
PYTHONPATH=src $PY scripts/compare_solver_consistency.py \
  --exago-root external/ExaGO \
  --build-profile frontier \
  --out data/analysis/solver_consistency_report.json
```

## Read results

```bash
sed -n '1,280p' data/analysis/solver_consistency_report.json
```

Expected success indicators:

1. `cases.*.exago.IPOPT.convergence == "CONVERGED"`
2. `cases.*.exago.HIOPSPARSE.convergence == "CONVERGED"`
3. `cases.*.exago.HIOP.convergence == "CONVERGED"`
4. `cases.*.pandapower.convergence == "CONVERGED"`
5. `powermodels_status.success == true`
6. `powermodels_status.termination_status` is typically `LOCALLY_SOLVED` or `OPTIMAL`

## Phase 1 gate command

Use the automated gate script to enforce calibration tolerances and preservation checks:

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/external/ExaGO
PY=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/HydraGNN/HydraGNN-Installation-Frontier-ROCm713/hydragnn_venv_rocm713/bin/python
SRCDIR=$PWD source buildsystem/clang-hip/frontierVariables.sh

cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
PYTHONPATH=src $PY scripts/phase1_gate.py --config configs/phase1_calibration.yaml --exago-root external/ExaGO --build-profile frontier --runs-root data/runs
```

If the command exits with code `0`, Phase 1 gate checks passed. Non-zero exit indicates at least one required check failed.

## Notes on interpretation

1. Objective values from pandapower and ExaGO are usually close but not identical.
2. Small deltas are expected due to formulation and conversion differences.
3. If PowerModels shows `preflight_timeout`, rerun once; the adapter is configured to continue solve attempts after preflight timeouts.
