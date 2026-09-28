#!/bin/bash
#SBATCH -J pgdf_riker_pf
#SBATCH -A lrn070
#SBATCH -p batch
#SBATCH --qos=normal
#SBATCH -N 1
#SBATCH --ntasks-per-node=32
#SBATCH --cpus-per-task=1
#SBATCH -t 02:00:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
PYBIN=${PYBIN:-$ROOT/.venv-riker/bin/python}
ANCHOR_INDEX=${ANCHOR_INDEX:-$ROOT/data/derived/pf_anchors/andes_acopf_anchors.parquet}
CAMPAIGN_ID=${CAMPAIGN_ID:-riker_pf_complement_v1}
ROUND_INDEX=${ROUND_INDEX:-0}
CONFIG=${CONFIG:-configs/pf_campaign_riker.yaml}
COUNT=${COUNT:-10000}
SEED_BASE=${SEED_BASE:-20260925}
SEED=${SEED:-$((SEED_BASE + ROUND_INDEX))}
RUNS_ROOT=${RUNS_ROOT:-data/outputs/runs/riker_pf_complement_v1}
SOLVER_ID=${SOLVER_ID:-powermodels_pf_ipopt}
TIMEOUT_S=${TIMEOUT_S:-1200}
SHARD_COUNT=${SHARD_COUNT:-$(( ${SLURM_NTASKS:-32} * 8 ))}
MAX_FAILURE_FRACTION=${MAX_FAILURE_FRACTION:-0.25}
RESUME=${RESUME:-0}

export PGDF_JULIA_BIN=${PGDF_JULIA_BIN:-$ROOT/.software/bin/julia}
export PGDF_JULIA_PROJECT_DIR=${PGDF_JULIA_PROJECT_DIR:-$ROOT/julia/lockfiles/riker}
export JULIA_DEPOT_PATH=${JULIA_DEPOT_PATH:-$ROOT/.julia_depot_riker}
export OPENBLAS_NUM_THREADS=1
export JULIA_NUM_THREADS=1

COINHSL_ENV=${COINHSL_ENV:-$HOME/.local/coinhsl/current/env.sh}
[[ -r "$COINHSL_ENV" ]] || { echo "Missing private Coin-HSL activation: $COINHSL_ENV" >&2; exit 2; }
source "$COINHSL_ENV"
[[ -r "${IPOPT_HSL_LIBRARY:-}" ]] || { echo "Missing Coin-HSL library: ${IPOPT_HSL_LIBRARY:-unset}" >&2; exit 2; }

[[ -x "$PYBIN" ]] || { echo "Missing Python: $PYBIN" >&2; exit 2; }
[[ -x "$PGDF_JULIA_BIN" ]] || { echo "Missing Julia wrapper: $PGDF_JULIA_BIN" >&2; exit 2; }
[[ -s "$ANCHOR_INDEX" ]] || { echo "Missing anchor index: $ANCHOR_INDEX" >&2; exit 2; }
PYTHONPATH="$ROOT/src" "$PYBIN" -c 'import grid_data_factory, pydantic, pyarrow, yaml'
echo "IPOPT_LINEAR_SOLVER_FALLBACK=default,ma27,ma57"

printf -v ROUND_PAD "%03d" "$ROUND_INDEX"
CAMPAIGN_ROOT="$ROOT/data/outputs/campaigns/$CAMPAIGN_ID"
ROUND_DIR="$CAMPAIGN_ROOT/round_summaries/round_${ROUND_PAD}_pf"
CANDIDATES="$ROUND_DIR/candidates.jsonl"
SHARD_DIR="$ROUND_DIR/shards"
IDS_FILE="$SHARD_DIR/shard_campaign_ids.txt"
QUEUE_DIR="$SHARD_DIR/queue"
QUEUE_FILE="$QUEUE_DIR/shards.txt"
INDEX_FILE="$QUEUE_DIR/next_index.txt"
LOCK_DIR="$QUEUE_DIR/lock"
DONE_DIR="$QUEUE_DIR/done"
QUEUE_READY="$QUEUE_DIR/ready"
REDUCE_MARKER="$CAMPAIGN_ROOT/round_summaries/round_${ROUND_PAD}_pf_reduce_report.json"
mkdir -p "$ROOT/data/scratch/logs" "$ROUND_DIR" "$DONE_DIR"

if [[ "${ROUND_SELF_CONTINUE:-0}" == "1" ]]; then
  [[ "${SLURM_JOB_ID:-}" =~ ^[0-9]+$ ]] || { echo "Missing SLURM_JOB_ID for self-continuation" >&2; exit 2; }
  FINALIZER_SCRIPT=${FINALIZER_SCRIPT:-$ROOT/configs/slurm/riker_pf_finalize.sbatch}
  FINALIZER_FILE="$ROUND_DIR/finalizer_after_job_${SLURM_JOB_ID}.txt"
  if [[ ! -s "$FINALIZER_FILE" ]]; then
    FINALIZER_CMD=(sbatch --parsable -A "${ACCOUNT:-lrn070}")
    [[ -n "${QOS:-normal}" ]] && FINALIZER_CMD+=(-q "${QOS:-normal}")
    FINALIZER_CMD+=(--dependency="afterany:${SLURM_JOB_ID}" --export="ALL,CAMPAIGN_ID=$CAMPAIGN_ID,ROUND_INDEX=$ROUND_INDEX,CONFIG=$CONFIG,RUNS_ROOT=$RUNS_ROOT,SHARD_IDS_FILE=$IDS_FILE,CYCLE=${CYCLE:-1}" "$FINALIZER_SCRIPT")
    finalizer_job=$("${FINALIZER_CMD[@]}")
    printf '%s\n' "${finalizer_job%%;*}" > "$FINALIZER_FILE.in_progress"
    mv "$FINALIZER_FILE.in_progress" "$FINALIZER_FILE"
    echo "[$(date -u +%FT%TZ)] finalizer_job=${finalizer_job%%;*} dependency=afterany:${SLURM_JOB_ID}"
  fi
fi

if [[ "$RESUME" == "1" && -s "$REDUCE_MARKER" ]] && grep -q '"ok": true' "$REDUCE_MARKER"; then
  echo "[$(date -u +%FT%TZ)] PF round already reduced successfully: $REDUCE_MARKER"
  exit 0
fi

if [[ "${ROUND_SELF_CONTINUE:-0}" == "1" && ( ! -s "$CANDIDATES" || ! -s "$QUEUE_READY" || ! -s "$IDS_FILE" || ! -s "$QUEUE_FILE" ) ]]; then
  echo "PF map inputs are incomplete; submit through riker_pf_campaign_supervisor.sbatch" >&2
  exit 2
fi

if [[ "$RESUME" != "1" || ! -s "$CANDIDATES" ]]; then
  PRIOR_ARGS=()
  while IFS= read -r prior; do PRIOR_ARGS+=("$prior"); done < <(find "$ROOT/$RUNS_ROOT" -path '*/pf/samples.jsonl' -type f 2>/dev/null | sort)
  CMD=("$PYBIN" "$ROOT/scripts/generate_pf_candidates.py" --anchor-index "$ANCHOR_INDEX" --config "$ROOT/$CONFIG" --output "$CANDIDATES" --count "$COUNT" --seed "$SEED")
  if (( ${#PRIOR_ARGS[@]} > 0 )); then CMD+=(--prior-samples "${PRIOR_ARGS[@]}"); fi
  PYTHONPATH="$ROOT/src" "${CMD[@]}"
  PYTHONPATH="$ROOT/src" "$PYBIN" "$ROOT/scripts/shard_selected_candidates.py" \
    --input "$CANDIDATES" --num-shards "$SHARD_COUNT" --out-dir "$SHARD_DIR" \
    --prefix selected --coverage-keys dataset_split,topology_class,control_distance_stratum \
    --min-per-bucket 1 --enforce-coverage --full-budget --stream --assignment contiguous
else
  echo "[$(date -u +%FT%TZ)] resume: reusing candidates and shards"
fi

if [[ ! -s "$QUEUE_READY" || ! -s "$IDS_FILE" || ! -s "$QUEUE_FILE" ]]; then
  queue_tmp="$QUEUE_FILE.in_progress"
  ids_tmp="$IDS_FILE.in_progress"
  find "$SHARD_DIR" -maxdepth 1 -type f -name 'selected_*.jsonl' -size +0 -printf '%f\n' \
    | sed -e 's/^selected_//' -e 's/\.jsonl$//' \
    | sort > "$queue_tmp"
  awk -v campaign="$CAMPAIGN_ID" -v round="$ROUND_PAD" \
    '{ print campaign "__r" round "__s" $0 }' "$queue_tmp" > "$ids_tmp"
  mv "$queue_tmp" "$QUEUE_FILE"
  mv "$ids_tmp" "$IDS_FILE"
  touch "$QUEUE_READY"
else
  echo "[$(date -u +%FT%TZ)] resume: reusing prepared shard queue"
fi
[[ -s "$QUEUE_FILE" ]] || { echo "No nonempty PF shards" >&2; exit 2; }
rmdir "$LOCK_DIR" 2>/dev/null || true
echo 0 > "$INDEX_FILE"

srun --ntasks="${SLURM_NTASKS}" --nodes="${SLURM_JOB_NUM_NODES}" --signal=USR1@120 bash -lc '
set -euo pipefail
trap '\''true'\'' USR1
ROOT="'$ROOT'"; PYBIN="'$PYBIN'"; CAMPAIGN_ID="'$CAMPAIGN_ID'"; ROUND_INDEX="'$ROUND_INDEX'"; ROUND_PAD="'$ROUND_PAD'"
CONFIG="'$CONFIG'"; SHARD_DIR="'$SHARD_DIR'"; QUEUE_FILE="'$QUEUE_FILE'"; INDEX_FILE="'$INDEX_FILE'"; LOCK_DIR="'$LOCK_DIR'"; DONE_DIR="'$DONE_DIR'"
RUNS_ROOT="'$RUNS_ROOT'"; SOLVER_ID="'$SOLVER_ID'"; TIMEOUT_S="'$TIMEOUT_S'"; MAX_FAILURE_FRACTION="'$MAX_FAILURE_FRACTION'"; RESUME="'$RESUME'"
export PGDF_JULIA_BIN="'$PGDF_JULIA_BIN'" PGDF_JULIA_PROJECT_DIR="'$PGDF_JULIA_PROJECT_DIR'" JULIA_DEPOT_PATH="'$JULIA_DEPOT_PATH'" OPENBLAS_NUM_THREADS=1 JULIA_NUM_THREADS=1
export IPOPT_HSL_LIBRARY="'${IPOPT_HSL_LIBRARY:-}'" LD_LIBRARY_PATH="'${LD_LIBRARY_PATH:-}'"
total=$(wc -l < "$QUEUE_FILE")
while true; do
  while ! mkdir "$LOCK_DIR" 2>/dev/null; do :; done
  next=$(cat "$INDEX_FILE")
  if (( next >= total )); then rmdir "$LOCK_DIR"; break; fi
  echo $((next + 1)) > "$INDEX_FILE"; rmdir "$LOCK_DIR"
  shard=$(sed -n "$((next + 1))p" "$QUEUE_FILE")
  [[ "$RESUME" == "1" && -f "$DONE_DIR/$shard" ]] && continue
  shard_campaign="${CAMPAIGN_ID}__r${ROUND_PAD}__s${shard}"
  args=(--campaign-id "$shard_campaign" --round-index "$ROUND_INDEX" --candidates-jsonl "$SHARD_DIR/selected_${shard}.jsonl" --config "$ROOT/$CONFIG" --runs-root "$ROOT/$RUNS_ROOT/mapreduce_round_${ROUND_PAD}/shard_${shard}" --solver-id "$SOLVER_ID" --timeout-s "$TIMEOUT_S" --max-failure-fraction "$MAX_FAILURE_FRACTION" --record-quality-failure --continue-on-error)
  [[ "$RESUME" == "1" ]] && args+=(--resume)
  PYTHONPATH="$ROOT/src" "$PYBIN" "$ROOT/scripts/run_campaign_pf_round.py" "${args[@]}"
  touch "$DONE_DIR/$shard"
done
'

if [[ "${ROUND_SELF_CONTINUE:-0}" == "1" ]]; then
  echo "[$(date -u +%FT%TZ)] PF map phase complete: campaign=$CAMPAIGN_ID round=$ROUND_INDEX"
else
  PYTHONPATH="$ROOT/src" "$PYBIN" "$ROOT/scripts/reduce_pf_campaign_shards.py" \
    --campaign-id "$CAMPAIGN_ID" --round-index "$ROUND_INDEX" --config "$ROOT/$CONFIG" \
    --shard-campaign-ids-file "$IDS_FILE" --runs-root "$ROOT/$RUNS_ROOT"
  echo "[$(date -u +%FT%TZ)] PF round complete: campaign=$CAMPAIGN_ID round=$ROUND_INDEX"
fi