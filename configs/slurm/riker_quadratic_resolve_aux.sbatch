#!/bin/bash
#SBATCH -J pgdf_quad_resolve_aux
#SBATCH -A LRN116
#SBATCH -p batch
#SBATCH --qos=normal
#SBATCH -N 8
#SBATCH --ntasks-per-node=32
#SBATCH --cpus-per-task=1
#SBATCH -t 36:00:00
#SBATCH -o /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err
# Quadratic re-solve for the PowerModels AC-OPF roots outside runs_3b.
# Resumable; in-place atomic merge like riker_quadratic_resolve.sbatch.

set -euo pipefail
ulimit -c 0

ROOT=/lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory
PYBIN=${PYBIN:-$ROOT/.venv-riker/bin/python}
export REPAIR_ID=${REPAIR_ID:-riker_quadratic_resolve_v2_aux}
export ROUNDS=0
export ALL_CASES=1
export SELECT=legacy-quadratic
export CAMPAIGN_PREFIX=${CAMPAIGN_PREFIX:-quadratic_resolve_v2_aux}
export RUN_MERGE=${RUN_MERGE:-1}
export APPLY_MERGE=${APPLY_MERGE:-1}
export MERGE_NO_BACKUP=${MERGE_NO_BACKUP:-1}
export WORK_ROOT=data/outputs/repairs/$REPAIR_ID

cd "$ROOT"
unset PYTHONUSERBASE PYTHONNOUSERSITE
BACKFILL=scripts/backfill_cost_scaling.py
SOURCE_LIST=$ROOT/$WORK_ROOT/source_files.txt
if [[ ! -s "$SOURCE_LIST" ]]; then
    mkdir -p "$ROOT/$WORK_ROOT"
    tmp="$SOURCE_LIST.in_progress"
    rm -f "$tmp"
    "$PYBIN" "$BACKFILL" list-sources --runs-root data/outputs/runs_3b_activsg_hsl_full --rounds 0 \
        --shard-count 1024 --output "$tmp" --append
    "$PYBIN" "$BACKFILL" list-sources --runs-root data/outputs/runs_3b_activsg_hsl_almost_restart --rounds 0 \
        --shard-count 331 --campaign-prefix activsg_hsl_almost_restart --output "$tmp" --append
    "$PYBIN" "$BACKFILL" list-sources --runs-root data/outputs/benchmarks/activsg_hsl_ma27_ma57/runs --rounds 0 \
        --shard-count 40 --campaign-prefix activsg_hsl_ma27_ma57 --output "$tmp" --append
    for bench in ultrascale_3b_grid_readiness ultrascale_3b_long_timeout; do
        for item in $(ls "data/outputs/benchmarks/$bench/runs"); do
            printf '%s\t\n' "$ROOT/data/outputs/benchmarks/$bench/runs/$item/ac_opf/samples.jsonl" >> "$tmp"
        done
    done
    mv "$tmp" "$SOURCE_LIST"
fi

exec bash "$ROOT/configs/slurm/riker_repair_activsg2000_case300.sbatch"
