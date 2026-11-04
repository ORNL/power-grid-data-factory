#!/bin/bash
#SBATCH -J pgdf_activsg_sup
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
SUPERVISOR_SCRIPT=$ROOT/configs/slurm/riker_activsg_hsl_full_supervisor.sbatch
RUNNER_SCRIPT=$ROOT/configs/slurm/riker_3b_round.sbatch
PYBIN=$ROOT/.venv-riker/bin/python
CAMPAIGN_ID=activsg_hsl_full_3b
ROUND_INDEX=0
CONFIG=configs/campaign_ultrascale_3b.yaml
RUNS_ROOT=data/outputs/runs_3b_activsg_hsl_full
MAX_CYCLES=${MAX_CYCLES:-500}
CYCLE=${CYCLE:-1}

export CAMPAIGN_ID ROUND_INDEX CONFIG RUNS_ROOT
export RESUME=1 RUN_BOOTSTRAP=0 BUDGET=433396 PER_CASE=217886 SEED=700
export SOLVER_ID=powermodels_ac_opf_ipopt_campaign TIMEOUT_S=36000 SHARD_COUNT=8192
export AUDIT_FRACTION=1.0 POWER_MODELS_LINEAR_SOLVERS=ma27,ma57
export EXECUTION_POLICY=configs/execution_policy_andes_exago_large_cases.yaml

ROUND_PAD=000
CAMPAIGN_ROOT=$ROOT/data/outputs/campaigns/$CAMPAIGN_ID
REPORT=$CAMPAIGN_ROOT/round_summaries/round_${ROUND_PAD}_mapreduce_reduce_report.json
SELECTED=$CAMPAIGN_ROOT/round_summaries/round_${ROUND_PAD}_selected_candidates.jsonl
SHARD_DIR=$CAMPAIGN_ROOT/round_summaries/round_${ROUND_PAD}_shards

if (( CYCLE > MAX_CYCLES )); then
  echo "MAX_CYCLES=$MAX_CYCLES exceeded; stopping without resubmission." >&2
  exit 1
fi

if [[ -s "$REPORT" ]] && "$PYBIN" -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("ok") is True else 1)' "$REPORT"; then
  echo "[$(date -u +%FT%TZ)] ActiveSG round $ROUND_INDEX already reduced ok; supervisor stopping."
  exit 0
fi

[[ -s "$SELECTED" ]] || { echo "Missing saved ActiveSG selection: $SELECTED" >&2; exit 2; }
shard_count=$(find "$SHARD_DIR" -maxdepth 1 -type f -name 'selected_*.jsonl' | wc -l)
(( shard_count == SHARD_COUNT )) || {
  echo "Expected $SHARD_COUNT saved shards under $SHARD_DIR; found $shard_count" >&2
  exit 2
}

mkdir -p "$ROOT/data/scratch/logs"
echo "[$(date -u +%FT%TZ)] ActiveSG PowerModels resume cycle=$CYCLE/$MAX_CYCLES shards=$shard_count"
ROUND_JOB=$(sbatch --parsable --export=ALL "$RUNNER_SCRIPT")
ROUND_JOB=${ROUND_JOB%%;*}
[[ "$ROUND_JOB" =~ ^[0-9]+$ ]] || { echo "Invalid ActiveSG resume job id: $ROUND_JOB" >&2; exit 1; }

if (( CYCLE < MAX_CYCLES )); then
  NEXT_JOB=$(sbatch --parsable \
    --dependency="afterany:$ROUND_JOB" \
    --export="ALL,CYCLE=$((CYCLE + 1)),MAX_CYCLES=$MAX_CYCLES" \
    "$SUPERVISOR_SCRIPT")
  echo "[$(date -u +%FT%TZ)] submitted ActiveSG resume job=$ROUND_JOB next_supervisor=${NEXT_JOB%%;*}"
else
  echo "[$(date -u +%FT%TZ)] submitted ActiveSG resume job=$ROUND_JOB; cycle limit reached, no continuation queued"
fi