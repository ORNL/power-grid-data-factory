#!/bin/bash
#SBATCH -J pgdf_scopf_sup
#SBATCH -A LRN116
#SBATCH -p batch
#SBATCH --qos=normal
#SBATCH -N 1
#SBATCH -n 1
#SBATCH --cpus-per-task=1
#SBATCH -t 00:05:00
#SBATCH -o /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

set -euo pipefail

ROOT=/lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory
SUPERVISOR_SCRIPT=$ROOT/configs/slurm/riker_scopf_campaign_supervisor.sbatch
RUNNER_SCRIPT=$ROOT/configs/slurm/riker_scopf_campaign.sbatch
PYBIN=$ROOT/.venv-riker/bin/python
CONFIG=configs/scopf_campaign_riker.yaml
WORKERS=${WORKERS:-8}
CYCLE=${CYCLE:-1}
MAX_CYCLES=${MAX_CYCLES:-500}

if (( CYCLE > MAX_CYCLES )); then
  echo "MAX_CYCLES=$MAX_CYCLES exceeded; stopping without resubmission." >&2
  exit 1
fi

mkdir -p "$ROOT/data/scratch/logs"
unset PYTHONUSERBASE PYTHONNOUSERSITE
export PYTHONPATH="$ROOT/src"
"$PYBIN" "$ROOT/scripts/scopf_campaign.py" prepare --config "$CONFIG"
"$PYBIN" "$ROOT/scripts/scopf_campaign.py" clear-stale-claims --config "$CONFIG"
STATUS_JSON=$("$PYBIN" "$ROOT/scripts/scopf_campaign.py" status --config "$CONFIG")
printf '%s\n' "$STATUS_JSON"
PENDING=$(printf '%s\n' "$STATUS_JSON" | "$PYBIN" -c 'import json,sys; print(int(json.load(sys.stdin)["pending"]))')

if (( PENDING == 0 )); then
  echo "[$(date -Is)] SCOPF campaign complete; supervisor stopping."
  exit 0
fi

ROUND_JOB=$(sbatch --parsable \
  --export="ALL,WORKERS=$WORKERS,CLEAR_STALE_CLAIMS=0" \
  "$RUNNER_SCRIPT")
ROUND_JOB=${ROUND_JOB%%;*}
[[ "$ROUND_JOB" =~ ^[0-9]+$ ]] || { echo "Invalid SCOPF job id: $ROUND_JOB" >&2; exit 1; }

if (( CYCLE < MAX_CYCLES )); then
  NEXT_JOB=$(sbatch --parsable \
    --dependency="afterany:$ROUND_JOB" \
    --export="ALL,CYCLE=$((CYCLE + 1)),MAX_CYCLES=$MAX_CYCLES,WORKERS=$WORKERS" \
    "$SUPERVISOR_SCRIPT")
  echo "[$(date -Is)] pending=$PENDING; submitted SCOPF job=$ROUND_JOB next_supervisor=${NEXT_JOB%%;*}"
else
  echo "[$(date -Is)] submitted SCOPF job=$ROUND_JOB; cycle limit reached, no continuation queued"
fi