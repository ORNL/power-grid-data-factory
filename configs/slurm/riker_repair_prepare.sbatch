#!/bin/bash
#SBATCH -J pgdf_repair_prep
#SBATCH -A lrn070
#SBATCH -p batch
#SBATCH --qos=normal
#SBATCH -N 1
#SBATCH -n 1
#SBATCH --cpus-per-task=1
#SBATCH -t 04:00:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
PYBIN=${PYBIN:-$ROOT/.venv-riker/bin/python}
REPAIR_ID=${REPAIR_ID:-riker_activsg2000_case300_r000_r005_parallel_v1}
ROUNDS=${ROUNDS:-0-5}
SOURCE_RUNS_ROOT=${SOURCE_RUNS_ROOT:-data/outputs/runs_3b}
WORK_ROOT=${WORK_ROOT:-data/outputs/repairs/$REPAIR_ID}
COMPUTE_SCRIPT=${COMPUTE_SCRIPT:-$ROOT/configs/slurm/riker_repair_activsg2000_case300.sbatch}
SOURCE_LIST="$ROOT/$WORK_ROOT/source_files.txt"

cd "$ROOT"
mkdir -p data/scratch/logs "$ROOT/$WORK_ROOT"
[[ -x "$PYBIN" ]] || { echo "Missing Python: $PYBIN" >&2; exit 2; }

if [[ ! -s "$SOURCE_LIST" ]]; then
    echo "[$(date -u +%FT%TZ)] preparing repair source list on one node"
    PYTHONPATH="$ROOT/src" "$PYBIN" scripts/repair_failed_campaign_cases.py list-sources \
        --runs-root "$SOURCE_RUNS_ROOT" \
        --rounds "$ROUNDS" \
        --output "$SOURCE_LIST"
fi

COMPUTE_CMD=(sbatch --parsable -A "${ACCOUNT:-lrn070}")
[[ -n "${QOS:-normal}" ]] && COMPUTE_CMD+=(-q "${QOS:-normal}")
COMPUTE_CMD+=(--export=ALL,REPAIR_ID="$REPAIR_ID",ROUNDS="$ROUNDS",SOURCE_RUNS_ROOT="$SOURCE_RUNS_ROOT",WORK_ROOT="$WORK_ROOT" "$COMPUTE_SCRIPT")
compute_job=$("${COMPUTE_CMD[@]}")
echo "[$(date -u +%FT%TZ)] repair_compute_job=${compute_job%%;*} source_files=$(wc -l < "$SOURCE_LIST")"