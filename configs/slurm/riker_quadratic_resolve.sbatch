#!/bin/bash
#SBATCH -J pgdf_quad_resolve
#SBATCH -A LRN116
#SBATCH -p batch
#SBATCH --qos=normal
#SBATCH -N 16
#SBATCH --ntasks-per-node=32
#SBATCH --cpus-per-task=1
#SBATCH -t 36:00:00
#SBATCH -o /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err
# Re-solve every pre-fix quadratic-cost PowerModels AC-OPF record of runs_3b
# with the per-unit cost code. Resumable: resubmit until all shards are done; the
# submission that finishes the last shard atomically replaces the legacy rows in
# place (no backups). Submit after the linear backfill so the two never overlap.

set -euo pipefail
ulimit -c 0

ROOT=/lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory
PYBIN=${PYBIN:-$ROOT/.venv-riker/bin/python}
export REPAIR_ID=${REPAIR_ID:-riker_quadratic_resolve_v2_r000_r006}
export ROUNDS=${ROUNDS:-0-6}
export MAX_ROUND=6
export ALL_CASES=1
export SELECT=legacy-quadratic
export CAMPAIGN_PREFIX=${CAMPAIGN_PREFIX:-quadratic_resolve_v2}
export RUN_MERGE=${RUN_MERGE:-1}
export APPLY_MERGE=${APPLY_MERGE:-1}
export MERGE_NO_BACKUP=${MERGE_NO_BACKUP:-1}
export WORK_ROOT=data/outputs/repairs/$REPAIR_ID

cd "$ROOT"
unset PYTHONUSERBASE PYTHONNOUSERSITE
SOURCE_LIST=$ROOT/$WORK_ROOT/source_files.txt
if [[ ! -s "$SOURCE_LIST" ]]; then
    mkdir -p "$ROOT/$WORK_ROOT"
    PYTHONPATH="$ROOT/src" "$PYBIN" scripts/repair_failed_campaign_cases.py list-sources \
        --runs-root data/outputs/runs_3b --rounds "$ROUNDS" --output "$SOURCE_LIST"
fi

exec bash "$ROOT/configs/slurm/riker_repair_activsg2000_case300.sbatch"
