#!/bin/bash
#SBATCH -J pgdf_pf_sup
#SBATCH -A lrn070
#SBATCH -p batch
#SBATCH --qos=normal
#SBATCH -N 1
#SBATCH -n 1
#SBATCH --cpus-per-task=1
#SBATCH -t 00:10:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
SUPERVISOR_SCRIPT=$ROOT/configs/slurm/riker_pf_campaign_supervisor.sbatch
DRIVE_SCRIPT=$ROOT/scripts/drive_campaign.py
PYBIN=${PYBIN:-$ROOT/.venv-riker/bin/python}

CAMPAIGN_ID=${CAMPAIGN_ID:-riker_pf_complement_large_v1}
ROUNDS=${ROUNDS:-5}
COUNT=${COUNT:-100000}
SEED_BASE=${SEED_BASE:-20260925}
ACCOUNT=${ACCOUNT:-lrn070}
QOS=${QOS:-normal}
ROUND_SBATCH=${ROUND_SBATCH:-configs/slurm/riker_pf_prepare.sbatch}
CONFIG=${CONFIG:-configs/pf_campaign_riker.yaml}
ANCHOR_INDEX=${ANCHOR_INDEX:-$ROOT/data/derived/pf_anchors/andes_acopf_anchors.parquet}
RUNS_ROOT=${RUNS_ROOT:-data/outputs/runs/$CAMPAIGN_ID}
ROUND_NODES=${ROUND_NODES:-64}
ROUND_TASKS_PER_NODE=${ROUND_TASKS_PER_NODE:-32}
ROUND_TIME=${ROUND_TIME:-36:00:00}
PREP_TIME=${PREP_TIME:-04:00:00}
SHARD_MULTIPLIER=${SHARD_MULTIPLIER:-8}
ROUND_SHARD_COUNT=${ROUND_SHARD_COUNT:-$((ROUND_NODES * ROUND_TASKS_PER_NODE * SHARD_MULTIPLIER))}
MAX_FAILURE_FRACTION=${MAX_FAILURE_FRACTION:-0.5}
COINHSL_ENV=${COINHSL_ENV:-$HOME/.local/coinhsl/current/env.sh}
DEP_TYPE=${DEP_TYPE:-afterany}
ROUND_SELF_CONTINUE=${ROUND_SELF_CONTINUE:-1}
CYCLE=${CYCLE:-1}
MAX_CYCLES=${MAX_CYCLES:-500}

[[ -x "$PYBIN" ]] || { echo "Missing Python: $PYBIN" >&2; exit 2; }
[[ -s "$ANCHOR_INDEX" ]] || { echo "Missing PF anchor index: $ANCHOR_INDEX" >&2; exit 2; }
(( ROUNDS > 0 && COUNT > 0 && ROUND_NODES > 0 && ROUND_TASKS_PER_NODE > 0 && ROUND_SHARD_COUNT > 0 )) || {
  echo "ROUNDS, COUNT, ROUND_NODES, ROUND_TASKS_PER_NODE, and ROUND_SHARD_COUNT must be positive" >&2
  exit 2
}
if (( CYCLE > MAX_CYCLES )); then
  echo "MAX_CYCLES=$MAX_CYCLES exceeded; stopping without resubmission" >&2
  exit 1
fi

mkdir -p "$ROOT/data/scratch/logs"
PLAN_DIR=$ROOT/data/outputs/campaigns/$CAMPAIGN_ID/round_summaries
mkdir -p "$PLAN_DIR"
PLAN_FILE=$PLAN_DIR/pf_supervisor_cycle_${CYCLE}_plan.json

echo "[$(date -u +%FT%TZ)] PF supervisor cycle=$CYCLE/$MAX_CYCLES campaign=$CAMPAIGN_ID rounds=$ROUNDS"

QOS_ARGS=()
[[ -n "$QOS" ]] && QOS_ARGS=(--qos "$QOS")
if ! PYTHONPATH="$ROOT/src" "$PYBIN" "$DRIVE_SCRIPT" \
  --campaign-id "$CAMPAIGN_ID" \
  --rounds "$ROUNDS" \
  --sbatch "$ROUND_SBATCH" \
  --config "$CONFIG" \
  --reduce-marker-template 'round_{round:03d}_pf_reduce_report.json' \
  --account "$ACCOUNT" \
  --dependency-type "$DEP_TYPE" \
  --emit-plan "$PLAN_FILE" \
  --nodes 1 \
  --ntasks-per-node 1 \
  --cpus-per-task 1 \
  --time "$PREP_TIME" \
  --set "COUNT=$COUNT" \
  --set "SEED_BASE=$SEED_BASE" \
  --set "CONFIG=$CONFIG" \
  --set "ANCHOR_INDEX=$ANCHOR_INDEX" \
  --set "RUNS_ROOT=$RUNS_ROOT" \
  --set "ROUND_NODES=$ROUND_NODES" \
  --set "ROUND_TASKS_PER_NODE=$ROUND_TASKS_PER_NODE" \
  --set "ROUND_TIME=$ROUND_TIME" \
  --set "SHARD_COUNT=$ROUND_SHARD_COUNT" \
  --set "MAX_FAILURE_FRACTION=$MAX_FAILURE_FRACTION" \
  --set "COINHSL_ENV=$COINHSL_ENV" \
  --set "ROUND_SELF_CONTINUE=$ROUND_SELF_CONTINUE" \
  --set "SUPERVISOR_SCRIPT=$SUPERVISOR_SCRIPT" \
  "${QOS_ARGS[@]}"; then
  echo "drive_campaign failed; not resubmitting supervisor" >&2
  exit 1
fi

[[ -s "$PLAN_FILE" ]] || { echo "Missing supervisor plan: $PLAN_FILE" >&2; exit 1; }
STATUS=$($PYBIN -c "import json; print(json.load(open('$PLAN_FILE')).get('status', ''))" 2>/dev/null || true)
if [[ "$STATUS" == "all_rounds_complete" ]]; then
  echo "[$(date -u +%FT%TZ)] all $ROUNDS PF rounds complete; supervisor stopping"
  exit 0
fi
[[ "$STATUS" == "submitted" ]] || { echo "Unexpected plan status: ${STATUS:-empty}" >&2; exit 1; }

ROUND_JOB=$($PYBIN -c "import json; p=json.load(open('$PLAN_FILE')); s=p.get('submitted') or []; print(s[0].get('job_id', '') if s else '')" 2>/dev/null || true)
[[ "$ROUND_JOB" =~ ^[0-9]+$ ]] || { echo "Plan contains no valid round job id" >&2; exit 1; }

if [[ "$ROUND_SELF_CONTINUE" == "1" ]]; then
  echo "[$(date -u +%FT%TZ)] round_job=$ROUND_JOB will schedule cycle=$((CYCLE + 1)) after it starts"
  exit 0
fi

SUPERVISOR_CMD=(sbatch -A "$ACCOUNT")
[[ -n "$QOS" ]] && SUPERVISOR_CMD+=(-q "$QOS")
SUPERVISOR_CMD+=(--dependency="afterany:$ROUND_JOB" --export="ALL,CYCLE=$((CYCLE + 1))" "$SUPERVISOR_SCRIPT")
"${SUPERVISOR_CMD[@]}"
echo "[$(date -u +%FT%TZ)] round_job=$ROUND_JOB next_supervisor=afterany:$ROUND_JOB"