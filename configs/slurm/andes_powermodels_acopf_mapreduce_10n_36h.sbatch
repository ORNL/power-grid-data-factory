#!/bin/bash
#SBATCH -J pgdf_campaign_mr
#SBATCH -A LRN070
#SBATCH -p batch
#SBATCH -N 10
#SBATCH --ntasks-per-node=16
#SBATCH --cpus-per-task=1
#SBATCH -t 36:00:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
BOOTSTRAP_SCRIPT=$ROOT/scripts/bootstrap_adaptive_campaign.py
SHARD_SCRIPT=$ROOT/scripts/shard_selected_candidates.py
MAP_SCRIPT=$ROOT/scripts/run_campaign_ac_opf_round.py
REDUCE_SCRIPT=$ROOT/scripts/reduce_campaign_shards.py

module load julia/1.8.2 2>/dev/null || true

# Prefer a prebuilt PackageCompiler sysimage so solves skip first-run compilation.
# Explicit path avoids relying on per-node hostname detection; a missing file
# cleanly falls back to the normal compile path in the solver adapter.
PGDF_JULIA_SYSIMAGE=${PGDF_JULIA_SYSIMAGE:-$ROOT/julia/sysimages/andes/pgdf_sysimage.so}
export PGDF_JULIA_SYSIMAGE

CAMPAIGN_ID=${CAMPAIGN_ID:-pilot_pglib_14_57_118}
ROUND_INDEX=${ROUND_INDEX:-0}
CONFIG=${CONFIG:-configs/campaign_default.yaml}
CASES=${CASES:-"pglib_opf_case14_ieee pglib_opf_case57_ieee pglib_opf_case118_ieee"}
PER_CASE=${PER_CASE:-500}
SAMPLER=${SAMPLER:-latin_hypercube}
BUDGET=${BUDGET:-600}
SEED=${SEED:-7}
SOLVER_ID=${SOLVER_ID:-powermodels_ac_opf_ipopt_campaign}
TIMEOUT_S=${TIMEOUT_S:-1200}
RUNS_ROOT=${RUNS_ROOT:-data/outputs/runs}
MAX_CANDIDATES=${MAX_CANDIDATES:-0}
RUN_BOOTSTRAP=${RUN_BOOTSTRAP:-1}
CONTINUE_ON_ERROR=${CONTINUE_ON_ERROR:-1}
SHARD_COUNT=${SHARD_COUNT:-${SLURM_NTASKS:-1}}
COVERAGE_KEYS=${COVERAGE_KEYS:-dataset,topology_id,operating_regime,contingency_class}
MIN_PER_BUCKET=${MIN_PER_BUCKET:-1}
ENFORCE_COVERAGE=${ENFORCE_COVERAGE:-1}
BACKFILL_FROM_POOL=${BACKFILL_FROM_POOL:-1}
MAX_BACKFILL_ADDITIONS=${MAX_BACKFILL_ADDITIONS:-0}
SCORE_KEY=${SCORE_KEY:-novelty_score}
MAX_FAILURE_FRACTION=${MAX_FAILURE_FRACTION:-0.5}
TOPOLOGIES_PER_CASE=${TOPOLOGIES_PER_CASE:-6}
MAX_SWITCHED_BRANCHES=${MAX_SWITCHED_BRANCHES:-3}
MAX_K=${MAX_K:-10}
SEQ_CASCADE_PER_OP=${SEQ_CASCADE_PER_OP:-0}
SEQ_MAX_LEN=${SEQ_MAX_LEN:-10}
RESUME=${RESUME:-0}
BOOTSTRAP_WORKERS=${BOOTSTRAP_WORKERS:-16}

printf -v ROUND_PAD "%03d" "$ROUND_INDEX"
CAMPAIGN_ROOT="$ROOT/data/outputs/campaigns/$CAMPAIGN_ID"
SELECTED_JSONL="$CAMPAIGN_ROOT/round_summaries/round_${ROUND_PAD}_selected_candidates.jsonl"
SHARD_DIR="$CAMPAIGN_ROOT/round_summaries/round_${ROUND_PAD}_shards"
SHARD_PREFIX="selected"
SHARD_IDS_FILE="$SHARD_DIR/shard_campaign_ids.txt"
POOL_JSONL_DEFAULT="$CAMPAIGN_ROOT/round${ROUND_INDEX}_screened_candidates.jsonl"
POOL_JSONL=${POOL_JSONL:-$POOL_JSONL_DEFAULT}
QUEUE_DIR="$SHARD_DIR/queue"
QUEUE_FILE="$QUEUE_DIR/shards.txt"
INDEX_FILE="$QUEUE_DIR/next_index.txt"
LOCK_DIR="$QUEUE_DIR/lock"
DONE_DIR="$QUEUE_DIR/done"

mkdir -p "$ROOT/data/scratch/logs"

echo "[$(date -u +%FT%TZ)] JOB=$SLURM_JOB_ID mode=map_reduce campaign_id=$CAMPAIGN_ID round_index=$ROUND_INDEX"
echo "[$(date -u +%FT%TZ)] config=$CONFIG budget=$BUDGET per_case=$PER_CASE seed=$SEED"
echo "[$(date -u +%FT%TZ)] solver_id=$SOLVER_ID timeout_s=$TIMEOUT_S shard_count=$SHARD_COUNT"
echo "[$(date -u +%FT%TZ)] coverage_keys=$COVERAGE_KEYS min_per_bucket=$MIN_PER_BUCKET enforce=$ENFORCE_COVERAGE backfill=$BACKFILL_FROM_POOL"

RESUMING=0
if [[ "$RESUME" == "1" ]] && compgen -G "$SHARD_DIR/${SHARD_PREFIX}_*.jsonl" > /dev/null; then
  RESUMING=1
  echo "[$(date -u +%FT%TZ)] RESUME enabled: reusing existing shards under $SHARD_DIR; completed shards and finalized runs are skipped"
fi

if [[ "$RESUMING" == "0" && "$RUN_BOOTSTRAP" == "1" ]]; then
  read -r -a CASE_ARRAY <<< "$CASES"
  echo "[$(date -u +%FT%TZ)] Bootstrapping campaign candidates and round selection"
  PYTHONPATH="$ROOT/src" python3.11 "$BOOTSTRAP_SCRIPT" \
    --campaign-id "$CAMPAIGN_ID" \
    --config "$CONFIG" \
    --cases "${CASE_ARRAY[@]}" \
    --per-case "$PER_CASE" \
    --sampler "$SAMPLER" \
    --topologies-per-case "$TOPOLOGIES_PER_CASE" \
    --max-switched-branches "$MAX_SWITCHED_BRANCHES" \
    --max-k "$MAX_K" \
    --sequential-cascade-per-operating-point "$SEQ_CASCADE_PER_OP" \
    --sequential-max-len "$SEQ_MAX_LEN" \
    --budget "$BUDGET" \
    --round-index "$ROUND_INDEX" \
    --seed "$SEED" \
    --workers "$BOOTSTRAP_WORKERS"
else
  echo "[$(date -u +%FT%TZ)] Skipping bootstrap (RESUMING=$RESUMING RUN_BOOTSTRAP=$RUN_BOOTSTRAP)"
fi

if [[ ! -s "$SELECTED_JSONL" ]]; then
  echo "Selected candidate file not found or empty: $SELECTED_JSONL" >&2
  echo "Set RUN_BOOTSTRAP=1 or provide a precomputed selected-candidates file for this campaign round." >&2
  exit 2
fi

SHARD_ARGS=(
  --input "$SELECTED_JSONL"
  --num-shards "$SHARD_COUNT"
  --out-dir "$SHARD_DIR"
  --prefix "$SHARD_PREFIX"
  --coverage-keys "$COVERAGE_KEYS"
  --min-per-bucket "$MIN_PER_BUCKET"
  --max-backfill-additions "$MAX_BACKFILL_ADDITIONS"
  --score-key "$SCORE_KEY"
)
if [[ "$ENFORCE_COVERAGE" == "1" ]]; then
  SHARD_ARGS+=(--enforce-coverage)
fi
if [[ "$BACKFILL_FROM_POOL" == "1" ]]; then
  SHARD_ARGS+=(--backfill-from-pool)
fi
if [[ -s "$POOL_JSONL" ]]; then
  SHARD_ARGS+=(--pool-jsonl "$POOL_JSONL")
fi

if [[ "$RESUMING" == "0" ]]; then
  PYTHONPATH="$ROOT/src" python3.11 "$SHARD_SCRIPT" "${SHARD_ARGS[@]}"
else
  echo "[$(date -u +%FT%TZ)] Skipping re-sharding (resume): reusing existing shard files"
fi

: > "$SHARD_IDS_FILE"
mkdir -p "$QUEUE_DIR" "$DONE_DIR"
: > "$QUEUE_FILE"
for ((i=0; i<SHARD_COUNT; i++)); do
  shard_file="$SHARD_DIR/${SHARD_PREFIX}_$(printf "%05d" "$i").jsonl"
  if [[ -s "$shard_file" ]]; then
    printf "%s__r%s__s%05d\n" "$CAMPAIGN_ID" "$ROUND_PAD" "$i" >> "$SHARD_IDS_FILE"
    printf "%05d\n" "$i" >> "$QUEUE_FILE"
  fi
done

echo 0 > "$INDEX_FILE"

if [[ ! -s "$SHARD_IDS_FILE" ]]; then
  echo "No non-empty shard files were generated." >&2
  exit 2
fi

echo "[$(date -u +%FT%TZ)] Starting map stage with srun tasks=${SLURM_NTASKS}"

srun --ntasks="${SLURM_NTASKS}" --nodes="${SLURM_JOB_NUM_NODES}" bash -lc '
set -euo pipefail
ROOT="'$ROOT'"
CAMPAIGN_ID="'$CAMPAIGN_ID'"
ROUND_INDEX="'$ROUND_INDEX'"
ROUND_PAD="'$ROUND_PAD'"
CONFIG="'$CONFIG'"
SHARD_DIR="'$SHARD_DIR'"
SHARD_PREFIX="'$SHARD_PREFIX'"
MAP_SCRIPT="'$MAP_SCRIPT'"
SOLVER_ID="'$SOLVER_ID'"
TIMEOUT_S="'$TIMEOUT_S'"
RUNS_ROOT="'$RUNS_ROOT'"
MAX_CANDIDATES="'$MAX_CANDIDATES'"
CONTINUE_ON_ERROR="'$CONTINUE_ON_ERROR'"
MAX_FAILURE_FRACTION="'$MAX_FAILURE_FRACTION'"
QUEUE_FILE="'$QUEUE_FILE'"
INDEX_FILE="'$INDEX_FILE'"
LOCK_DIR="'$LOCK_DIR'"
DONE_DIR="'$DONE_DIR'"
RESUMING="'$RESUMING'"
PGDF_JULIA_SYSIMAGE="'$PGDF_JULIA_SYSIMAGE'"
export PGDF_JULIA_SYSIMAGE
TOTAL_SHARDS=$(wc -l < "$QUEUE_FILE")

TASK_ID=${SLURM_PROCID:-0}

echo "[$(date -u +%FT%TZ)] task=$TASK_ID dynamic shard pickup enabled total_shards=$TOTAL_SHARDS"
while true; do
  while ! mkdir "$LOCK_DIR" 2>/dev/null; do
    sleep 0.05
  done

  next_idx=$(cat "$INDEX_FILE")
  if (( next_idx >= TOTAL_SHARDS )); then
    rmdir "$LOCK_DIR"
    break
  fi

  echo $((next_idx + 1)) > "$INDEX_FILE"
  rmdir "$LOCK_DIR"

  shard_number=$(sed -n "$((next_idx + 1))p" "$QUEUE_FILE")
  if [[ -z "${shard_number:-}" ]]; then
    continue
  fi

  SHARD_FILE="$SHARD_DIR/${SHARD_PREFIX}_${shard_number}.jsonl"
  if [[ ! -s "$SHARD_FILE" ]]; then
    continue
  fi

  if [[ "$RESUMING" == "1" && -f "$DONE_DIR/$shard_number" ]]; then
    echo "[$(date -u +%FT%TZ)] task=$TASK_ID shard_number=$shard_number already complete (resume) skipping"
    continue
  fi

  SHARD_CAMPAIGN_ID="${CAMPAIGN_ID}__r${ROUND_PAD}__s${shard_number}"
  SHARD_RUNS_ROOT="${RUNS_ROOT}/mapreduce_round_${ROUND_PAD}/shard_${shard_number}"

  ARGS=(
    --campaign-id "$SHARD_CAMPAIGN_ID"
    --round-index "$ROUND_INDEX"
    --selected-candidates-jsonl "$SHARD_FILE"
    --config "$CONFIG"
    --runs-root "$SHARD_RUNS_ROOT"
    --solver-id "$SOLVER_ID"
    --timeout-s "$TIMEOUT_S"
    --max-failure-fraction "$MAX_FAILURE_FRACTION"
  )

  if [[ "$MAX_CANDIDATES" != "0" ]]; then
    ARGS+=(--max-candidates "$MAX_CANDIDATES")
  fi
  if [[ "$CONTINUE_ON_ERROR" == "1" ]]; then
    ARGS+=(--continue-on-error)
  fi
  if [[ "$RESUMING" == "1" ]]; then
    ARGS+=(--resume)
  fi

  echo "[$(date -u +%FT%TZ)] task=$TASK_ID shard_number=$shard_number shard_campaign_id=$SHARD_CAMPAIGN_ID"
  PYTHONPATH="$ROOT/src" python3.11 "$MAP_SCRIPT" "${ARGS[@]}"
  touch "$DONE_DIR/$shard_number"
done
'

echo "[$(date -u +%FT%TZ)] Map stage complete. Starting deterministic reduce stage"

PYTHONPATH="$ROOT/src" python3.11 "$REDUCE_SCRIPT" \
  --campaign-id "$CAMPAIGN_ID" \
  --round-index "$ROUND_INDEX" \
  --config "$CONFIG" \
  --shard-campaign-ids-file "$SHARD_IDS_FILE"

echo "[$(date -u +%FT%TZ)] Completed map/reduce campaign round job $SLURM_JOB_ID"
