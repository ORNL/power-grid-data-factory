#!/bin/bash
#SBATCH -J pgdf_pf_replay
#SBATCH -A LRN116
#SBATCH -p batch
#SBATCH --qos=normal
#SBATCH -N 1
#SBATCH --ntasks-per-node=32
#SBATCH --cpus-per-task=1
#SBATCH -t 36:00:00
#SBATCH -o /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

set -euo pipefail
ulimit -c 0

ROOT=/lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory
PYBIN=${PYBIN:-$ROOT/.venv-riker/bin/python}
SOURCE_CAMPAIGN_ID=${SOURCE_CAMPAIGN_ID:?Set SOURCE_CAMPAIGN_ID}
CAMPAIGN_ID=${CAMPAIGN_ID:?Set CAMPAIGN_ID to the staged campaign ID}
ROUND_INDEX=${ROUND_INDEX:-3}
CONFIG=${CONFIG:-configs/pf_campaign_riker_severity_25.yaml}
RUNS_ROOT=${RUNS_ROOT:-data/outputs/runs/$CAMPAIGN_ID}
TIMEOUT_S=${TIMEOUT_S:-1200}
MAX_FAILURE_FRACTION=${MAX_FAILURE_FRACTION:-0.25}
PF_LINEAR_SOLVERS=${PF_LINEAR_SOLVERS:-default,ma27,ma57}
PF_QUALITY_METRIC=${PF_QUALITY_METRIC:-software_error}

export PGDF_JULIA_BIN=${PGDF_JULIA_BIN:-$ROOT/.software/bin/julia}
export PGDF_JULIA_PROJECT_DIR=${PGDF_JULIA_PROJECT_DIR:-$ROOT/julia/lockfiles/riker}
export JULIA_DEPOT_PATH=${JULIA_DEPOT_PATH:-$ROOT/.julia_depot_riker}
export OPENBLAS_NUM_THREADS=1 JULIA_NUM_THREADS=1

COINHSL_ENV=${COINHSL_ENV:-$HOME/.local/coinhsl/riker/env.sh}
[[ -r "$COINHSL_ENV" ]] || { echo "Missing private Coin-HSL activation: $COINHSL_ENV" >&2; exit 2; }
source "$COINHSL_ENV"
[[ -x "$PYBIN" && -x "$PGDF_JULIA_BIN" ]] || { echo "Missing Python or Julia executable" >&2; exit 2; }

printf -v ROUND_PAD "%03d" "$ROUND_INDEX"
SOURCE_SHARD_DIR="$ROOT/data/outputs/campaigns/$SOURCE_CAMPAIGN_ID/round_summaries/round_${ROUND_PAD}_pf/shards"
STAGED_ROUND_DIR="$ROOT/data/outputs/campaigns/$CAMPAIGN_ID/round_summaries/round_${ROUND_PAD}_pf"
QUEUE_DIR="$STAGED_ROUND_DIR/shards/queue"
QUEUE_FILE="$QUEUE_DIR/shards.txt"
INDEX_FILE="$QUEUE_DIR/next_index.txt"
LOCK_DIR="$QUEUE_DIR/lock"
DONE_DIR="$QUEUE_DIR/done"
mkdir -p "$ROOT/data/scratch/logs" "$DONE_DIR"
[[ -e "$QUEUE_DIR/ready" && -s "$QUEUE_FILE" ]] || { echo "Missing prepared replay queue: $QUEUE_FILE" >&2; exit 2; }

PYTHONPATH="$ROOT/src" "$PYBIN" -c 'import grid_data_factory, pydantic, pyarrow, yaml'
echo "source=$SOURCE_CAMPAIGN_ID staged=$CAMPAIGN_ID round=$ROUND_INDEX shards=$(wc -l < "$QUEUE_FILE") timeout=$TIMEOUT_S quality=$PF_QUALITY_METRIC"
echo 0 > "$INDEX_FILE"

srun --ntasks="${SLURM_NTASKS}" --nodes="${SLURM_JOB_NUM_NODES}" --signal=USR1@120 bash -lc '
set -euo pipefail
trap '\''true'\'' USR1
ROOT="'"$ROOT"'"; PYBIN="'"$PYBIN"'"; SOURCE_CAMPAIGN_ID="'"$SOURCE_CAMPAIGN_ID"'"; CAMPAIGN_ID="'"$CAMPAIGN_ID"'"; ROUND_INDEX="'"$ROUND_INDEX"'"; ROUND_PAD="'"$ROUND_PAD"'"
CONFIG="'"$CONFIG"'"; SOURCE_SHARD_DIR="'"$SOURCE_SHARD_DIR"'"; QUEUE_FILE="'"$QUEUE_FILE"'"; INDEX_FILE="'"$INDEX_FILE"'"; LOCK_DIR="'"$LOCK_DIR"'"; DONE_DIR="'"$DONE_DIR"'"
RUNS_ROOT="'"$RUNS_ROOT"'"; TIMEOUT_S="'"$TIMEOUT_S"'"; MAX_FAILURE_FRACTION="'"$MAX_FAILURE_FRACTION"'"; PF_LINEAR_SOLVERS="'"$PF_LINEAR_SOLVERS"'"; PF_QUALITY_METRIC="'"$PF_QUALITY_METRIC"'"
export PGDF_JULIA_BIN="'"$PGDF_JULIA_BIN"'" PGDF_JULIA_PROJECT_DIR="'"$PGDF_JULIA_PROJECT_DIR"'" JULIA_DEPOT_PATH="'"$JULIA_DEPOT_PATH"'" OPENBLAS_NUM_THREADS=1 JULIA_NUM_THREADS=1
export IPOPT_HSL_LIBRARY="'"${IPOPT_HSL_LIBRARY:-}"'" LD_LIBRARY_PATH="'"${LD_LIBRARY_PATH:-}"'"
total=$(wc -l < "$QUEUE_FILE")
while true; do
  while ! mkdir "$LOCK_DIR" 2>/dev/null; do :; done
  next=$(cat "$INDEX_FILE")
  if (( next >= total )); then rmdir "$LOCK_DIR"; break; fi
  echo $((next + 1)) > "$INDEX_FILE"; rmdir "$LOCK_DIR"
  shard=$(sed -n "$((next + 1))p" "$QUEUE_FILE")
  [[ -f "$DONE_DIR/$shard" ]] && continue
  shard_campaign="${CAMPAIGN_ID}__r${ROUND_PAD}__s${shard}"
  args=(--campaign-id "$shard_campaign" --round-index "$ROUND_INDEX" --candidates-jsonl "$SOURCE_SHARD_DIR/selected_${shard}.jsonl" --config "$ROOT/$CONFIG" --runs-root "$ROOT/$RUNS_ROOT/mapreduce_round_${ROUND_PAD}/shard_${shard}" --solver-id powermodels_pf_ipopt --timeout-s "$TIMEOUT_S" --max-failure-fraction "$MAX_FAILURE_FRACTION" --linear-solvers "$PF_LINEAR_SOLVERS" --quality-metric "$PF_QUALITY_METRIC" --record-quality-failure --continue-on-error)
  PYTHONPATH="$ROOT/src" "$PYBIN" "$ROOT/scripts/run_campaign_pf_round.py" "${args[@]}"
  touch "$DONE_DIR/$shard"
done
'

missing=$(PYTHONPATH="$ROOT/src" "$PYBIN" - "$QUEUE_FILE" "$DONE_DIR" <<'PY'
import sys
from pathlib import Path

queue = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
done = Path(sys.argv[2])
print(sum(not (done / shard).is_file() for shard in queue if shard))
PY
)
echo "replay_missing_shards=$missing"
(( missing == 0 )) || exit 1