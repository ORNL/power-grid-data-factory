#!/bin/bash
#SBATCH -J pgdf_orphan_pv_audit
#SBATCH -A LRN116
#SBATCH -p batch
#SBATCH --qos=normal
#SBATCH -N 1
#SBATCH -n 1
#SBATCH --cpus-per-task=32
#SBATCH -t 04:00:00
#SBATCH -o /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

set -euo pipefail
ulimit -c 0

ROOT=/lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory
cd "$ROOT"

PYTHON=${PYTHON:-$ROOT/.venv-riker/bin/python}
OVERLAY=${OVERLAY:-$ROOT/data/derived/pf_malformed_jsonl_recovery/samples.jsonl}
WORKERS=${WORKERS:-${SLURM_CPUS_PER_TASK:-32}}

unset PYTHONUSERBASE PYTHONNOUSERSITE
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
mkdir -p data/scratch/logs

[[ -x "$PYTHON" ]] || { echo "Python executable not found: $PYTHON" >&2; exit 2; }
command -v jq >/dev/null || { echo "jq is required for the audit" >&2; exit 2; }
[[ -s "$OVERLAY" ]] || { echo "Recovery overlay not found: $OVERLAY" >&2; exit 2; }

echo "[$(date -u +%FT%TZ)] job=$SLURM_JOB_ID workers=$WORKERS overlay=$OVERLAY"
"$PYTHON" scripts/audit_orphan_pv_pf_corpus.py \
  --recovery-overlay "$OVERLAY" \
  --workers "$WORKERS" \
  --progress-every 250
echo "[$(date -u +%FT%TZ)] orphan-PV corpus audit complete"