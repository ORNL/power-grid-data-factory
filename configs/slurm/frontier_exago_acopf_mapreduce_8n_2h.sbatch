#!/bin/bash
#SBATCH -J pgdf_exago_mr
#SBATCH -A LRN070
#SBATCH -p batch
#SBATCH -N 8
#SBATCH --ntasks-per-node=8
#SBATCH --cpus-per-task=7
#SBATCH --gpus-per-node=8
#SBATCH --gpu-bind=closest
#SBATCH -t 02:00:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

# =============================================================================
# Frontier ExaGO GPU AC-OPF map/reduce campaign round.
#
# Mirrors the Andes PowerModels campaign (bootstrap -> shard -> map -> reduce)
# but solves each candidate with ExaGO OPFLOW on AMD MI250X GCDs. One MPI rank
# is bound to each of the 8 GCDs per node (--ntasks-per-node=8, --gpus-per-node=8,
# --gpu-bind=closest, --cpus-per-task=7).
#
# Frontier batch-partition walltime bins (node-count -> max walltime):
#   1-91 nodes  -> 2h   (this template's default: -N 8, -t 02:00:00)
#   92-183      -> 6h
#   184-1881    -> 12h
# Accumulate work across successive 2h jobs with resumable rounds and
# `scripts/drive_campaign.py --chain` (afterok dependencies). Node count and
# walltime are overridable at submit time (drive_campaign --nodes/--time or
# `sbatch -N.. -t..`); keep the pair inside a legal bin.
# =============================================================================

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
BOOTSTRAP_SCRIPT=$ROOT/scripts/bootstrap_adaptive_campaign.py
SHARD_SCRIPT=$ROOT/scripts/shard_selected_candidates.py
MAP_SCRIPT=$ROOT/scripts/run_campaign_exago_ac_opf_round.py
REDUCE_SCRIPT=$ROOT/scripts/reduce_campaign_shards.py

PYBIN=${PYBIN:-python3.11}

# Canonical campaign virtualenv (pandas+pyarrow) so every campaign writes uniform
# parquet ledgers. Build it once with scripts/setup_frontier_venv.sh. Override
# PGDF_VENV to point elsewhere, or set PGDF_VENV= (empty) to force the module
# Python (ledgers then degrade to JSONL if pyarrow is unavailable).
PGDF_VENV=${PGDF_VENV-$ROOT/.venv}

# ----- ExaGO Frontier runtime environment (ROCm + spack deps for opflow) ------
export SRCDIR=$ROOT/external/ExaGO
if [[ -f "$SRCDIR/buildsystem/clang-hip/frontier/frontierExago.sh" ]]; then
  # base.sh references unset vars (e.g. EXTRA_CMAKE_ARGS); relax -e/-u to source.
  set +eu
  # shellcheck disable=SC1091
  source "$SRCDIR/buildsystem/clang-hip/frontier/frontierExago.sh"
  set -eu
fi
# Activate the campaign virtualenv (must follow the module env so it wins PATH).
if [[ -n "${PGDF_VENV:-}" && -f "${PGDF_VENV}/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${PGDF_VENV}/bin/activate"
elif [[ -n "${PGDF_VENV:-}" ]]; then
  echo "[warn] PGDF_VENV=$PGDF_VENV not found; using module '$PYBIN'." \
       "Ledgers will fall back to JSONL if pyarrow is unavailable." \
       "Run scripts/setup_frontier_venv.sh for uniform parquet ledgers." >&2
fi

export PGDF_EXAGO_BUILD_PROFILE=${PGDF_EXAGO_BUILD_PROFILE:-frontier}
export PGDF_EXAGO_OPFLOW_BIN=${PGDF_EXAGO_OPFLOW_BIN:-$ROOT/external/ExaGO/builds/frontier/install/bin/opflow}

# 45 PGLib large grid topologies (>1000 buses).
LARGE_CASES_DEFAULT="epigrids_florida_5658 epigrids_texas_7336 epigrids_midwest_10192 epigrids_western_20758 epigrids_eastern_78484 pglib_opf_case2000_goc pglib_opf_case10000_goc pglib_opf_case4661_sdet pglib_opf_case6470_rte pglib_opf_case13659_pegase pglib_opf_case1354_pegase pglib_opf_case1803_snem pglib_opf_case1888_rte pglib_opf_case1951_rte pglib_opf_case2312_goc pglib_opf_case2383wp_k pglib_opf_case2736sp_k pglib_opf_case2737sop_k pglib_opf_case2742_goc pglib_opf_case2746wop_k pglib_opf_case2746wp_k pglib_opf_case2848_rte pglib_opf_case2853_sdet pglib_opf_case2868_rte pglib_opf_case2869_pegase pglib_opf_case3012wp_k pglib_opf_case3022_goc pglib_opf_case3120sp_k pglib_opf_case3375wp_k pglib_opf_case3970_goc pglib_opf_case4020_goc pglib_opf_case4601_goc pglib_opf_case4619_goc pglib_opf_case4837_goc pglib_opf_case4917_goc pglib_opf_case6468_rte pglib_opf_case6495_rte pglib_opf_case6515_rte pglib_opf_case8387_pegase pglib_opf_case9241_pegase pglib_opf_case9591_goc pglib_opf_case10480_goc pglib_opf_case19402_goc pglib_opf_case24464_goc pglib_opf_case30000_goc"

CAMPAIGN_ID=${CAMPAIGN_ID:-exago_frontier_large_grids}
ROUND_INDEX=${ROUND_INDEX:-0}
CONFIG=${CONFIG:-configs/campaign_default.yaml}
CASES=${CASES:-"$LARGE_CASES_DEFAULT"}
PER_CASE=${PER_CASE:-500}
SAMPLER=${SAMPLER:-latin_hypercube}
BUDGET=${BUDGET:-600}
SEED=${SEED:-7}
SOLVER_ID=${SOLVER_ID:-exago_ac_opf_hiopsparsegpu_campaign}
SOLVER_MODE=${SOLVER_MODE:-gpu_then_ipopt}
TIMEOUT_S=${TIMEOUT_S:-1800}
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

echo "[$(date -u +%FT%TZ)] JOB=$SLURM_JOB_ID mode=map_reduce backend=exago campaign_id=$CAMPAIGN_ID round_index=$ROUND_INDEX"
echo "[$(date -u +%FT%TZ)] config=$CONFIG budget=$BUDGET per_case=$PER_CASE seed=$SEED max_k=$MAX_K"
echo "[$(date -u +%FT%TZ)] solver_id=$SOLVER_ID solver_mode=$SOLVER_MODE timeout_s=$TIMEOUT_S shard_count=$SHARD_COUNT"
echo "[$(date -u +%FT%TZ)] opflow_bin=$PGDF_EXAGO_OPFLOW_BIN"
echo "[$(date -u +%FT%TZ)] coverage_keys=$COVERAGE_KEYS min_per_bucket=$MIN_PER_BUCKET enforce=$ENFORCE_COVERAGE backfill=$BACKFILL_FROM_POOL"

RESUMING=0
if [[ "$RESUME" == "1" ]] && compgen -G "$SHARD_DIR/${SHARD_PREFIX}_*.jsonl" > /dev/null; then
  RESUMING=1
  echo "[$(date -u +%FT%TZ)] RESUME enabled: reusing existing shards under $SHARD_DIR; completed shards and finalized runs are skipped"
fi

if [[ "$RESUMING" == "0" && "$RUN_BOOTSTRAP" == "1" ]]; then
  read -r -a CASE_ARRAY <<< "$CASES"
  echo "[$(date -u +%FT%TZ)] Bootstrapping campaign candidates and round selection (${#CASE_ARRAY[@]} cases)"
  PYTHONPATH="$ROOT/src" "$PYBIN" "$BOOTSTRAP_SCRIPT" \
    --campaign-id "$CAMPAIGN_ID" \
    --config "$CONFIG" \
    --cases "${CASE_ARRAY[@]}" \
    --per-case "$PER_CASE" \
    --sampler "$SAMPLER" \
    --topologies-per-case "$TOPOLOGIES_PER_CASE" \
    --max-switched-branches "$MAX_SWITCHED_BRANCHES" \
    --max-k "$MAX_K" \
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
  PYTHONPATH="$ROOT/src" "$PYBIN" "$SHARD_SCRIPT" "${SHARD_ARGS[@]}"
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

echo "[$(date -u +%FT%TZ)] Starting GPU map stage with srun tasks=${SLURM_NTASKS} (1 rank per GCD)"

srun --nodes="${SLURM_JOB_NUM_NODES}" --ntasks="${SLURM_NTASKS}" \
     --cpus-per-task="${SLURM_CPUS_PER_TASK:-7}" \
     --gpus-per-task=1 --gpu-bind=closest \
     bash -c '
set -euo pipefail
ROOT="'$ROOT'"
PYBIN="'$PYBIN'"
CAMPAIGN_ID="'$CAMPAIGN_ID'"
ROUND_INDEX="'$ROUND_INDEX'"
ROUND_PAD="'$ROUND_PAD'"
CONFIG="'$CONFIG'"
SHARD_DIR="'$SHARD_DIR'"
SHARD_PREFIX="'$SHARD_PREFIX'"
MAP_SCRIPT="'$MAP_SCRIPT'"
SOLVER_ID="'$SOLVER_ID'"
SOLVER_MODE="'$SOLVER_MODE'"
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
TOTAL_SHARDS=$(wc -l < "$QUEUE_FILE")

TASK_ID=${SLURM_PROCID:-0}

echo "[$(date -u +%FT%TZ)] task=$TASK_ID host=$(hostname) rocr=${ROCR_VISIBLE_DEVICES:-unset} dynamic shard pickup total_shards=$TOTAL_SHARDS"
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
    --solver-mode "$SOLVER_MODE"
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
  PYTHONPATH="$ROOT/src" "$PYBIN" "$MAP_SCRIPT" "${ARGS[@]}"
  touch "$DONE_DIR/$shard_number"
done
'

echo "[$(date -u +%FT%TZ)] Map stage complete. Starting deterministic reduce stage"

PYTHONPATH="$ROOT/src" "$PYBIN" "$REDUCE_SCRIPT" \
  --campaign-id "$CAMPAIGN_ID" \
  --round-index "$ROUND_INDEX" \
  --config "$CONFIG" \
  --shard-campaign-ids-file "$SHARD_IDS_FILE"

echo "[$(date -u +%FT%TZ)] Completed ExaGO map/reduce campaign round job $SLURM_JOB_ID"
