#!/bin/bash
#SBATCH -J pgdf_exago_mr
#SBATCH -A LRN070
#SBATCH -p batch
#SBATCH -N 4
#SBATCH --ntasks-per-node=4
#SBATCH --cpus-per-task=8
#SBATCH -t 36:00:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

# =============================================================================
# Andes CPU ExaGO IPOPT AC-OPF map/reduce campaign round.
#
# Targets activsg70k and activsg82k only -- cases too large for Frontier GPU.
# Uses IPOPT (CPU) instead of HiOp (GPU); timeout_s=7200 (2 h per solve).
#
# 4 nodes x 4 tasks/node = 16 parallel IPOPT solvers.
# Max throughput at 2 h per solve: 16 workers * (36 h / 2 h) = 288 solves/round.
#
# Submit from an Andes login node:
#   CAMPAIGN_ID=exago_andes_activsg_g00 \
#   RUNS_ROOT=data/outputs/runs/exago_andes_activsg/g00 \
#   ACCOUNT=LRN070 \
#   ROUND_SBATCH=configs/slurm/andes_exago_acopf_mapreduce_4n_36h.sbatch \
#   ROUNDS=200 \
#     scripts/submit_exago_frontier_campaign.sh
#
# Binary: external/ExaGO-andes-cpu-latest/builds/andes-cpu-latest/install/bin/opflow
# Built with: OpenMPI 5.0.5 from /sw/andes/spack-envs/sw-25.04/
# IPOPT: if `ldd opflow` shows libipopt.so.3 => not found, load the IPOPT module
# on Andes or set IPOPT_LIB_DIR below to the directory containing libipopt.so.3.
# =============================================================================

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
BOOTSTRAP_SCRIPT=$ROOT/scripts/bootstrap_adaptive_campaign.py
SHARD_SCRIPT=$ROOT/scripts/shard_selected_candidates.py
MAP_SCRIPT=$ROOT/scripts/run_campaign_exago_ac_opf_round.py
REDUCE_SCRIPT=$ROOT/scripts/reduce_campaign_shards.py

PYBIN=${PYBIN:-python3.11}
PGDF_VENV=${PGDF_VENV-$ROOT/.venv}

# ----- Andes CPU ExaGO runtime environment -----------------------------------
# Load OpenMPI so mpiexec/srun finds the right libmpi. The opflow binary was
# linked against OpenMPI 5.0.5 from /sw/andes/spack-envs/sw-25.04/.
module load openmpi 2>/dev/null || true

# IPOPT library: the binary was built with IPOPT from the user spack install.
# If that /tmp path is gone, set IPOPT_LIB_DIR to where libipopt.so.3 lives
# (e.g. from `module load ipopt` or a persistent spack prefix).
IPOPT_LIB_DIR=${IPOPT_LIB_DIR:-}
if [[ -z "$IPOPT_LIB_DIR" ]]; then
  # Try the original spack /tmp build path (only valid on Andes compute nodes
  # where the spack install was performed; likely absent after node reuse).
  _tmp_ipopt=/tmp/mlupopa/spack-install/linux-zen2/ipopt-3.14.14-z2ayx5wqcfaqtc444w22rlygqerfhr42/lib
  if [[ -d "$_tmp_ipopt" ]]; then
    IPOPT_LIB_DIR="$_tmp_ipopt"
  fi
fi
if [[ -n "$IPOPT_LIB_DIR" ]]; then
  export LD_LIBRARY_PATH="${IPOPT_LIB_DIR}:${LD_LIBRARY_PATH:-}"
else
  echo "[warn] IPOPT_LIB_DIR not set and /tmp spack path absent." \
       "If opflow fails with 'libipopt.so.3 => not found'," \
       "set IPOPT_LIB_DIR to the directory containing libipopt.so.3." >&2
fi

# Activate the campaign virtualenv (must follow module setup so it wins PATH).
if [[ -n "${PGDF_VENV:-}" && -f "${PGDF_VENV}/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${PGDF_VENV}/bin/activate"
fi

# ExaGO Andes CPU build: IPOPT only, no HiOp/ROCm.
export PGDF_EXAGO_OPFLOW_BIN=${PGDF_EXAGO_OPFLOW_BIN:-$ROOT/external/ExaGO-andes-cpu-latest/builds/andes-cpu-latest/install/bin/opflow}

# Only the two activsg cases run on Andes; everything else is handled on Frontier.
ANDES_CASES_DEFAULT="activsg70k activsg82k"

CAMPAIGN_ID=${CAMPAIGN_ID:-exago_andes_activsg}
ROUND_INDEX=${ROUND_INDEX:-0}
CONFIG=${CONFIG:-configs/campaign_default.yaml}
CASES=${CASES:-"$ANDES_CASES_DEFAULT"}
PER_CASE=${PER_CASE:-500}
SAMPLER=${SAMPLER:-latin_hypercube}
BUDGET=${BUDGET:-250}
SEED=${SEED:-7}
SOLVER_ID=${SOLVER_ID:-exago_ac_opf_ipopt_campaign}
SOLVER_MODE=${SOLVER_MODE:-ipopt_only}
# IPOPT on 70k-82k bus networks is slow; 2 h gives MUMPS time to factor.
TIMEOUT_S=${TIMEOUT_S:-7200}
EXECUTION_POLICY=${EXECUTION_POLICY:-}
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
SEQ_CASCADE_PER_OP=${SEQ_CASCADE_PER_OP:-1}
SEQ_MAX_LEN=${SEQ_MAX_LEN:-10}
RESUME=${RESUME:-0}
BOOTSTRAP_WORKERS=${BOOTSTRAP_WORKERS:-16}

printf -v ROUND_PAD "%03d" "$ROUND_INDEX"
CAMPAIGN_ROOT="$ROOT/data/outputs/campaigns/$CAMPAIGN_ID"
SELECTED_JSONL_DEFAULT="$CAMPAIGN_ROOT/round_summaries/round_${ROUND_PAD}_selected_candidates.jsonl"
SELECTED_JSONL=${SELECTED_JSONL_OVERRIDE:-$SELECTED_JSONL_DEFAULT}
SHARD_DIR="$CAMPAIGN_ROOT/round_summaries/round_${ROUND_PAD}_shards"
SHARD_PREFIX="selected"
SHARD_IDS_FILE="$SHARD_DIR/shard_campaign_ids.txt"
POOL_JSONL_DEFAULT="$CAMPAIGN_ROOT/round${ROUND_INDEX}_screened_candidates.jsonl"
if [[ ! -s "$POOL_JSONL_DEFAULT" ]]; then
  POOL_JSONL_DEFAULT="$CAMPAIGN_ROOT/round0_screened_candidates.jsonl"
fi
POOL_JSONL=${POOL_JSONL:-$POOL_JSONL_DEFAULT}
QUEUE_DIR="$SHARD_DIR/queue"
QUEUE_FILE="$QUEUE_DIR/shards.txt"
INDEX_FILE="$QUEUE_DIR/next_index.txt"
LOCK_DIR="$QUEUE_DIR/lock"
DONE_DIR="$QUEUE_DIR/done"

mkdir -p "$ROOT/data/scratch/logs"

echo "[$(date -u +%FT%TZ)] JOB=$SLURM_JOB_ID mode=map_reduce backend=exago_ipopt campaign_id=$CAMPAIGN_ID round_index=$ROUND_INDEX"
echo "[$(date -u +%FT%TZ)] config=$CONFIG budget=$BUDGET per_case=$PER_CASE seed=$SEED max_k=$MAX_K"
echo "[$(date -u +%FT%TZ)] contingencies=simultaneous+cascade seq_cascade_per_op=$SEQ_CASCADE_PER_OP seq_max_len=$SEQ_MAX_LEN"
echo "[$(date -u +%FT%TZ)] solver_id=$SOLVER_ID solver_mode=$SOLVER_MODE timeout_s=$TIMEOUT_S shard_count=$SHARD_COUNT"
echo "[$(date -u +%FT%TZ)] execution_policy=${EXECUTION_POLICY:-none}"
echo "[$(date -u +%FT%TZ)] selected_jsonl=$SELECTED_JSONL"
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
  --stream
)
if [[ "$ENFORCE_COVERAGE" == "1" ]]; then
  SHARD_ARGS+=(--enforce-coverage)
fi
if [[ "$BACKFILL_FROM_POOL" == "1" && -s "$POOL_JSONL" ]]; then
  SHARD_ARGS+=(--backfill-from-pool --pool-jsonl "$POOL_JSONL")
elif [[ -s "$POOL_JSONL" ]]; then
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

# opflow is MPI-linked (PETSc). Each solve must run as its own single-task srun
# step; IPOPT aborts with "does not support execution in parallel" if inherited
# MPI world size > 1. Same pattern as Frontier, minus the GPU flags.
export PGDF_EXAGO_SRUN_PREFIX="srun --overlap --exact -N1 -n1 --cpus-per-task=${SLURM_CPUS_PER_TASK:-8}"

# 4 workers per node (matches --ntasks-per-node=4). Each pulls shards dynamically.
MAP_CONCURRENCY=${MAP_CONCURRENCY:-$(( SLURM_JOB_NUM_NODES * 4 ))}
TOTAL_SHARDS=$(wc -l < "$QUEUE_FILE")
echo "[$(date -u +%FT%TZ)] map_concurrency=$MAP_CONCURRENCY total_shards=$TOTAL_SHARDS srun_prefix='$PGDF_EXAGO_SRUN_PREFIX'"

map_worker() {
  local TASK_ID="$1"
  echo "[$(date -u +%FT%TZ)] worker=$TASK_ID host=$(hostname) started dynamic shard pickup total_shards=$TOTAL_SHARDS"
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
      echo "[$(date -u +%FT%TZ)] worker=$TASK_ID shard_number=$shard_number already complete (resume) skipping"
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
    if [[ -n "$EXECUTION_POLICY" ]]; then
      ARGS+=(--execution-policy "$EXECUTION_POLICY")
    fi
    if [[ "$MAX_CANDIDATES" != "0" ]]; then
      ARGS+=(--max-candidates "$MAX_CANDIDATES")
    fi
    if [[ "$CONTINUE_ON_ERROR" == "1" ]]; then
      ARGS+=(--continue-on-error)
    fi
    if [[ "$RESUMING" == "1" ]]; then
      ARGS+=(--resume)
    fi

    echo "[$(date -u +%FT%TZ)] worker=$TASK_ID shard_number=$shard_number shard_campaign_id=$SHARD_CAMPAIGN_ID"
    if PYTHONPATH="$ROOT/src" "$PYBIN" "$MAP_SCRIPT" "${ARGS[@]}"; then
      touch "$DONE_DIR/$shard_number"
    else
      echo "[$(date -u +%FT%TZ)] worker=$TASK_ID shard_number=$shard_number MAP_SCRIPT failed (rc=$?); leaving unmarked for resume" >&2
    fi
  done
  echo "[$(date -u +%FT%TZ)] worker=$TASK_ID drained queue; exiting"
}

map_pids=()
for ((w=0; w<MAP_CONCURRENCY; w++)); do
  map_worker "$w" &
  map_pids+=($!)
done
map_rc=0
for p in "${map_pids[@]}"; do
  wait "$p" || map_rc=$?
done
if (( map_rc != 0 )); then
  echo "[$(date -u +%FT%TZ)] WARNING: at least one map worker exited non-zero (rc=$map_rc)" >&2
fi

echo "[$(date -u +%FT%TZ)] Map stage complete. Starting deterministic reduce stage"

PYTHONPATH="$ROOT/src" "$PYBIN" "$REDUCE_SCRIPT" \
  --campaign-id "$CAMPAIGN_ID" \
  --round-index "$ROUND_INDEX" \
  --config "$CONFIG" \
  --shard-campaign-ids-file "$SHARD_IDS_FILE"

echo "[$(date -u +%FT%TZ)] Completed ExaGO IPOPT map/reduce campaign round job $SLURM_JOB_ID"
