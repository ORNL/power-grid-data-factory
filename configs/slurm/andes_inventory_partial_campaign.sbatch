#!/bin/bash
#SBATCH -J pgdf_inventory_3b
#SBATCH -A LRN070
#SBATCH -p batch
#SBATCH -N 1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=2
#SBATCH -t 12:00:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
cd "$ROOT"

RUNS_TREE=${RUNS_TREE:-data/outputs/runs_3b/mapreduce_round_000}
OUT=${OUT:-data/reports/ultrascale_3b_partial_inventory.json}
SAMPLE_EVERY=${SAMPLE_EVERY:-10}
TIMING_SAMPLE_SIZE=${TIMING_SAMPLE_SIZE:-4096}
FORCE=${FORCE:-0}

case "$RUNS_TREE" in /*) : ;; *) RUNS_TREE="$ROOT/$RUNS_TREE" ;; esac
case "$OUT" in /*) : ;; *) OUT="$ROOT/$OUT" ;; esac

mkdir -p "$ROOT/data/scratch/logs" "$(dirname "$OUT")"

if [[ ! -d "$RUNS_TREE" ]]; then
  echo "Runs tree not found: $RUNS_TREE" >&2
  exit 2
fi
if [[ -e "$OUT" && "$FORCE" != "1" ]]; then
  echo "Inventory report already exists: $OUT (set FORCE=1 to replace)" >&2
  exit 2
fi
if [[ "$SAMPLE_EVERY" -le 0 || "$TIMING_SAMPLE_SIZE" -le 0 ]]; then
  echo "SAMPLE_EVERY and TIMING_SAMPLE_SIZE must be > 0" >&2
  exit 2
fi

unset PYTHONUSERBASE PYTHONNOUSERSITE
export PYTHONPATH="$ROOT/src"

echo "[$(date -u +%FT%TZ)] job=$SLURM_JOB_ID runs_tree=$RUNS_TREE"
echo "[$(date -u +%FT%TZ)] out=$OUT sample_every=$SAMPLE_EVERY timing_sample_size=$TIMING_SAMPLE_SIZE"

python3.11 scripts/inventory_partial_campaign.py \
  --runs-tree "$RUNS_TREE" \
  --out "$OUT" \
  --sample-every "$SAMPLE_EVERY" \
  --timing-sample-size "$TIMING_SAMPLE_SIZE"

python3.11 -c 'import json,sys; report=json.load(open(sys.argv[1])); assert report["parsed_records"] > 0; print(json.dumps({"ok": True, "report": sys.argv[1], "files_scanned": report["files_scanned"], "parsed_records": report["parsed_records"], "cases": len(report["cases"]), "elapsed_seconds": report["elapsed_seconds"]}, indent=2))' "$OUT"
echo "[$(date -u +%FT%TZ)] inventory complete"