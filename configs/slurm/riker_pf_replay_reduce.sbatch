#!/bin/bash
#SBATCH -J pgdf_pf_replay_reduce
#SBATCH -A LRN116
#SBATCH -p batch
#SBATCH --qos=normal
#SBATCH -N 1
#SBATCH -n 1
#SBATCH --cpus-per-task=1
#SBATCH -t 12:00:00
#SBATCH -o /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

set -euo pipefail
ulimit -c 0

ROOT=/lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory
PYBIN=${PYBIN:-$ROOT/.venv-riker/bin/python}
CAMPAIGN_ID=${CAMPAIGN_ID:?Set original CAMPAIGN_ID}
REPLAY_CAMPAIGN_ID=${REPLAY_CAMPAIGN_ID:?Set REPLAY_CAMPAIGN_ID}
PRIOR_REPLAY_CAMPAIGN_ID=${PRIOR_REPLAY_CAMPAIGN_ID:-}
ROUND_INDEX=${ROUND_INDEX:-3}
CONFIG=${CONFIG:-configs/pf_campaign_riker_severity_25.yaml}
RUNS_ROOT=${RUNS_ROOT:-data/outputs/runs/$CAMPAIGN_ID}
REPLAY_RUNS_ROOT=${REPLAY_RUNS_ROOT:-data/outputs/runs/$REPLAY_CAMPAIGN_ID}

printf -v ROUND_PAD "%03d" "$ROUND_INDEX"
SHARD_IDS_FILE="$ROOT/data/outputs/campaigns/$REPLAY_CAMPAIGN_ID/round_summaries/round_${ROUND_PAD}_pf/merged_shard_campaign_ids.txt"
[[ -x "$PYBIN" && -s "$SHARD_IDS_FILE" ]] || { echo "Missing Python or merged shard IDs: $SHARD_IDS_FILE" >&2; exit 2; }

cd "$ROOT"
REDUCE_ARGS=(
  --campaign-id "$CAMPAIGN_ID" --round-index "$ROUND_INDEX" --config "$ROOT/$CONFIG"
  --shard-campaign-ids-file "$SHARD_IDS_FILE" --runs-root "$ROOT/$RUNS_ROOT"
  --additional-runs-root "$ROOT/$REPLAY_RUNS_ROOT" --force
)
if [[ -n "$PRIOR_REPLAY_CAMPAIGN_ID" ]]; then
  REDUCE_ARGS+=(--additional-runs-root "$ROOT/data/outputs/runs/$PRIOR_REPLAY_CAMPAIGN_ID")
fi
PYTHONPATH="$ROOT/src" "$PYBIN" "$ROOT/scripts/reduce_pf_campaign_shards.py" "${REDUCE_ARGS[@]}"