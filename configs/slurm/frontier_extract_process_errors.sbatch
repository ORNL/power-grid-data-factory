#!/bin/bash
#SBATCH -J pgdf_extract_pe
#SBATCH -A LRN087
#SBATCH -p batch
#SBATCH -N 1
#SBATCH -t 01:00:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

# =============================================================================
# Extract process_error candidate inputs from completed campaign rounds so they
# can be re-submitted as a repair round with the fixed .m files.
#
# Phase 1 (this job): scan all samples.jsonl, write extracted candidates JSONL.
# Phase 2 (manual submit after this job completes): re-run with existing round
# job template, using SELECTED_JSONL_OVERRIDE to bypass bootstrap.
#
# Submit:
#   sbatch configs/slurm/frontier_extract_process_errors.sbatch
#
# Then after this job finishes, submit the repair round:
#   sbatch \
#     --export=ALL,\
# SELECTED_JSONL_OVERRIDE=data/scratch/repair/process_error_candidates.jsonl,\
# RUN_BOOTSTRAP=0,\
# CAMPAIGN_ID=exago_frontier_large_grids_par_repair,\
# RUNS_ROOT=data/outputs/runs/exago_frontier_large_grids_par_repair/r000,\
# ROUND_INDEX=0 \
#     configs/slurm/frontier_exago_acopf_mapreduce_8n_2h.sbatch
# =============================================================================

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
PYBIN=${PYBIN:-python3.11}
PGDF_VENV=${PGDF_VENV-$ROOT/.venv}

if [[ -n "${PGDF_VENV:-}" && -f "${PGDF_VENV}/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${PGDF_VENV}/bin/activate"
fi

GROUPS=${GROUPS:-all}
RUNS_ROOT=${RUNS_ROOT:-data/outputs/runs/exago_frontier_large_grids_par}
OUT=${OUT:-data/scratch/repair/process_error_candidates.jsonl}
WORKERS=${WORKERS:-128}

mkdir -p "$(dirname "$OUT")"
mkdir -p "$ROOT/data/scratch/logs"

echo "[$(date -u +%FT%TZ)] job=$SLURM_JOB_ID scanning for process_error candidates"
echo "[$(date -u +%FT%TZ)] groups=$GROUPS runs_root=$RUNS_ROOT out=$OUT workers=$WORKERS"

PYTHONPATH="$ROOT/src" "$PYBIN" "$ROOT/scripts/extract_process_error_candidates.py" \
  --runs-root "$RUNS_ROOT" \
  --groups "$GROUPS" \
  --out "$OUT" \
  --workers "$WORKERS"

COUNT=$(wc -l < "$OUT" 2>/dev/null || echo 0)
echo "[$(date -u +%FT%TZ)] Done. Extracted $COUNT unique process_error candidates -> $OUT"
echo ""
echo "=== Next step: submit the repair round ==="
echo "sbatch \\"
echo "  --export=ALL,\\"
echo "SELECTED_JSONL_OVERRIDE=$OUT,\\"
echo "RUN_BOOTSTRAP=0,\\"
echo "ENFORCE_COVERAGE=0,\\"
echo "BACKFILL_FROM_POOL=0,\\"
echo "CAMPAIGN_ID=exago_frontier_large_grids_par_repair,\\"
echo "RUNS_ROOT=data/outputs/runs/exago_frontier_large_grids_par_repair/r000,\\"
echo "ROUND_INDEX=0 \\"
echo "  $ROOT/configs/slurm/frontier_exago_acopf_mapreduce_8n_2h.sbatch"
