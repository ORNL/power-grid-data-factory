#!/bin/bash
# Submit all archive (zstd-compression) jobs, chained to the map/reduce jobs they
# depend on. Runs on a login node; the actual compression happens inside each
# submitted configs/slurm/andes_archive_round.sbatch job.
#
# Layout produced (no filename collisions -- round names are keyed only by round
# index, so each campaign gets its own ARCHIVE_DIR):
#   data/outputs/archives/prev_run/round_000_*   <- previous run, moved aside
#   data/outputs/archives/round_000_*            <- v1 (job 3428668)
#   data/outputs/archives_3b/round_000..149_*    <- new 3B campaign (150 rounds)
#
# 1) preserve the previous run's round_000 archive so the v1 archive does not
#    overwrite it,
# 2) submit the v1 archive job (afterok:3428668) to compress job 3428668's output,
# 3) submit one archive job per 3B round, each afterok on that round's map/reduce
#    job, writing into a unique archive dir.

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
cd "$ROOT"

ARCHIVE_SBATCH=configs/slurm/andes_archive_round.sbatch

# ---- v1 (validation) campaign ----
V1_JOB=3428668                 # running map/reduce job for ultrascale_v1 round 0
V1_CAMPAIGN=ultrascale_v1
V1_RUNS_ROOT=data/outputs/runs
V1_ARCHIVE_DIR=data/outputs/archives

# ---- new 3B campaign ----
B3_CAMPAIGN=ultrascale_3b
B3_RUNS_ROOT=data/outputs/runs_3b
B3_ARCHIVE_DIR=data/outputs/archives_3b   # unique -> never overrides pre-existing archives
B3_FIRST_MAP_JOB=3428782                  # round 0 map/reduce job (rounds are contiguous jobids)
B3_ROUNDS=150

# ---------------------------------------------------------------------------
# 1) preserve the previous run's round_000 archive
# ---------------------------------------------------------------------------
mkdir -p "$V1_ARCHIVE_DIR/prev_run"
for f in round_000_raw.tar.zst \
         round_000_raw.tar.zst.sha256 \
         round_000_convergence_summary.json \
         round_000_shard_campaign_dirs.txt; do
  if [[ -e "$V1_ARCHIVE_DIR/$f" ]]; then
    mv "$V1_ARCHIVE_DIR/$f" "$V1_ARCHIVE_DIR/prev_run/"
    echo "moved previous-run $f -> $V1_ARCHIVE_DIR/prev_run/"
  fi
done

# ---------------------------------------------------------------------------
# 2) archive v1 (job 3428668) output, chained to its completion
# ---------------------------------------------------------------------------
V1_ARCHIVE_JOB=$(CAMPAIGN_ID="$V1_CAMPAIGN" ROUND_INDEX=0 \
  RUNS_ROOT="$V1_RUNS_ROOT" ARCHIVE_DIR="$V1_ARCHIVE_DIR" \
  sbatch --parsable --dependency=afterok:$V1_JOB "$ARCHIVE_SBATCH")
echo "v1 archive job $V1_ARCHIVE_JOB submitted (afterok:$V1_JOB) -> $V1_ARCHIVE_DIR/round_000_raw.tar.zst"

# ---------------------------------------------------------------------------
# 3) archive the 150 3B rounds, each afterok on its round's map/reduce job
# ---------------------------------------------------------------------------
# Verify the contiguous jobid assumption before chaining anything.
last=$((B3_FIRST_MAP_JOB + B3_ROUNDS - 1))
declare -A JNAME
while read -r jid jname; do JNAME[$jid]="$jname"; done < <(squeue -u "$USER" -h -o "%i %j")
for i in $(seq 0 $((B3_ROUNDS - 1))); do
  jid=$((B3_FIRST_MAP_JOB + i))
  expected=$(printf "pgdf_mr_r%03d" "$i")
  got=${JNAME[$jid]:-MISSING}
  if [[ "$got" != "$expected" ]]; then
    echo "ABORT: jobid $jid is '$got', expected '$expected'. 3B map jobs are not the assumed contiguous range $B3_FIRST_MAP_JOB..$last; adjust B3_FIRST_MAP_JOB." >&2
    exit 1
  fi
done
echo "verified 3B map jobs $B3_FIRST_MAP_JOB..$last map to rounds 000..$(printf %03d $((B3_ROUNDS-1)))"

for i in $(seq 0 $((B3_ROUNDS - 1))); do
  round_pad=$(printf "%03d" "$i")
  map_job=$((B3_FIRST_MAP_JOB + i))
  aj=$(CAMPAIGN_ID="$B3_CAMPAIGN" ROUND_INDEX="$i" \
    RUNS_ROOT="$B3_RUNS_ROOT" ARCHIVE_DIR="$B3_ARCHIVE_DIR" \
    sbatch --parsable --dependency=afterok:$map_job "$ARCHIVE_SBATCH")
  echo "3B round $round_pad: archive job $aj (afterok:$map_job) -> $B3_ARCHIVE_DIR/round_${round_pad}_raw.tar.zst"
done

echo "all archive jobs submitted."
