#!/bin/bash
#SBATCH -J pgdf_reduce
#SBATCH -A LRN070
#SBATCH -p batch
#SBATCH -N 1
#SBATCH -n 1
#SBATCH --cpus-per-task=1
#SBATCH -t 04:00:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
cd "$ROOT"

CAMPAIGN_ID=${CAMPAIGN_ID:?Set CAMPAIGN_ID}
ROUND_INDEX=${ROUND_INDEX:?Set ROUND_INDEX}
CONFIG=${CONFIG:-configs/campaign_default.yaml}
PYTHON=${PYTHON:-/usr/bin/python3.11}

printf -v ROUND_PAD "%03d" "$ROUND_INDEX"
SHARD_IDS_FILE=${SHARD_IDS_FILE:-data/outputs/campaigns/$CAMPAIGN_ID/round_summaries/round_${ROUND_PAD}_shards/shard_campaign_ids.txt}

unset PYTHONUSERBASE PYTHONNOUSERSITE
export PYTHONPATH="$ROOT/src"
mkdir -p data/scratch/logs

echo "[$(date -u +%FT%TZ)] campaign=$CAMPAIGN_ID round=$ROUND_INDEX reduce-only"
"$PYTHON" scripts/reduce_campaign_shards.py \
  --campaign-id "$CAMPAIGN_ID" \
  --round-index "$ROUND_INDEX" \
  --config "$CONFIG" \
  --shard-campaign-ids-file "$SHARD_IDS_FILE"
echo "[$(date -u +%FT%TZ)] reduce-only job complete"
