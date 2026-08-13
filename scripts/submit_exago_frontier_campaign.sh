#!/bin/bash
# =============================================================================
# Launch the self-resubmitting Frontier ExaGO AC-OPF campaign supervisor.
#
# The supervisor (configs/slurm/exago_frontier_campaign_supervisor.sbatch) runs
# drive_campaign.py each cycle: it submits the furthest INCOMPLETE round with
# RESUME=1, then re-queues itself `afterany` that round job. It resumes the same
# round across as many 2h windows as needed, advances when a round's reduce
# marker is `ok`, and stops when all rounds are complete.
#
# Usage:
#   scripts/submit_exago_frontier_campaign.sh                 # defaults
#   ROUNDS=8 scripts/submit_exago_frontier_campaign.sh        # 8 rounds
#   AFTER=5248977 scripts/submit_exago_frontier_campaign.sh   # start after a running job
#   DRY_RUN=1 scripts/submit_exago_frontier_campaign.sh       # print, do not submit
#
# All parameters are environment overrides (shown with defaults below).
# =============================================================================

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
SUPERVISOR="$ROOT/configs/slurm/exago_frontier_campaign_supervisor.sbatch"

# ----- campaign parameters -----
export CAMPAIGN_ID=${CAMPAIGN_ID:-exago_frontier_large_grids}
export ROUNDS=${ROUNDS:-5}
export ACCOUNT=${ACCOUNT:-LRN087}
# Slurm QOS. Default is the partition default (batch/normal). The self-resubmitting
# chain keeps a round job AND the next supervisor cycle queued simultaneously,
# which exceeds the 'debug' QOS per-user submit limit (QOSMaxSubmitJobPerUserLimit).
# Set QOS=debug only for a single short, standalone run.
export QOS=${QOS-}
export ROUND_SBATCH=${ROUND_SBATCH:-configs/slurm/frontier_exago_acopf_mapreduce_8n_2h.sbatch}
export CONFIG=${CONFIG:-configs/campaign_default.yaml}
export BUDGET=${BUDGET:-600}
export DEP_TYPE=${DEP_TYPE:-afterany}
export MAX_CYCLES=${MAX_CYCLES:-50}
# Optional round-job resource overrides (0/empty keeps the round sbatch header).
export ROUND_NODES=${ROUND_NODES:-0}
export ROUND_TIME=${ROUND_TIME:-}
# Optional extra KEY=VALUE env forwarded to each round job (space-separated).
export ROUND_EXTRA_ENV=${ROUND_EXTRA_ENV:-}

# Seed job id: first supervisor cycle waits `afterany` on this (avoids colliding
# with an already-running round job on the shared campaign paths). Empty = start now.
AFTER=${AFTER:-}
DRY_RUN=${DRY_RUN:-0}

if [[ ! -f "$SUPERVISOR" ]]; then
  echo "supervisor sbatch not found: $SUPERVISOR" >&2
  exit 1
fi

CMD=(sbatch -A "$ACCOUNT")
if [[ -n "$QOS" ]]; then
  CMD+=(-q "$QOS")
fi
if [[ -n "$AFTER" ]]; then
  CMD+=(--dependency="afterany:$AFTER")
fi
CMD+=(--export=ALL,CYCLE=1 "$SUPERVISOR")

echo "campaign_id=$CAMPAIGN_ID rounds=$ROUNDS account=$ACCOUNT qos=${QOS:-default} round_sbatch=$ROUND_SBATCH budget=$BUDGET"
echo "dep_type=$DEP_TYPE max_cycles=$MAX_CYCLES after=${AFTER:-none} round_nodes=$ROUND_NODES round_time=${ROUND_TIME:-header} round_extra_env=${ROUND_EXTRA_ENV:-none}"
echo "cmd: ${CMD[*]}"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "[dry-run] not submitting."
  exit 0
fi

cd "$ROOT"
"${CMD[@]}"
