#!/bin/bash
#SBATCH -J pgdf_archive_sweep_3b
#SBATCH -A LRN070
#SBATCH -p batch
#SBATCH -N 1
#SBATCH -t 12:00:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

# Archive sweep for ultrascale_3b: iterates over completed rounds and archives
# each one that has a runs tree but no existing archive tarball. Intended to run
# as an afterany gate between solve-chain batches so the Lustre MDS inode load
# from millions of tiny sample files is collapsed before the next solve batch
# starts.  Safe to re-run: already-archived rounds are skipped.

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
cd "$ROOT"

CAMPAIGN_ID=${CAMPAIGN_ID:-ultrascale_3b}
TOTAL_ROUNDS=${TOTAL_ROUNDS:-150}
RUNS_ROOT=${RUNS_ROOT:-data/outputs/runs_3b}
ARCHIVE_DIR=${ARCHIVE_DIR:-data/outputs/archives_3b}
ZSTD_LEVEL=${ZSTD_LEVEL:-6}
case "$ARCHIVE_DIR" in /*) : ;; *) ARCHIVE_DIR="$ROOT/$ARCHIVE_DIR" ;; esac

mkdir -p "$ARCHIVE_DIR" "$ROOT/data/scratch/logs"
command -v lfs >/dev/null 2>&1 && lfs setstripe -c 8 "$ARCHIVE_DIR" 2>/dev/null || true

echo "[$(date -u +%FT%TZ)] sweep start: campaign=$CAMPAIGN_ID rounds=0..$((TOTAL_ROUNDS-1)) runs_root=$RUNS_ROOT archive_dir=$ARCHIVE_DIR"

archived=0
skipped_missing=0
skipped_already=0
failed=0

for (( r=0; r<TOTAL_ROUNDS; r++ )); do
    printf -v ROUND_PAD "%03d" "$r"
    RUNS_TREE="$ROOT/$RUNS_ROOT/mapreduce_round_${ROUND_PAD}"
    ARCHIVE="$ARCHIVE_DIR/round_${ROUND_PAD}_raw.tar.zst"

    # Skip rounds with no runs tree (not yet executed).
    if [[ ! -d "$RUNS_TREE" ]]; then
        (( skipped_missing++ ))
        continue
    fi

    # Skip rounds already successfully archived and verified.
    if [[ -s "$ARCHIVE" && -s "${ARCHIVE}.sha256" ]]; then
        echo "[$(date -u +%FT%TZ)] round $r already archived, skipping"
        (( skipped_already++ ))
        continue
    fi

    echo "[$(date -u +%FT%TZ)] archiving round $r ..."
    if env CAMPAIGN_ID="$CAMPAIGN_ID" ROUND_INDEX="$r" RUNS_ROOT="$RUNS_ROOT" \
           ARCHIVE_DIR="$ARCHIVE_DIR" ZSTD_LEVEL="$ZSTD_LEVEL" TOTAL_ROUNDS="$TOTAL_ROUNDS" \
           bash configs/slurm/andes_archive_round.sbatch; then
        (( archived++ ))
    else
        rc=$?
        if [[ $rc -eq 2 ]]; then
            # runs tree disappeared between check and archive call (race): harmless
            (( skipped_missing++ ))
        else
            echo "[$(date -u +%FT%TZ)] ERROR: archive of round $r exited $rc" >&2
            (( failed++ ))
        fi
    fi
done

echo "[$(date -u +%FT%TZ)] sweep done: archived=$archived skipped_already=$skipped_already skipped_missing=$skipped_missing failed=$failed"
[[ $failed -eq 0 ]]
