#!/bin/bash
#SBATCH -J pgdf_scopf_campaign
#SBATCH -A LRN116
#SBATCH -p batch
#SBATCH --qos=normal
#SBATCH -N 1
#SBATCH -c 64
#SBATCH -t 2-00:00:00
#SBATCH -o /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

# Resumable SCOPF campaign: WORKERS single-threaded run_scopf.py processes pull
# tasks from the campaign manifest. The two-day window is the cluster's maximum
# job limit; xlarge tasks have a 72-hour solver timeout and may require a later
# retry if they exceed the allocation. Resubmit the same script to continue.
set -euo pipefail
ulimit -c 0

ROOT=/lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory
cd "$ROOT"
PYBIN=${PYBIN:-$ROOT/.venv-riker/bin/python}
CONFIG=${CONFIG:-configs/scopf_campaign_riker.yaml}
WORKERS=${WORKERS:-${SLURM_CPUS_PER_TASK:-8}}
# Space-separated tier names to claim from (empty = all tiers). The cluster caps
# allocations at two days, shorter than the xlarge solver timeout; use tier filters
# to isolate long work, e.g. sbatch -t 2-00:00:00 -c 16 --export=ALL,TIERS="large xlarge",CLEAR_STALE_CLAIMS=0 ...
TIER_ARGS=()
for t in ${TIERS:-}; do TIER_ARGS+=(--tier "$t"); done

export PGDF_JULIA_BIN=${PGDF_JULIA_BIN:-$ROOT/.software/bin/julia}
export PGDF_JULIA_PROJECT_DIR=${PGDF_JULIA_PROJECT_DIR:-$ROOT/julia/lockfiles/riker}
export JULIA_DEPOT_PATH=${JULIA_DEPOT_PATH:-$ROOT/.julia_depot_riker}
export OPENBLAS_NUM_THREADS=1 JULIA_NUM_THREADS=1 OMP_NUM_THREADS=1
source "${COINHSL_ENV:-$HOME/.local/coinhsl/riker/env.sh}"
export PYTHONPATH="$ROOT/src"

"$PYBIN" scripts/scopf_campaign.py prepare --config "$CONFIG"
# Claims left by a killed job would block those tasks; this job is the only writer.
if [[ "${CLEAR_STALE_CLAIMS:-1}" == "1" ]]; then
  "$PYBIN" scripts/scopf_campaign.py clear-stale-claims --config "$CONFIG"
fi

echo "=== $(date -Is) starting $WORKERS workers"
for w in $(seq 1 "$WORKERS"); do
  "$PYBIN" scripts/scopf_campaign.py run --config "$CONFIG" --python "$PYBIN" "${TIER_ARGS[@]}" > "data/scratch/logs/scopf_campaign_${SLURM_JOB_ID:-local}_w${w}.json" 2>&1 &
  sleep 2
done
wait
"$PYBIN" scripts/scopf_campaign.py status --config "$CONFIG"
