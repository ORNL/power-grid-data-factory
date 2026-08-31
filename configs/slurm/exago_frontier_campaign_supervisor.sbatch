#!/bin/bash
#SBATCH -J pgdf_exago_sup
#SBATCH -A LRN070
#SBATCH -p batch
#SBATCH -N 1
#SBATCH -t 00:20:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

# =============================================================================
# Self-resubmitting supervisor for the Frontier ExaGO AC-OPF campaign.
#
# Each cycle:
#   1. Ask drive_campaign.py for the furthest INCOMPLETE round and submit it
#      with RESUME=1 (skips finished shards + already-solved candidates).
#   2. If a round job was submitted, resubmit THIS supervisor with an
#      `afterany` dependency on that round job, so the next cycle only runs
#      once the round job ends (completed OR timed out).
#   3. If every round is complete, stop (no resubmission).
#
# This makes back-to-back jobs resume the SAME round until its reduce marker is
# ``ok`` (all shards reported), then advance to the next round -- unlike a fixed
# ``--chain`` that would step past an unfinished round after a 2h timeout.
#
# Launch with scripts/submit_exago_frontier_campaign.sh (sets ACCOUNT, ROUNDS,
# and seeds the first cycle `afterany` any currently-running round job so it
# does not collide on the shared campaign paths).
# =============================================================================

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
SUPERVISOR_SCRIPT="$ROOT/configs/slurm/exago_frontier_campaign_supervisor.sbatch"
DRIVE_SCRIPT="$ROOT/scripts/drive_campaign.py"
PYBIN=${PYBIN:-python3.11}

# ----- campaign parameters (overridable via the launcher's --export list) -----
CAMPAIGN_ID=${CAMPAIGN_ID:-exago_frontier_large_grids}
ROUNDS=${ROUNDS:-5}
ACCOUNT=${ACCOUNT:-LRN087}
# Slurm QOS. Default is the partition default (batch/normal): the supervisor keeps
# a round job AND the next supervisor cycle queued at once, which exceeds the
# 'debug' QOS per-user submit limit (QOSMaxSubmitJobPerUserLimit). Opt into debug
# only for a single short run, not for the self-resubmitting chain.
QOS=${QOS-}
ROUND_SBATCH=${ROUND_SBATCH:-configs/slurm/frontier_exago_acopf_mapreduce_8n_2h.sbatch}
CONFIG=${CONFIG:-configs/campaign_default.yaml}
BUDGET=${BUDGET:-600}
DEP_TYPE=${DEP_TYPE:-afterany}
# Optional round-job resource overrides forwarded to drive_campaign (0/empty = keep header).
ROUND_NODES=${ROUND_NODES:-0}
ROUND_TIME=${ROUND_TIME:-}
# Optional extra KEY=VALUE env overrides forwarded to each round job (space-separated).
ROUND_EXTRA_ENV=${ROUND_EXTRA_ENV:-}
# Runaway guard: stop after this many supervisor cycles.
CYCLE=${CYCLE:-1}
MAX_CYCLES=${MAX_CYCLES:-500}

mkdir -p "$ROOT/data/scratch/logs"
PLAN_DIR="$ROOT/data/outputs/campaigns/$CAMPAIGN_ID/round_summaries"
mkdir -p "$PLAN_DIR"
PLAN_FILE="$PLAN_DIR/supervisor_cycle_${CYCLE}_plan.json"

echo "[$(date -u +%FT%TZ)] supervisor cycle=$CYCLE/$MAX_CYCLES job=$SLURM_JOB_ID campaign=$CAMPAIGN_ID rounds=$ROUNDS account=$ACCOUNT qos=${QOS:-default}"

if (( CYCLE > MAX_CYCLES )); then
  echo "[$(date -u +%FT%TZ)] MAX_CYCLES=$MAX_CYCLES exceeded; stopping without resubmission." >&2
  exit 1
fi

# Build drive_campaign resource-override flags.
DRIVE_RES=()
if [[ "$ROUND_NODES" != "0" && -n "$ROUND_NODES" ]]; then
  DRIVE_RES+=(--nodes "$ROUND_NODES")
fi
if [[ -n "$ROUND_TIME" ]]; then
  DRIVE_RES+=(--time "$ROUND_TIME")
fi
DRIVE_EXTRA=()
for kv in $ROUND_EXTRA_ENV; do
  DRIVE_EXTRA+=(--set "$kv")
done

DRIVE_QOS=()
if [[ -n "${QOS:-}" ]]; then
  DRIVE_QOS+=(--qos "$QOS")
fi

# 1. Submit the furthest incomplete round (RESUME=1) and capture the plan.
if ! PYTHONPATH="$ROOT/src" "$PYBIN" "$DRIVE_SCRIPT" \
  --campaign-id "$CAMPAIGN_ID" \
  --rounds "$ROUNDS" \
  --sbatch "$ROUND_SBATCH" \
  --config "$CONFIG" \
  --budget "$BUDGET" \
  --account "$ACCOUNT" \
  --dependency-type "$DEP_TYPE" \
  --emit-plan "$PLAN_FILE" \
  "${DRIVE_QOS[@]}" \
  "${DRIVE_RES[@]}" \
  "${DRIVE_EXTRA[@]}"; then
  echo "[$(date -u +%FT%TZ)] drive_campaign failed; not resubmitting supervisor." >&2
  exit 1
fi

if [[ ! -s "$PLAN_FILE" ]]; then
  echo "[$(date -u +%FT%TZ)] drive_campaign produced no plan file ($PLAN_FILE); stopping without requeue." >&2
  exit 1
fi

STATUS=$("$PYBIN" -c "import json,sys; p=json.load(open('$PLAN_FILE')); print(str(p.get('status','')).strip())" 2>/dev/null || true)
if [[ -z "$STATUS" ]]; then
  echo "[$(date -u +%FT%TZ)] invalid/empty plan JSON in $PLAN_FILE; stopping without requeue." >&2
  exit 1
fi

if [[ "$STATUS" == "all_rounds_complete" ]]; then
  echo "[$(date -u +%FT%TZ)] all $ROUNDS rounds complete for $CAMPAIGN_ID; supervisor stopping."
  exit 0
fi

if [[ "$STATUS" != "submitted" ]]; then
  echo "[$(date -u +%FT%TZ)] plan status was '$STATUS' instead of 'submitted'; stopping without requeue." >&2
  exit 1
fi

ROUND_JOB=$("$PYBIN" -c "import json; p=json.load(open('$PLAN_FILE')); s=p.get('submitted') or []; print((s[0].get('job_id','')) if s else '')" 2>/dev/null || true)
if [[ -z "$ROUND_JOB" || ! "$ROUND_JOB" =~ ^[0-9]+$ ]]; then
  echo "[$(date -u +%FT%TZ)] no valid round job id in plan; stopping without requeue." >&2
  exit 1
fi

echo "[$(date -u +%FT%TZ)] submitted round job $ROUND_JOB; resubmitting supervisor afterany:$ROUND_JOB (cycle $((CYCLE+1)))"

# 2. Resubmit this supervisor to run once the round job ends (completed or timed out).
if ! sbatch -A "$ACCOUNT" \
  ${QOS:+-q "$QOS"} \
  --dependency="afterany:$ROUND_JOB" \
  --export=ALL,CYCLE=$((CYCLE + 1)) \
  "$SUPERVISOR_SCRIPT"; then
  echo "[$(date -u +%FT%TZ)] failed to resubmit supervisor after round job $ROUND_JOB." >&2
  exit 1
fi

echo "[$(date -u +%FT%TZ)] supervisor cycle=$CYCLE done"
