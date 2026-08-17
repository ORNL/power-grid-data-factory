#!/bin/bash
#SBATCH -J pgdf_archive_exago_par
#SBATCH -A LRN070
#SBATCH -p batch
#SBATCH -N 1
#SBATCH -t 02:00:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

# Archive one round across all 20 fan-out groups of exago_frontier_large_grids_par
# into per-group zstd tarballs to collapse Lustre inodes. Submit once per round
# (ROUND_INDEX=0..4). Reuses andes_archive_round.sbatch logic via direct bash
# call with env vars; originals removed only after integrity + file-count checks.

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
cd "$ROOT"

BASE_CAMPAIGN_ID=${BASE_CAMPAIGN_ID:-exago_frontier_large_grids_par}
UMBRELLA_RUNS_ROOT=${UMBRELLA_RUNS_ROOT:-data/outputs/runs/exago_frontier_large_grids_par}
BASE_ARCHIVE_DIR=${BASE_ARCHIVE_DIR:-data/outputs/archives_exago_frontier_par}
TOTAL_ROUNDS=${TOTAL_ROUNDS:-5}
NUM_GROUPS=${NUM_GROUPS:-20}
ZSTD_LEVEL=${ZSTD_LEVEL:-6}
# Which round this job processes (override at submit time, e.g. --export=ALL,ROUND_INDEX=2)
ROUND_INDEX=${ROUND_INDEX:-0}

mkdir -p "$ROOT/data/scratch/logs"

echo "[$(date -u +%FT%TZ)] archive sweep: base=$BASE_CAMPAIGN_ID round=$ROUND_INDEX groups=0..$((NUM_GROUPS-1))"
archived=0; skipped=0; failed=0

for (( g=0; g<NUM_GROUPS; g++ )); do
    gid=$(printf "%02d" "$g")
    campaign_id="${BASE_CAMPAIGN_ID}_g${gid}"
    runs_root="${UMBRELLA_RUNS_ROOT}/g${gid}"
    archive_dir="${BASE_ARCHIVE_DIR}/g${gid}"
    mkdir -p "$archive_dir"
    command -v lfs >/dev/null 2>&1 && lfs setstripe -c 8 "$archive_dir" 2>/dev/null || true

    archive="${archive_dir}/$(printf 'round_%03d' "$ROUND_INDEX")_raw.tar.zst"
    if [[ -s "$archive" && -s "${archive}.sha256" ]]; then
        echo "[$(date -u +%FT%TZ)] g${gid} round $ROUND_INDEX already archived, skipping"
        (( skipped++ )); continue
    fi
    echo "[$(date -u +%FT%TZ)] archiving g${gid} round $ROUND_INDEX -> $archive"
    if env CAMPAIGN_ID="$campaign_id" \
           ROUND_INDEX="$ROUND_INDEX" \
           RUNS_ROOT="$runs_root" \
           ARCHIVE_DIR="$archive_dir" \
           ZSTD_LEVEL="$ZSTD_LEVEL" \
           TOTAL_ROUNDS="$TOTAL_ROUNDS" \
           bash "$ROOT/configs/slurm/andes_archive_round.sbatch"; then
        echo "[$(date -u +%FT%TZ)] g${gid} round $ROUND_INDEX archived OK"
        (( archived++ ))
    else
        rc=$?
        echo "[$(date -u +%FT%TZ)] g${gid} round $ROUND_INDEX FAILED (rc=$rc), continuing" >&2
        (( failed++ ))
    fi
done

echo "[$(date -u +%FT%TZ)] sweep done: archived=$archived skipped=$skipped failed=$failed"
[[ "$failed" -gt 0 ]] && exit 1 || exit 0
