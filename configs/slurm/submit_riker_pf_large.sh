#!/bin/bash
set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
SUPERVISOR=$ROOT/configs/slurm/riker_pf_campaign_supervisor.sbatch

ANCHOR_INDEX=${ANCHOR_INDEX:-$ROOT/data/derived/pf_anchors/andes_acopf_anchors.parquet}
CAMPAIGN_ID=${CAMPAIGN_ID:-riker_pf_complement_large_v1}
CONFIG=${CONFIG:-configs/pf_campaign_riker.yaml}
ROUNDS=${ROUNDS:-5}
COUNT=${COUNT:-100000}
SEED_BASE=${SEED_BASE:-20260925}
NODES=${NODES:-64}
NTASKS_PER_NODE=${NTASKS_PER_NODE:-32}
SHARD_MULTIPLIER=${SHARD_MULTIPLIER:-8}
SHARD_COUNT=${SHARD_COUNT:-$((NODES * NTASKS_PER_NODE * SHARD_MULTIPLIER))}
WALLTIME=${WALLTIME:-36:00:00}
RUNS_ROOT=${RUNS_ROOT:-data/outputs/runs/$CAMPAIGN_ID}
MAX_FAILURE_FRACTION=${MAX_FAILURE_FRACTION:-0.5}
ACCOUNT=${ACCOUNT:-lrn070}
QOS=${QOS:-normal}
DEP_TYPE=${DEP_TYPE:-afterany}
MAX_CYCLES=${MAX_CYCLES:-500}
AFTER=${AFTER:-}
DRY_RUN=${DRY_RUN:-0}
COINHSL_ENV=${COINHSL_ENV:-$HOME/.local/coinhsl/current/env.sh}

[[ -s "$ANCHOR_INDEX" ]] || {
  echo "Missing PF anchor index: $ANCHOR_INDEX" >&2
  echo "Build it first with scripts/build_pf_anchor_index.py from successful Andes AC-OPF samples." >&2
  exit 2
}
(( ROUNDS > 0 && COUNT > 0 && NODES > 0 && NTASKS_PER_NODE > 0 && SHARD_COUNT > 0 )) || {
  echo "ROUNDS, COUNT, NODES, NTASKS_PER_NODE, and SHARD_COUNT must be positive" >&2
  exit 2
}
[[ -r "$COINHSL_ENV" ]] || { echo "Missing private Coin-HSL activation: $COINHSL_ENV" >&2; exit 2; }
source "$COINHSL_ENV"
[[ -r "${IPOPT_HSL_LIBRARY:-}" ]] || { echo "Missing Coin-HSL library: ${IPOPT_HSL_LIBRARY:-unset}" >&2; exit 2; }

export CAMPAIGN_ID CONFIG ROUNDS COUNT SEED_BASE ACCOUNT QOS DEP_TYPE MAX_CYCLES ANCHOR_INDEX RUNS_ROOT MAX_FAILURE_FRACTION COINHSL_ENV
export ROUND_NODES=$NODES ROUND_TASKS_PER_NODE=$NTASKS_PER_NODE ROUND_TIME=$WALLTIME ROUND_SHARD_COUNT=$SHARD_COUNT SHARD_MULTIPLIER

CMD=(sbatch -A "$ACCOUNT")
[[ -n "$QOS" ]] && CMD+=(-q "$QOS")
[[ -n "$AFTER" ]] && CMD+=(--dependency="afterany:$AFTER")
CMD+=(--export=ALL,CYCLE=1 "$SUPERVISOR")

echo "[submit_riker_pf_large] campaign=$CAMPAIGN_ID rounds=$ROUNDS candidates_per_round=$COUNT"
echo "[submit_riker_pf_large] config=$CONFIG seed_base=$SEED_BASE"
echo "[submit_riker_pf_large] nodes=$NODES tasks_per_node=$NTASKS_PER_NODE shards=$SHARD_COUNT walltime=$WALLTIME"
echo "[submit_riker_pf_large] anchor_index=$ANCHOR_INDEX runs_root=$RUNS_ROOT after=${AFTER:-none}"
echo "[submit_riker_pf_large] linear_solver_fallback=default,ma27,ma57"
echo "[submit_riker_pf_large] cmd=${CMD[*]}"

[[ "$DRY_RUN" == "1" ]] && exit 0
cd "$ROOT"
exec "${CMD[@]}"