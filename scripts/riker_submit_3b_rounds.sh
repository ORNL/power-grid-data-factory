#!/bin/bash
# Submit rolling AUTO_ROUND solve batches for ultrascale_3b, with archive-sweep
# gates between batches to collapse Lustre inodes before the next solve round.
#
# Usage:
#   bash scripts/riker_submit_3b_rounds.sh [N_SLOTS] [FIRST_DEP] [N_BATCHES]
#
#   N_SLOTS   - solve slots per batch (default 20)
#   FIRST_DEP - Slurm job ID to depend on for the first batch, or "none"
#   N_BATCHES - how many solve+sweep batches to submit (default 3)
#
# The final sweep self-extends by one batch while rounds remain (AUTO_EXTEND=0
# to disable). Example — extend the chain after the current tail job:
#   bash scripts/riker_submit_3b_rounds.sh 20 <tail_job_id> 3
set -euo pipefail

ROOT=/lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory
SOLVE_SBATCH=$ROOT/configs/slurm/riker_3b_round.sbatch
SWEEP_SBATCH=$ROOT/configs/slurm/riker_archive_sweep_3b.sbatch
CASES_FILE=$ROOT/configs/slurm/ultrascale_cases.txt

CAMPAIGN_ID=ultrascale_3b
TOTAL_ROUNDS=150
BUDGET=20000000
PER_CASE=43000
SEED_BASE=700
TIMEOUT_S=1800
RUNS_ROOT=data/outputs/runs_3b
ARCHIVE_DIR=data/outputs/archives_3b
CONFIG=configs/campaign_ultrascale_3b.yaml
EXECUTION_POLICY=configs/campaign_execution_tiers_3b.yaml
SOLVER_ID=powermodels_ac_opf_ipopt_campaign

N_SLOTS=${1:-20}        # 36h solve slots per batch
FIRST_DEP=${2:-none}    # chain tail job id, or "none" to start immediately
N_BATCHES=${3:-3}       # number of solve+sweep batches

unset PYTHONUSERBASE PYTHONNOUSERSITE

CASES=$(grep -v '^#' "$CASES_FILE" | tr '\n' ' ')
# Ipopt linear-solver escalation per candidate: MUMPS (default) -> MA27 -> MA57.
LINEAR_FALLBACKS=ma27,ma57
# Tail sweep re-submits another batch while rounds remain and progress is made.
AUTO_EXTEND=${AUTO_EXTEND:-1}
N_OK_NOW=$(grep -l '"ok": true' "$ROOT/data/outputs/campaigns/$CAMPAIGN_ID"/round_summaries/round_*_mapreduce_reduce_report.json 2>/dev/null | wc -l)
echo "[submit_3b_rounds] cases=$(echo $CASES | wc -w) grids, slots_per_batch=$N_SLOTS, batches=$N_BATCHES, first_dep=$FIRST_DEP"

PREV=$FIRST_DEP
for b in $(seq 1 "$N_BATCHES"); do
    echo "[submit_3b_rounds] batch $b: $N_SLOTS solve slots starting from dep=$PREV"
    for i in $(seq -w 1 "$N_SLOTS"); do
        DEP_ARGS=()
        [[ "$PREV" != "none" ]] && DEP_ARGS=(--dependency="afterany:${PREV}")
        JID=$(sbatch \
            --job-name="pgdf_mr_3b_b${b}s${i}" \
            "${DEP_ARGS[@]}" \
            --export="ALL,CAMPAIGN_ID=${CAMPAIGN_ID},AUTO_ROUND=1,TOTAL_ROUNDS=${TOTAL_ROUNDS},CONFIG=${CONFIG},CASES=${CASES},BUDGET=${BUDGET},PER_CASE=${PER_CASE},SEED_BASE=${SEED_BASE},AUDIT_FRACTION=1.0,SOLVER_ID=${SOLVER_ID},TIMEOUT_S=${TIMEOUT_S},EXECUTION_POLICY=${EXECUTION_POLICY},RUNS_ROOT=${RUNS_ROOT},POWER_MODELS_LINEAR_SOLVER_FALLBACKS=${LINEAR_FALLBACKS}" \
            "$SOLVE_SBATCH" \
            | awk '{print $NF}')
        echo "  solve b${b}s${i}: $JID (dep $PREV)"
        PREV=$JID
    done

    # Archive sweep after each solve batch — collapses inodes before next batch.
    # Only the final sweep of this submission carries AUTO_EXTEND.
    EXT=0; [[ "$b" -eq "$N_BATCHES" ]] && EXT=$AUTO_EXTEND
    SWEEP_JID=$(sbatch \
        --job-name="pgdf_sweep_3b_b${b}" \
        --dependency="afterany:${PREV}" \
        --export="ALL,CAMPAIGN_ID=${CAMPAIGN_ID},TOTAL_ROUNDS=${TOTAL_ROUNDS},RUNS_ROOT=${RUNS_ROOT},ARCHIVE_DIR=${ARCHIVE_DIR},AUTO_EXTEND=${EXT},EXTEND_SLOTS=${N_SLOTS},EXTEND_BASELINE=${N_OK_NOW}" \
        "$SWEEP_SBATCH" \
        | awk '{print $NF}')
    echo "  sweep b${b}: $SWEEP_JID (dep $PREV)"
    PREV=$SWEEP_JID
done

echo "[submit_3b_rounds] chain tail: $PREV"
