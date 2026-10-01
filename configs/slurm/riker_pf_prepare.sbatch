#!/bin/bash
#SBATCH -J pgdf_pf_prep
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
ulimit -c 0  # disable core dumps: Julia/Ipopt crashes were filling Lustre

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
PYBIN=${PYBIN:-$ROOT/.venv-riker/bin/python}
ANCHOR_INDEX=${ANCHOR_INDEX:-$ROOT/data/derived/pf_anchors/andes_acopf_anchors.parquet}
CAMPAIGN_ID=${CAMPAIGN_ID:-riker_pf_complement_v1}
ROUND_INDEX=${ROUND_INDEX:-0}
CONFIG=${CONFIG:-configs/pf_campaign_riker.yaml}
COUNT=${COUNT:-10000}
SEED_BASE=${SEED_BASE:-20260925}
SEED=${SEED:-$((SEED_BASE + ROUND_INDEX))}
RUNS_ROOT=${RUNS_ROOT:-data/outputs/runs/$CAMPAIGN_ID}
RESUME=${RESUME:-1}
SHARD_COUNT=${SHARD_COUNT:-$(( ${ROUND_NODES:-1} * ${ROUND_TASKS_PER_NODE:-32} * 8 ))}
ROUND_NODES=${ROUND_NODES:-1}
ROUND_TASKS_PER_NODE=${ROUND_TASKS_PER_NODE:-32}
ROUND_TIME=${ROUND_TIME:-36:00:00}
MAP_SCRIPT=${MAP_SCRIPT:-$ROOT/configs/slurm/riker_pf_mapreduce.sbatch}

printf -v ROUND_PAD "%03d" "$ROUND_INDEX"
ROUND_DIR="$ROOT/data/outputs/campaigns/$CAMPAIGN_ID/round_summaries/round_${ROUND_PAD}_pf"
CANDIDATES="$ROUND_DIR/candidates.jsonl"
SHARD_DIR="$ROUND_DIR/shards"
IDS_FILE="$SHARD_DIR/shard_campaign_ids.txt"
QUEUE_DIR="$SHARD_DIR/queue"
QUEUE_FILE="$QUEUE_DIR/shards.txt"
QUEUE_READY="$QUEUE_DIR/ready"
MAP_JOB_FILE="$ROUND_DIR/map_after_prepare_${SLURM_JOB_ID:-manual}.txt"

cd "$ROOT"
mkdir -p data/scratch/logs "$QUEUE_DIR"
[[ -x "$PYBIN" ]] || { echo "Missing Python: $PYBIN" >&2; exit 2; }
[[ -s "$ANCHOR_INDEX" ]] || { echo "Missing anchor index: $ANCHOR_INDEX" >&2; exit 2; }

if [[ "$RESUME" != "1" || ! -s "$CANDIDATES" ]]; then
  PRIOR_ARGS=()
  while IFS= read -r prior; do PRIOR_ARGS+=("$prior"); done < <(find "$ROOT/$RUNS_ROOT" -path '*/pf/samples.jsonl' -type f 2>/dev/null | sort)
  CMD=("$PYBIN" scripts/generate_pf_candidates.py --anchor-index "$ANCHOR_INDEX" --config "$ROOT/$CONFIG" --output "$CANDIDATES" --count "$COUNT" --seed "$SEED")
  if (( ${#PRIOR_ARGS[@]} > 0 )); then CMD+=(--prior-samples "${PRIOR_ARGS[@]}"); fi
  PYTHONPATH="$ROOT/src" "${CMD[@]}"
  PYTHONPATH="$ROOT/src" "$PYBIN" scripts/shard_selected_candidates.py \
    --input "$CANDIDATES" --num-shards "$SHARD_COUNT" --out-dir "$SHARD_DIR" \
    --prefix selected --coverage-keys dataset_split,topology_class,control_distance_stratum \
    --min-per-bucket 1 --enforce-coverage --full-budget --stream --assignment contiguous
else
  echo "[$(date -u +%FT%TZ)] prepare: reusing candidates and shards"
fi

if [[ ! -e "$QUEUE_READY" || ! -s "$IDS_FILE" || ! -s "$QUEUE_FILE" ]]; then
  find "$SHARD_DIR" -maxdepth 1 -type f -name 'selected_*.jsonl' -size +0 -printf '%f\n' \
    | sed -e 's/^selected_//' -e 's/\.jsonl$//' \
    | sort > "$QUEUE_FILE.in_progress"
  awk -v campaign="$CAMPAIGN_ID" -v round="$ROUND_PAD" \
    '{ print campaign "__r" round "__s" $0 }' "$QUEUE_FILE.in_progress" > "$IDS_FILE.in_progress"
  mv "$QUEUE_FILE.in_progress" "$QUEUE_FILE"
  mv "$IDS_FILE.in_progress" "$IDS_FILE"
  touch "$QUEUE_READY"
fi
[[ -s "$QUEUE_FILE" ]] || { echo "No nonempty PF shards" >&2; exit 2; }

if [[ ! -s "$MAP_JOB_FILE" ]]; then
  MAP_CMD=(sbatch --parsable -A "${ACCOUNT:-lrn070}")
  [[ -n "${QOS:-normal}" ]] && MAP_CMD+=(-q "${QOS:-normal}")
  MAP_CMD+=(--nodes "$ROUND_NODES" --ntasks-per-node "$ROUND_TASKS_PER_NODE" --cpus-per-task 1 --time "$ROUND_TIME")
  MAP_CMD+=(--export="ALL,CAMPAIGN_ID=$CAMPAIGN_ID,ROUND_INDEX=$ROUND_INDEX,CONFIG=$CONFIG,COUNT=$COUNT,SEED_BASE=$SEED_BASE,RUNS_ROOT=$RUNS_ROOT,SHARD_COUNT=$SHARD_COUNT,RESUME=1,ROUND_SELF_CONTINUE=${ROUND_SELF_CONTINUE:-1},CYCLE=${CYCLE:-1}" "$MAP_SCRIPT")
  map_job=$("${MAP_CMD[@]}")
  printf '%s\n' "${map_job%%;*}" > "$MAP_JOB_FILE.in_progress"
  mv "$MAP_JOB_FILE.in_progress" "$MAP_JOB_FILE"
  echo "[$(date -u +%FT%TZ)] map_job=${map_job%%;*} nodes=$ROUND_NODES tasks_per_node=$ROUND_TASKS_PER_NODE"
fi