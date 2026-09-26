#!/bin/bash
set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
SBATCH_FILE=$ROOT/configs/slurm/riker_pf_mapreduce.sbatch

ANCHOR_INDEX=${ANCHOR_INDEX:-$ROOT/data/derived/pf_anchors/andes_acopf_anchors.parquet}
CAMPAIGN_ID=${CAMPAIGN_ID:-riker_pf_complement_large_v1}
ROUND_INDEX=${ROUND_INDEX:-0}
COUNT=${COUNT:-100000}
NODES=${NODES:-8}
NTASKS_PER_NODE=${NTASKS_PER_NODE:-32}
SHARD_MULTIPLIER=${SHARD_MULTIPLIER:-8}
SHARD_COUNT=${SHARD_COUNT:-$((NODES * NTASKS_PER_NODE * SHARD_MULTIPLIER))}
WALLTIME=${WALLTIME:-02:00:00}
SEED=${SEED:-$((20260925 + ROUND_INDEX))}
RUNS_ROOT=${RUNS_ROOT:-data/outputs/runs/$CAMPAIGN_ID}
MAX_FAILURE_FRACTION=${MAX_FAILURE_FRACTION:-0.5}
RESUME=${RESUME:-0}
JOB_NAME=${JOB_NAME:-pgdf_pf_large}
COINHSL_ENV=${COINHSL_ENV:-$HOME/.local/coinhsl/current/env.sh}

[[ -s "$ANCHOR_INDEX" ]] || {
  echo "Missing PF anchor index: $ANCHOR_INDEX" >&2
  echo "Build it first with scripts/build_pf_anchor_index.py from successful Andes AC-OPF samples." >&2
  exit 2
}
(( COUNT > 0 && NODES > 0 && NTASKS_PER_NODE > 0 && SHARD_COUNT > 0 )) || {
  echo "COUNT, NODES, NTASKS_PER_NODE, and SHARD_COUNT must be positive" >&2
  exit 2
}
[[ -r "$COINHSL_ENV" ]] || { echo "Missing private Coin-HSL activation: $COINHSL_ENV" >&2; exit 2; }
source "$COINHSL_ENV"
[[ -r "${IPOPT_HSL_LIBRARY:-}" ]] || { echo "Missing Coin-HSL library: ${IPOPT_HSL_LIBRARY:-unset}" >&2; exit 2; }

echo "[submit_riker_pf_large] campaign=$CAMPAIGN_ID round=$ROUND_INDEX candidates=$COUNT"
echo "[submit_riker_pf_large] nodes=$NODES tasks_per_node=$NTASKS_PER_NODE shards=$SHARD_COUNT walltime=$WALLTIME"
echo "[submit_riker_pf_large] anchor_index=$ANCHOR_INDEX runs_root=$RUNS_ROOT resume=$RESUME"
echo "[submit_riker_pf_large] linear_solver_fallback=default,ma27,ma57"

exec sbatch \
  -J "$JOB_NAME" \
  -N "$NODES" \
  --ntasks-per-node="$NTASKS_PER_NODE" \
  -t "$WALLTIME" \
  --export="ALL,ANCHOR_INDEX=$ANCHOR_INDEX,CAMPAIGN_ID=$CAMPAIGN_ID,ROUND_INDEX=$ROUND_INDEX,COUNT=$COUNT,SEED=$SEED,SHARD_COUNT=$SHARD_COUNT,RUNS_ROOT=$RUNS_ROOT,MAX_FAILURE_FRACTION=$MAX_FAILURE_FRACTION,RESUME=$RESUME,IPOPT_HSL_LIBRARY=$IPOPT_HSL_LIBRARY,COINHSL_ENV=$COINHSL_ENV,LD_LIBRARY_PATH=$LD_LIBRARY_PATH" \
  "$SBATCH_FILE"