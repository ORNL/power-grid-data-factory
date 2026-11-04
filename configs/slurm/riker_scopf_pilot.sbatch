#!/bin/bash
#SBATCH -J pgdf_scopf_pilot
#SBATCH -A LRN116
#SBATCH -p batch
#SBATCH --qos=normal
#SBATCH -N 1
#SBATCH -c 8
#SBATCH -t 03:00:00
#SBATCH -o /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

# SCOPF smoke/timing pilot: pglib case300_ieee with 5/20/50 N-1 contingencies (PMSC run_c1_scopf + Ipopt).
set -euo pipefail
ulimit -c 0

ROOT=/lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory
cd "$ROOT"
PYBIN=${PYBIN:-$ROOT/.venv-riker/bin/python}
CASE_ID=${CASE_ID:-pglib_opf_case300_ieee}
CASE_FILE=${CASE_FILE:-external/pglib-opf/pglib_opf_case300_ieee.m}
SETS=${SETS:-"case300_n1_b5_g0 case300_n1_b15_g5 case300_n1_b40_g10"}
TIMEOUT_S=${TIMEOUT_S:-3000}

export PGDF_JULIA_BIN=${PGDF_JULIA_BIN:-$ROOT/.software/bin/julia}
export PGDF_JULIA_PROJECT_DIR=${PGDF_JULIA_PROJECT_DIR:-$ROOT/julia/lockfiles/riker}
export JULIA_DEPOT_PATH=${JULIA_DEPOT_PATH:-$ROOT/.julia_depot_riker}
export OPENBLAS_NUM_THREADS=1 JULIA_NUM_THREADS=1
source "${COINHSL_ENV:-$HOME/.local/coinhsl/riker/env.sh}"
export IPOPT_LINEAR_SOLVER=${IPOPT_LINEAR_SOLVER:-ma27}
export PYTHONPATH="$ROOT/src"

mkdir -p data/reports/scopf_pilot
for set_name in $SETS; do
  echo "=== $set_name start $(date -Is) linear_solver=$IPOPT_LINEAR_SOLVER"
  t0=$(date +%s)
  rc=0
  "$PYBIN" scripts/run_scopf.py --source pglib --case-file "$CASE_FILE" \
    --contingency-set "data/inputs/scopf_pilot/${set_name}.json" \
    --runs-root data/outputs/runs/scopf_pilot \
    --timeout-s "$TIMEOUT_S" ${SCREEN:+--screen-contingencies} \
    --out "data/reports/scopf_pilot/${set_name}_${SLURM_JOB_ID:-local}.json" || rc=$?
  echo "=== $set_name rc=$rc wall_s=$(( $(date +%s) - t0 ))"
done
