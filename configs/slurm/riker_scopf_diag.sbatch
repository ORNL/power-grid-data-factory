#!/bin/bash
#SBATCH -J pgdf_scopf_diag
#SBATCH -A MAT151
#SBATCH -p batch
#SBATCH --qos=normal
#SBATCH -N 1
#SBATCH -c 8
#SBATCH -t 01:00:00
#SBATCH -o /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

# SCOPF infeasibility diagnostic: base AC-OPF + per-contingency constraint-group ablation.
set -euo pipefail
ulimit -c 0
ROOT=/lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory
cd "$ROOT"
SET=${SET:-n1_b5_g0_s0}
ATT=data/outputs/runs/scopf_pilot/scopf/pglib_opf_case300_ieee/topology_000000_baseline/op_000000_baseline/$SET/powermodels_scopf_pmsc_ipopt/attempts/attempt_000001/inputs
export JULIA_DEPOT_PATH=$ROOT/.julia_depot_riker
export OPENBLAS_NUM_THREADS=1 JULIA_NUM_THREADS=1
source "$HOME/.local/coinhsl/riker/env.sh"
mkdir -p data/reports/scopf_pilot
"$ROOT/.software/bin/julia" --project="$ROOT/julia/lockfiles/riker" julia/diag_scopf.jl \
  "$ATT/resolved_case.json" "$ATT/resolved_contingency_set.json" \
  "data/reports/scopf_pilot/diag_${SET}_${SLURM_JOB_ID:-local}.json"
