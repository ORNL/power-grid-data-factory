#!/bin/bash
#SBATCH -J pgdf_grid_bench_3b
#SBATCH -A LRN070
#SBATCH -p batch
#SBATCH -N 1
#SBATCH --ntasks-per-node=16
#SBATCH --cpus-per-task=1
#SBATCH -t 08:00:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
PREPARE_SCRIPT=$ROOT/scripts/prepare_grid_benchmark.py
RUN_SCRIPT=$ROOT/scripts/run_campaign_ac_opf_round.py
INVENTORY_SCRIPT=$ROOT/scripts/inventory_partial_campaign.py

module load julia/1.8.2 2>/dev/null || true
export PGDF_JULIA_SYSIMAGE=${PGDF_JULIA_SYSIMAGE:-$ROOT/julia/sysimages/andes/pgdf_sysimage.so}
export JULIA_DEPOT_PATH=${JULIA_DEPOT_PATH:-$ROOT/.julia_depot_andes_profile}
unset PYTHONUSERBASE PYTHONNOUSERSITE

CANDIDATES_JSONL=${CANDIDATES_JSONL:-data/outputs/campaigns/ultrascale_3b/round_summaries/round_000_selected_candidates.jsonl}
PARTIAL_RUNS_TREE=${PARTIAL_RUNS_TREE:-data/outputs/runs_3b/mapreduce_round_000}
SELECTION_DIR=${SELECTION_DIR:-data/outputs/benchmarks/ultrascale_3b_grid_readiness/selection}
BENCHMARK_RUNS_ROOT=${BENCHMARK_RUNS_ROOT:-data/outputs/benchmarks/ultrascale_3b_grid_readiness/runs}
REPORT=${REPORT:-data/reports/ultrascale_3b_grid_benchmark.json}
CONFIG=${CONFIG:-configs/campaign_default.yaml}
EXECUTION_POLICY=${EXECUTION_POLICY:-configs/grid_benchmark_execution_3b.yaml}
PER_CASE=${PER_CASE:-20}
SEED=${SEED:-20260810}
TIMEOUT_S=${TIMEOUT_S:-120}
FORCE_SELECTION=${FORCE_SELECTION:-0}

cd "$ROOT"
mkdir -p data/scratch/logs "$SELECTION_DIR" "$BENCHMARK_RUNS_ROOT" "$(dirname "$REPORT")"

echo "[$(date -u +%FT%TZ)] job=$SLURM_JOB_ID preparing grid benchmark per_case=$PER_CASE seed=$SEED"
echo "[$(date -u +%FT%TZ)] candidates=$CANDIDATES_JSONL partial_runs=$PARTIAL_RUNS_TREE"

MANIFEST="$SELECTION_DIR/benchmark_selection_manifest.json"
QUEUE_FILE="$SELECTION_DIR/benchmark_shards.txt"
if [[ ! -s "$MANIFEST" || ! -s "$QUEUE_FILE" || "$FORCE_SELECTION" == "1" ]]; then
  PREPARE_ARGS=(
    --candidates-jsonl "$CANDIDATES_JSONL"
    --partial-runs-tree "$PARTIAL_RUNS_TREE"
    --out-dir "$SELECTION_DIR"
    --per-case "$PER_CASE"
    --seed "$SEED"
  )
  if [[ "$FORCE_SELECTION" == "1" ]]; then
    PREPARE_ARGS+=(--force)
  fi
  PYTHONPATH="$ROOT/src" python3.11 "$PREPARE_SCRIPT" "${PREPARE_ARGS[@]}"
else
  echo "[$(date -u +%FT%TZ)] reusing benchmark selection $MANIFEST"
fi

SHARD_COUNT=$(wc -l < "$QUEUE_FILE")
if (( SHARD_COUNT == 0 )); then
  echo "Benchmark selection queue is empty: $QUEUE_FILE" >&2
  exit 2
fi

echo "[$(date -u +%FT%TZ)] launching $SHARD_COUNT per-grid benchmark shards across $SLURM_NTASKS tasks"
srun --ntasks="$SLURM_NTASKS" --nodes=1 bash -lc '
set -euo pipefail
ROOT="'$ROOT'"
QUEUE_FILE="'$QUEUE_FILE'"
SHARD_COUNT="'$SHARD_COUNT'"
CONFIG="'$CONFIG'"
EXECUTION_POLICY="'$EXECUTION_POLICY'"
BENCHMARK_RUNS_ROOT="'$BENCHMARK_RUNS_ROOT'"
TIMEOUT_S="'$TIMEOUT_S'"
TASK_ID=${SLURM_PROCID:-0}
TASK_COUNT=${SLURM_NTASKS:-1}

for ((index=TASK_ID; index<SHARD_COUNT; index+=TASK_COUNT)); do
  shard=$(sed -n "$((index + 1))p" "$QUEUE_FILE")
  [[ -n "$shard" && -s "$shard" ]] || continue
  case_id=$(basename "$shard" .jsonl)
  case_id=${case_id#case_???_}
  echo "[$(date -u +%FT%TZ)] task=$TASK_ID case=$case_id shard=$shard"
  PYTHONPATH="$ROOT/src" python3.11 "$ROOT/scripts/run_campaign_ac_opf_round.py" \
    --campaign-id "grid_benchmark_3b__${case_id}" \
    --round-index 0 \
    --selected-candidates-jsonl "$shard" \
    --config "$CONFIG" \
    --runs-root "$BENCHMARK_RUNS_ROOT/$case_id" \
    --solver-id powermodels_ac_opf_ipopt_grid_benchmark \
    --timeout-s "$TIMEOUT_S" \
    --execution-policy "$EXECUTION_POLICY" \
    --continue-on-error \
    --max-failure-fraction 1.0 \
    --resume
done
'

PYTHONPATH="$ROOT/src" python3.11 "$INVENTORY_SCRIPT" \
  --runs-tree "$BENCHMARK_RUNS_ROOT" \
  --out "$REPORT" \
  --sample-every 1 \
  --timing-sample-size 4096

python3.11 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["parsed_records"] > 0; print(json.dumps({"ok": True, "report": sys.argv[1], "records": d["parsed_records"], "cases": len(d["cases"])}, indent=2))' "$REPORT"
echo "[$(date -u +%FT%TZ)] grid benchmark complete"