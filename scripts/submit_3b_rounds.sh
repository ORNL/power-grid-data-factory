#!/bin/bash
# Submit rolling AUTO_ROUND solve batches for ultrascale_3b, with archive-sweep
# gates between batches to collapse Lustre inodes before the next solve round.
#
# Usage:
#   bash scripts/submit_3b_rounds.sh [N_SLOTS] [FIRST_DEP] [N_BATCHES]
#
#   N_SLOTS   - solve slots per batch (default 20)
#   FIRST_DEP - Slurm job ID to depend on for the first batch (default 3434070)
#   N_BATCHES - how many solve+sweep batches to submit (default 3)
#
# Example — extend the chain after the current tail job:
#   bash scripts/submit_3b_rounds.sh 20 <tail_job_id> 3
set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
SOLVE_SBATCH=$ROOT/configs/slurm/andes_powermodels_acopf_mapreduce_10n_36h.sbatch
SWEEP_SBATCH=$ROOT/configs/slurm/andes_archive_sweep_3b.sbatch
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
FIRST_DEP=${2:-3434070} # depend on current chain tail (slot 20)
N_BATCHES=${3:-3}       # number of solve+sweep batches

CASES=$(grep -v '^#' "$CASES_FILE" | tr '\n' ' ')
echo "[submit_3b_rounds] cases=$(echo $CASES | wc -w) grids, slots_per_batch=$N_SLOTS, batches=$N_BATCHES, first_dep=$FIRST_DEP"

PREV=$FIRST_DEP
for b in $(seq 1 "$N_BATCHES"); do
    echo "[submit_3b_rounds] batch $b: $N_SLOTS solve slots starting from dep=$PREV"
    for i in $(seq -w 1 "$N_SLOTS"); do
        JID=$(sbatch \
            --job-name="pgdf_mr_3b_b${b}s${i}" \
            --dependency="afterany:${PREV}" \
            --export="ALL,CAMPAIGN_ID=${CAMPAIGN_ID},AUTO_ROUND=1,TOTAL_ROUNDS=${TOTAL_ROUNDS},CONFIG=${CONFIG},CASES=${CASES},BUDGET=${BUDGET},PER_CASE=${PER_CASE},SEED_BASE=${SEED_BASE},AUDIT_FRACTION=1.0,SOLVER_ID=${SOLVER_ID},TIMEOUT_S=${TIMEOUT_S},EXECUTION_POLICY=${EXECUTION_POLICY},RUNS_ROOT=${RUNS_ROOT}" \
            "$SOLVE_SBATCH" \
            | awk '{print $NF}')
        echo "  solve b${b}s${i}: $JID (dep $PREV)"
        PREV=$JID
    done

    # Archive sweep after each solve batch — collapses inodes before next batch.
    SWEEP_JID=$(sbatch \
        --job-name="pgdf_sweep_3b_b${b}" \
        --dependency="afterany:${PREV}" \
        --export="ALL,CAMPAIGN_ID=${CAMPAIGN_ID},TOTAL_ROUNDS=${TOTAL_ROUNDS},RUNS_ROOT=${RUNS_ROOT},ARCHIVE_DIR=${ARCHIVE_DIR}" \
        "$SWEEP_SBATCH" \
        | awk '{print $NF}')
    echo "  sweep b${b}: $SWEEP_JID (dep $PREV)"
    PREV=$SWEEP_JID
done

echo "[submit_3b_rounds] chain tail: $PREV"
