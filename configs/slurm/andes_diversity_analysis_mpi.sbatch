#!/bin/bash
#SBATCH -J pgdf_diversity
#SBATCH -A LRN070
#SBATCH -p batch
#SBATCH -N 4
#SBATCH --ntasks-per-node=8
#SBATCH --cpus-per-task=1
#SBATCH -t 04:00:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
cd "$ROOT"

module load python/.3.11-anaconda3 2>/dev/null || true
module load openmpi 2>/dev/null || true

CAMPAIGN_ID=${CAMPAIGN_ID:-ultrascale_3b}
ROUNDS=${ROUNDS:-0-5}
CONFIG=${CONFIG:-configs/diversity_analysis.yaml}
SAMPLE_SIZE=${SAMPLE_SIZE:-10000}
BLOCK_SIZE=${BLOCK_SIZE:-256}
NEAR_DUPLICATE_THRESHOLD=${NEAR_DUPLICATE_THRESHOLD:-0.02}
OUTPUT_DIR=${OUTPUT_DIR:-data/reports/diversity/$CAMPAIGN_ID/rounds_mpi}
PYTHON=${PYTHON:-$ROOT/.venv/bin/python}

unset PYTHONUSERBASE PYTHONNOUSERSITE
export PYTHONPATH="$ROOT/src"
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
mkdir -p data/scratch/logs "$OUTPUT_DIR"

if [[ ! -x "$PYTHON" ]]; then
  echo "Python executable not found: $PYTHON" >&2
  exit 2
fi
if ! "$PYTHON" -c 'import mpi4py' 2>/dev/null; then
  echo "mpi4py is unavailable in $PYTHON; install the analysis-mpi project extra." >&2
  exit 2
fi

echo "[$(date -u +%FT%TZ)] job=$SLURM_JOB_ID campaign=$CAMPAIGN_ID rounds=$ROUNDS ranks=$SLURM_NTASKS"
echo "[$(date -u +%FT%TZ)] sample_size=$SAMPLE_SIZE block_size=$BLOCK_SIZE output=$OUTPUT_DIR"

srun --ntasks="$SLURM_NTASKS" --cpus-per-task="$SLURM_CPUS_PER_TASK" \
  "$PYTHON" scripts/analyze_campaign_diversity_mpi.py \
    --campaign-id "$CAMPAIGN_ID" \
    --rounds "$ROUNDS" \
    --config "$CONFIG" \
    --sample-size "$SAMPLE_SIZE" \
    --block-size "$BLOCK_SIZE" \
    --near-duplicate-threshold "$NEAR_DUPLICATE_THRESHOLD" \
    --output-dir "$OUTPUT_DIR"

echo "[$(date -u +%FT%TZ)] diversity analysis complete"