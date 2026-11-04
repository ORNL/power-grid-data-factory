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

# Resubmits the runs_3b round 0-6 legacy-quadratic re-solve until its in-place merge
# is applied, then lifts the archive holds on those rounds.
set -euo pipefail

ROOT=/lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory
SUPERVISOR_SCRIPT=$ROOT/configs/slurm/riker_quadratic_resolve_supervisor.sbatch
RUNNER_SCRIPT=$ROOT/configs/slurm/riker_quadratic_resolve.sbatch
PYBIN=$ROOT/.venv-riker/bin/python
WORK=$ROOT/data/outputs/repairs/riker_quadratic_resolve_v2_r000_r006
ARCHIVE_DIR=$ROOT/data/outputs/archives_3b
TASKS_PER_NODE=${TASKS_PER_NODE:-64}
CYCLE=${CYCLE:-1}
MAX_CYCLES=${MAX_CYCLES:-60}
PREV_ATTEMPTED=${PREV_ATTEMPTED:--1}

if (( CYCLE > MAX_CYCLES )); then
  echo "MAX_CYCLES=$MAX_CYCLES exceeded; stopping without resubmission." >&2
  exit 1
fi
unset PYTHONUSERBASE PYTHONNOUSERSITE

read -r APPLIED ATTEMPTED < <("$PYBIN" - "$WORK" <<'PY'
import glob, json, sys
work = sys.argv[1]
parts = [json.load(open(p)) for p in glob.glob(f"{work}/merge_parts/part_*.json")]
counts = {p.get("part_count") for p in parts}
applied = (
    len(counts) == 1
    and all(p.get("applied") is True for p in parts)
    and {p.get("part_index") for p in parts} == set(range(counts.pop()))
)
attempted = sum(json.load(open(p)).get("attempted", 0) for p in glob.glob(f"{work}/progress/shard_*.json"))
print(int(applied), attempted)
PY
)
done_shards=$(find "$WORK/done" -maxdepth 1 -type f | wc -l)
echo "[$(date -u +%FT%TZ)] cycle=$CYCLE/$MAX_CYCLES done_shards=$done_shards attempted=$ATTEMPTED prev=$PREV_ATTEMPTED merge_applied=$APPLIED"

if (( APPLIED == 1 )); then
  rm -f "$ARCHIVE_DIR"/round_00{0..6}.hold
  echo "[$(date -u +%FT%TZ)] merge applied; archive holds for rounds 0-6 removed; supervisor stopping."
  exit 0
fi
if (( ATTEMPTED == PREV_ATTEMPTED )); then
  echo "No re-solve progress since the previous cycle; stopping for inspection." >&2
  exit 1
fi

ROUND_JOB=$(sbatch --parsable --ntasks-per-node="$TASKS_PER_NODE" "$RUNNER_SCRIPT")
ROUND_JOB=${ROUND_JOB%%;*}
[[ "$ROUND_JOB" =~ ^[0-9]+$ ]] || { echo "Invalid re-solve job id: $ROUND_JOB" >&2; exit 1; }

if (( CYCLE < MAX_CYCLES )); then
  NEXT_JOB=$(sbatch --parsable \
    --dependency="afterany:$ROUND_JOB" \
    --export="ALL,CYCLE=$((CYCLE + 1)),MAX_CYCLES=$MAX_CYCLES,TASKS_PER_NODE=$TASKS_PER_NODE,PREV_ATTEMPTED=$ATTEMPTED" \
    "$SUPERVISOR_SCRIPT")
  echo "[$(date -u +%FT%TZ)] submitted re-solve job=$ROUND_JOB next_supervisor=${NEXT_JOB%%;*}"
else
  echo "[$(date -u +%FT%TZ)] submitted re-solve job=$ROUND_JOB; cycle limit reached, no continuation queued"
fi
