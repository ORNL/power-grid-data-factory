#!/bin/bash
#SBATCH -J pgdf_pf_reduce
#SBATCH -A lrn070
#SBATCH -p batch
#SBATCH --qos=normal
#SBATCH -N 1
#SBATCH -n 1
#SBATCH --cpus-per-task=1
#SBATCH -t 12:00:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
PYBIN=${PYBIN:-$ROOT/.venv-riker/bin/python}
CAMPAIGN_ID=${CAMPAIGN_ID:?Set CAMPAIGN_ID}
ROUND_INDEX=${ROUND_INDEX:?Set ROUND_INDEX}
CONFIG=${CONFIG:-configs/pf_campaign_riker.yaml}
RUNS_ROOT=${RUNS_ROOT:-data/outputs/runs/$CAMPAIGN_ID}
SUPERVISOR_SCRIPT=${SUPERVISOR_SCRIPT:-$ROOT/configs/slurm/riker_pf_campaign_supervisor.sbatch}

printf -v ROUND_PAD "%03d" "$ROUND_INDEX"
ROUND_DIR="$ROOT/data/outputs/campaigns/$CAMPAIGN_ID/round_summaries/round_${ROUND_PAD}_pf"
SHARD_IDS_FILE=${SHARD_IDS_FILE:-$ROUND_DIR/shards/shard_campaign_ids.txt}
QUEUE_FILE="$ROUND_DIR/shards/queue/shards.txt"
DONE_DIR="$ROUND_DIR/shards/queue/done"
REDUCE_MARKER="$ROOT/data/outputs/campaigns/$CAMPAIGN_ID/round_summaries/round_${ROUND_PAD}_pf_reduce_report.json"

cd "$ROOT"
mkdir -p data/scratch/logs "$ROUND_DIR"
[[ -x "$PYBIN" ]] || { echo "Missing Python: $PYBIN" >&2; exit 2; }

if [[ "${ROUND_SELF_CONTINUE:-0}" == "1" ]]; then
  [[ "${SLURM_JOB_ID:-}" =~ ^[0-9]+$ ]] || { echo "Missing SLURM_JOB_ID for self-continuation" >&2; exit 2; }
  CONTINUATION_FILE="$ROUND_DIR/continuation_after_finalizer_${SLURM_JOB_ID}.txt"
  if [[ ! -s "$CONTINUATION_FILE" ]]; then
    CONTINUE_CMD=(sbatch --parsable -A "${ACCOUNT:-lrn070}")
    [[ -n "${QOS:-normal}" ]] && CONTINUE_CMD+=(-q "${QOS:-normal}")
    CONTINUE_CMD+=(--dependency="afterany:${SLURM_JOB_ID}" --export="ALL,CYCLE=$(( ${CYCLE:-1} + 1 ))" "$SUPERVISOR_SCRIPT")
    continuation_job=$("${CONTINUE_CMD[@]}")
    printf '%s\n' "${continuation_job%%;*}" > "$CONTINUATION_FILE.in_progress"
    mv "$CONTINUATION_FILE.in_progress" "$CONTINUATION_FILE"
    echo "[$(date -u +%FT%TZ)] continuation_job=${continuation_job%%;*} dependency=afterany:${SLURM_JOB_ID}"
  fi
fi

if [[ -s "$REDUCE_MARKER" ]] && grep -Eq '"(complete|ok)": true' "$REDUCE_MARKER"; then
  echo "[$(date -u +%FT%TZ)] PF round already reduced successfully: $REDUCE_MARKER"
  exit 0
fi

if [[ ! -s "$QUEUE_FILE" ]]; then
  echo "[$(date -u +%FT%TZ)] PF map incomplete: missing shard queue; reduction deferred"
  exit 0
fi
missing_shards=$(PYTHONPATH="$ROOT/src" "$PYBIN" - "$QUEUE_FILE" "$DONE_DIR" <<'PY'
import sys
from pathlib import Path

queue = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
done = Path(sys.argv[2])
print(sum(not (done / shard).is_file() for shard in queue if shard))
PY
)
if (( missing_shards > 0 )); then
  echo "[$(date -u +%FT%TZ)] PF map incomplete: missing_shards=$missing_shards; reduction deferred"
  exit 0
fi

echo "[$(date -u +%FT%TZ)] PF reduce phase: campaign=$CAMPAIGN_ID round=$ROUND_INDEX"
PYTHONPATH="$ROOT/src" "$PYBIN" "$ROOT/scripts/reduce_pf_campaign_shards.py" \
  --campaign-id "$CAMPAIGN_ID" --round-index "$ROUND_INDEX" --config "$ROOT/$CONFIG" \
  --shard-campaign-ids-file "$SHARD_IDS_FILE" --runs-root "$ROOT/$RUNS_ROOT" --force
echo "[$(date -u +%FT%TZ)] PF reduce phase complete: campaign=$CAMPAIGN_ID round=$ROUND_INDEX"