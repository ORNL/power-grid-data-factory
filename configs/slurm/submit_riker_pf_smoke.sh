#!/bin/bash
set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
ANCHOR_INDEX=${ANCHOR_INDEX:-$ROOT/data/derived/pf_anchors/andes_acopf_anchors.parquet}
CAMPAIGN_ID=${CAMPAIGN_ID:-riker_pf_smoke}
COUNT=${COUNT:-100}
SHARD_COUNT=${SHARD_COUNT:-32}
NTASKS_PER_NODE=${NTASKS_PER_NODE:-32}
WALLTIME=${WALLTIME:-00:30:00}
MAX_FAILURE_FRACTION=${MAX_FAILURE_FRACTION:-1.0}
RESUME=${RESUME:-0}
COINHSL_ENV=${COINHSL_ENV:-$HOME/.local/coinhsl/current/env.sh}

[[ -r "$COINHSL_ENV" ]] || { echo "Missing private Coin-HSL activation: $COINHSL_ENV" >&2; exit 2; }
source "$COINHSL_ENV"
[[ -r "${IPOPT_HSL_LIBRARY:-}" ]] || { echo "Missing Coin-HSL library: ${IPOPT_HSL_LIBRARY:-unset}" >&2; exit 2; }

exec sbatch -N 1 --ntasks-per-node="$NTASKS_PER_NODE" -t "$WALLTIME" \
  --export="ALL,ANCHOR_INDEX=$ANCHOR_INDEX,CAMPAIGN_ID=$CAMPAIGN_ID,COUNT=$COUNT,SHARD_COUNT=$SHARD_COUNT,RUNS_ROOT=data/outputs/runs/$CAMPAIGN_ID,MAX_FAILURE_FRACTION=$MAX_FAILURE_FRACTION,RESUME=$RESUME,IPOPT_HSL_LIBRARY=$IPOPT_HSL_LIBRARY,COINHSL_ENV=$COINHSL_ENV,LD_LIBRARY_PATH=$LD_LIBRARY_PATH" \
  "$ROOT/configs/slurm/riker_pf_mapreduce.sbatch"