#!/bin/bash
# =============================================================================
# Fan the Frontier ExaGO AC-OPF campaign out into N concurrent, fully-isolated
# per-case-group supervisor chains, so multiple Slurm jobs fill the shared data
# pool in parallel WITHOUT colliding and WITHOUT solving any case twice.
#
# Why two isolation keys are required (verified against the round/reduce paths):
#   * CAMPAIGN_ID  -> keys all campaign bookkeeping (selected candidates, shard
#                     dir, queue, reduce markers, ledgers) via campaign_root().
#   * RUNS_ROOT    -> the per-shard runs-root is RUNS_ROOT/mapreduce_round_NNN/
#                     shard_NNNNN, keyed by round+shard ONLY. Two concurrent jobs
#                     that share RUNS_ROOT collide on the per-shard samples.jsonl
#                     sink even if their CAMPAIGN_IDs differ.
# Give every group its own CAMPAIGN_ID and RUNS_ROOT and the groups are fully
# independent; the final pool is the union of the per-group RUNS_ROOTs.
#
# Cases are partitioned round-robin across GROUPS, so the groups are disjoint.
# Each group is launched through scripts/submit_exago_frontier_campaign.sh, so it
# gets the same hardened, self-resubmitting supervisor (fail-safe requeue,
# partition-default QOS) as a single-campaign run.
#
# Usage:
#   scripts/submit_exago_frontier_parallel_campaigns.sh                 # defaults
#   NUM_GROUPS=6 scripts/submit_exago_frontier_parallel_campaigns.sh     # 6 chains
#   DRY_RUN=1 scripts/submit_exago_frontier_parallel_campaigns.sh        # preview
#   CASES="caseA caseB caseC" NUM_GROUPS=3 scripts/submit_..._campaigns.sh # custom set
#
# NOTE: N groups keep ~2N jobs queued at once (a round job + its supervisor per
# group). Keep NUM_GROUPS within the account's per-user submit/run limits, and do
# not use QOS=debug for the chain (its per-user submit cap is too small).
# NUM_GROUPS is used instead of GROUPS because GROUPS is a reserved bash builtin.
# =============================================================================

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
ROUND_SBATCH_REL=${ROUND_SBATCH:-configs/slurm/frontier_exago_acopf_mapreduce_8n_2h.sbatch}
LAUNCHER="$ROOT/scripts/submit_exago_frontier_campaign.sh"

NUM_GROUPS=${NUM_GROUPS:-4}
BASE_CAMPAIGN_ID=${BASE_CAMPAIGN_ID:-exago_frontier_large_grids_par}
ACCOUNT=${ACCOUNT:-LRN087}
# Partition-default QOS by default (the chain exceeds the debug submit cap).
QOS=${QOS-}
ROUNDS=${ROUNDS:-5}
BUDGET=${BUDGET:-600}
DRY_RUN=${DRY_RUN:-0}

if [[ ! -x "$LAUNCHER" && ! -f "$LAUNCHER" ]]; then
  echo "launcher not found: $LAUNCHER" >&2
  exit 1
fi

# Single source of truth for the case list: read LARGE_CASES_DEFAULT straight
# from the round sbatch (no eval of the sbatch itself) unless CASES is provided.
if [[ -z "${CASES:-}" ]]; then
  CASES=$(grep -E '^LARGE_CASES_DEFAULT=' "$ROOT/$ROUND_SBATCH_REL" \
            | sed -E 's/^LARGE_CASES_DEFAULT=//' | tr -d '"')
fi
read -r -a CASE_ARR <<< "$CASES"
if (( ${#CASE_ARR[@]} == 0 )); then
  echo "no cases to distribute" >&2
  exit 1
fi
if (( NUM_GROUPS > ${#CASE_ARR[@]} )); then
  NUM_GROUPS=${#CASE_ARR[@]}
fi

# Round-robin partition so group sizes differ by at most one.
declare -a GROUP_CASES
for ((g=0; g<NUM_GROUPS; g++)); do GROUP_CASES[$g]=""; done
for ((i=0; i<${#CASE_ARR[@]}; i++)); do
  g=$(( i % NUM_GROUPS ))
  GROUP_CASES[$g]="${GROUP_CASES[$g]} ${CASE_ARR[$i]}"
done

echo "fan-out groups=$NUM_GROUPS base_campaign=$BASE_CAMPAIGN_ID account=$ACCOUNT qos=${QOS:-default} rounds=$ROUNDS total_cases=${#CASE_ARR[@]} dry_run=$DRY_RUN"

for ((g=0; g<NUM_GROUPS; g++)); do
  gid=$(printf "%02d" "$g")
  campaign_id="${BASE_CAMPAIGN_ID}_g${gid}"
  runs_root="data/outputs/runs/${campaign_id}"
  group_cases=$(echo "${GROUP_CASES[$g]}" | xargs)  # trim leading space
  echo "--- group $gid campaign_id=$campaign_id runs_root=$runs_root"
  echo "    cases: $group_cases"
  # CASES + RUNS_ROOT ride --export=ALL through the supervisor to each round job.
  CAMPAIGN_ID="$campaign_id" \
  CASES="$group_cases" \
  RUNS_ROOT="$runs_root" \
  ACCOUNT="$ACCOUNT" \
  QOS="$QOS" \
  ROUNDS="$ROUNDS" \
  BUDGET="$BUDGET" \
  DRY_RUN="$DRY_RUN" \
    bash "$LAUNCHER"
done
