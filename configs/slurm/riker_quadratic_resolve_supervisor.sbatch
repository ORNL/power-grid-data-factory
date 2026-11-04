#!/bin/bash
#SBATCH -J pgdf_quad_sup
#SBATCH -A LRN116
#SBATCH -p batch
#SBATCH --qos=normal
#SBATCH -N 1
#SBATCH -n 1
#SBATCH --cpus-per-task=1
#SBATCH -t 00:10:00
#SBATCH -o /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

# Drives the runs_3b round 0-6 legacy-quadratic repair to completion: re-solve, in-place
# merge (only once rounds 0-6 are reduced, so no live map job writes those samples),
# shard report/ledger rebuild and re-reduce; then lifts the round 0-6 archive holds.
set -euo pipefail

ROOT=/lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory
SUPERVISOR_SCRIPT=$ROOT/configs/slurm/riker_quadratic_resolve_supervisor.sbatch
RUNNER_SCRIPT=$ROOT/configs/slurm/riker_quadratic_resolve.sbatch
REBUILD_SCRIPT=$ROOT/configs/slurm/riker_rebuild_round_summaries.sbatch
PYBIN=$ROOT/.venv-riker/bin/python
WORK=$ROOT/data/outputs/repairs/riker_quadratic_resolve_v2_r000_r006
SUMMARIES=$ROOT/data/outputs/campaigns/ultrascale_3b/round_summaries
ARCHIVE_DIR=$ROOT/data/outputs/archives_3b
TASKS_PER_NODE=${TASKS_PER_NODE:-64}
CYCLE=${CYCLE:-1}
MAX_CYCLES=${MAX_CYCLES:-100}
PREV_STATE=${PREV_STATE:-none}

if (( CYCLE > MAX_CYCLES )); then
  echo "MAX_CYCLES=$MAX_CYCLES exceeded; stopping without resubmission." >&2
  exit 1
fi
unset PYTHONUSERBASE PYTHONNOUSERSITE

read -r MERGE_DONE REBUILD_DONE STATE < <("$PYBIN" - "$WORK" <<'PY'
import glob, json, os, sys
work = sys.argv[1]
def load(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return {}
merge = load(f"{work}/merge_manifest.json")
merge_done = merge.get("mode") == "merge_finalize" and merge.get("complete") is True
rebuild_done = load(f"{work}/round_summaries_rebuilt.json").get("complete") is True
attempted = sum(load(p).get("attempted", 0) for p in glob.glob(f"{work}/progress/shard_*.json"))
counts = [len(glob.glob(f"{work}/{d}/part_*.json")) for d in ("merge_index", "merge_parts", "rebuild/parts")]
print(int(merge_done), int(rebuild_done), ":".join(map(str, [attempted, *counts])))
PY
)
shards=$(find "$WORK/shards" -maxdepth 1 -name 'repair_*.jsonl' | wc -l)
done_shards=$(find "$WORK/done" -maxdepth 1 -type f | wc -l)
ROUNDS_READY=1
for r in 0 1 2 3 4 5 6; do
  grep -q '"ok": true' "$SUMMARIES/round_00${r}_mapreduce_reduce_report.json" 2>/dev/null || ROUNDS_READY=0
done
echo "[$(date -u +%FT%TZ)] cycle=$CYCLE/$MAX_CYCLES shards_done=$done_shards/$shards state=$STATE prev=$PREV_STATE rounds_reduced=$ROUNDS_READY merged=$MERGE_DONE rebuilt=$REBUILD_DONE"

if (( REBUILD_DONE == 1 )); then
  rm -f "$ARCHIVE_DIR"/round_00{0..6}.hold
  echo "[$(date -u +%FT%TZ)] merge applied and summaries rebuilt; archive holds for rounds 0-6 removed; supervisor stopping."
  exit 0
fi

NEXT_EXPORT="ALL,CYCLE=$((CYCLE + 1)),MAX_CYCLES=$MAX_CYCLES,TASKS_PER_NODE=$TASKS_PER_NODE,PREV_STATE=$STATE"
if (( MERGE_DONE == 0 && done_shards == shards && ROUNDS_READY == 0 )); then
  # Re-solve finished but round 6 is still being solved; wait rather than merge into live files.
  NEXT_JOB=$(sbatch --parsable --begin=now+6hours --export="${NEXT_EXPORT/PREV_STATE=$STATE/PREV_STATE=$PREV_STATE}" "$SUPERVISOR_SCRIPT")
  echo "[$(date -u +%FT%TZ)] re-solve done, waiting for rounds 0-6 to be reduced; next_supervisor=${NEXT_JOB%%;*}"
  exit 0
fi
if [[ "$STATE" == "$PREV_STATE" ]]; then
  echo "No progress since the previous cycle; stopping for inspection." >&2
  exit 1
fi

if (( MERGE_DONE == 1 )); then
  ROUND_JOB=$(sbatch --parsable "$REBUILD_SCRIPT")
  KIND=rebuild
else
  ROUND_JOB=$(sbatch --parsable --ntasks-per-node="$TASKS_PER_NODE" --export="ALL,RUN_MERGE=$ROUNDS_READY" "$RUNNER_SCRIPT")
  KIND=re-solve
fi
ROUND_JOB=${ROUND_JOB%%;*}
[[ "$ROUND_JOB" =~ ^[0-9]+$ ]] || { echo "Invalid $KIND job id: $ROUND_JOB" >&2; exit 1; }

if (( CYCLE < MAX_CYCLES )); then
  NEXT_JOB=$(sbatch --parsable --dependency="afterany:$ROUND_JOB" --export="$NEXT_EXPORT" "$SUPERVISOR_SCRIPT")
  echo "[$(date -u +%FT%TZ)] submitted $KIND job=$ROUND_JOB next_supervisor=${NEXT_JOB%%;*}"
else
  echo "[$(date -u +%FT%TZ)] submitted $KIND job=$ROUND_JOB; cycle limit reached, no continuation queued"
fi
