#!/bin/bash
#SBATCH -J pgdf_campaign_round
#SBATCH -A LRN070
#SBATCH -p batch
#SBATCH -N 1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=4
#SBATCH -t 36:00:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
BOOTSTRAP_SCRIPT=$ROOT/scripts/bootstrap_adaptive_campaign.py
EXEC_SCRIPT=$ROOT/scripts/run_campaign_ac_opf_round.py

module load julia/1.8.2 2>/dev/null || true

# Campaign-driven scheduler parameters.
CAMPAIGN_ID=${CAMPAIGN_ID:-pilot_pglib_14_57_118}
ROUND_INDEX=${ROUND_INDEX:-0}
CONFIG=${CONFIG:-configs/campaign_default.yaml}
CASES=${CASES:-"pglib_opf_case14_ieee pglib_opf_case57_ieee pglib_opf_case118_ieee"}
PER_CASE=${PER_CASE:-500}
SAMPLER=${SAMPLER:-latin_hypercube}
BUDGET=${BUDGET:-600}
SEED=${SEED:-7}
SOLVER_ID=${SOLVER_ID:-powermodels_ac_opf_ipopt_campaign}
TIMEOUT_S=${TIMEOUT_S:-1200}
RUNS_ROOT=${RUNS_ROOT:-data/outputs/runs}
MAX_CANDIDATES=${MAX_CANDIDATES:-0}
RUN_BOOTSTRAP=${RUN_BOOTSTRAP:-1}
CONTINUE_ON_ERROR=${CONTINUE_ON_ERROR:-1}
MAX_FAILURE_FRACTION=${MAX_FAILURE_FRACTION:-0.5}
TOPOLOGIES_PER_CASE=${TOPOLOGIES_PER_CASE:-6}
MAX_SWITCHED_BRANCHES=${MAX_SWITCHED_BRANCHES:-3}

printf -v ROUND_PAD "%03d" "$ROUND_INDEX"
CAMPAIGN_ROOT="$ROOT/data/outputs/campaigns/$CAMPAIGN_ID"
SELECTED_JSONL="$CAMPAIGN_ROOT/round_summaries/round_${ROUND_PAD}_selected_candidates.jsonl"
REPORT_JSON="$CAMPAIGN_ROOT/round_summaries/round_${ROUND_PAD}_ac_execution_report.json"

mkdir -p "$ROOT/data/scratch/logs"

echo "[$(date -u +%FT%TZ)] JOB=$SLURM_JOB_ID campaign_id=$CAMPAIGN_ID round_index=$ROUND_INDEX"
echo "[$(date -u +%FT%TZ)] config=$CONFIG budget=$BUDGET per_case=$PER_CASE seed=$SEED"
echo "[$(date -u +%FT%TZ)] runs_root=$RUNS_ROOT solver_id=$SOLVER_ID timeout_s=$TIMEOUT_S"

if [[ "$RUN_BOOTSTRAP" == "1" ]]; then
  read -r -a CASE_ARRAY <<< "$CASES"
  echo "[$(date -u +%FT%TZ)] Bootstrapping campaign candidates and round selection"
  PYTHONPATH="$ROOT/src" python3.11 "$BOOTSTRAP_SCRIPT" \
    --campaign-id "$CAMPAIGN_ID" \
    --config "$CONFIG" \
    --cases "${CASE_ARRAY[@]}" \
    --per-case "$PER_CASE" \
    --sampler "$SAMPLER" \
    --topologies-per-case "$TOPOLOGIES_PER_CASE" \
    --max-switched-branches "$MAX_SWITCHED_BRANCHES" \
    --budget "$BUDGET" \
    --round-index "$ROUND_INDEX" \
    --seed "$SEED"
else
  echo "[$(date -u +%FT%TZ)] Skipping bootstrap (RUN_BOOTSTRAP=$RUN_BOOTSTRAP)"
fi

if [[ ! -s "$SELECTED_JSONL" ]]; then
  echo "Selected candidate file not found or empty: $SELECTED_JSONL" >&2
  echo "Set RUN_BOOTSTRAP=1 or provide a precomputed selected-candidates file for this campaign round." >&2
  exit 2
fi

EXEC_ARGS=(
  --campaign-id "$CAMPAIGN_ID"
  --round-index "$ROUND_INDEX"
  --selected-candidates-jsonl "$SELECTED_JSONL"
  --config "$CONFIG"
  --runs-root "$RUNS_ROOT"
  --solver-id "$SOLVER_ID"
  --timeout-s "$TIMEOUT_S"
  --max-failure-fraction "$MAX_FAILURE_FRACTION"
)

if [[ "$CONTINUE_ON_ERROR" == "1" ]]; then
  EXEC_ARGS+=(--continue-on-error)
fi

if [[ "$MAX_CANDIDATES" != "0" ]]; then
  EXEC_ARGS+=(--max-candidates "$MAX_CANDIDATES")
fi

echo "[$(date -u +%FT%TZ)] Running AC execution from selected candidates: $SELECTED_JSONL"
PYTHONPATH="$ROOT/src" python3.11 "$EXEC_SCRIPT" "${EXEC_ARGS[@]}"

if [[ -f "$REPORT_JSON" ]]; then
  echo "[$(date -u +%FT%TZ)] AC execution report: $REPORT_JSON"
fi

echo "[$(date -u +%FT%TZ)] Completed campaign round job $SLURM_JOB_ID"
