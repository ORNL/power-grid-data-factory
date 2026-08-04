#!/bin/bash
# Submit a tiny single-node smoke of the Andes PowerModels AC-OPF map/reduce job.
# Validates the full solver path (bootstrap -> shard -> map -> reduce) end-to-end
# before committing a large multi-node allocation. Every value below can be
# overridden by exporting it before invoking this wrapper, e.g.:
#   PER_CASE=10 SHARD_COUNT=8 configs/slurm/submit_andes_smoke.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SBATCH_FILE="$SCRIPT_DIR/andes_powermodels_acopf_mapreduce_10n_36h.sbatch"

# Tiny defaults (all overridable via the environment).
CAMPAIGN_ID=${CAMPAIGN_ID:-readiness_smoke}
ROUND_INDEX=${ROUND_INDEX:-0}
CASES=${CASES:-pglib_opf_case14_ieee}
PER_CASE=${PER_CASE:-5}
TOPOLOGIES_PER_CASE=${TOPOLOGIES_PER_CASE:-1}
MAX_K=${MAX_K:-2}
BUDGET=${BUDGET:-10}
SEED=${SEED:-1}
SHARD_COUNT=${SHARD_COUNT:-4}
MAX_CANDIDATES=${MAX_CANDIDATES:-8}

# Tiny resource footprint (overrides the sbatch #SBATCH -N/-t/-J directives).
NODES=${NODES:-1}
NTASKS_PER_NODE=${NTASKS_PER_NODE:-4}
WALLTIME=${WALLTIME:-00:30:00}
JOB_NAME=${JOB_NAME:-pgdf_smoke}

echo "[submit_andes_smoke] campaign_id=$CAMPAIGN_ID cases='$CASES' per_case=$PER_CASE shard_count=$SHARD_COUNT"
echo "[submit_andes_smoke] nodes=$NODES ntasks_per_node=$NTASKS_PER_NODE walltime=$WALLTIME"

exec sbatch \
  -J "$JOB_NAME" \
  -N "$NODES" \
  --ntasks-per-node="$NTASKS_PER_NODE" \
  -t "$WALLTIME" \
  --export=ALL,CAMPAIGN_ID="$CAMPAIGN_ID",ROUND_INDEX="$ROUND_INDEX",CASES="$CASES",PER_CASE="$PER_CASE",TOPOLOGIES_PER_CASE="$TOPOLOGIES_PER_CASE",MAX_K="$MAX_K",BUDGET="$BUDGET",SEED="$SEED",SHARD_COUNT="$SHARD_COUNT",MAX_CANDIDATES="$MAX_CANDIDATES" \
  "$SBATCH_FILE"
