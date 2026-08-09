#!/bin/bash
#SBATCH -J pgdf_archive_r
#SBATCH -A LRN070
#SBATCH -p batch
#SBATCH -N 1
#SBATCH -t 12:00:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

# Archive one campaign round's raw output into a single zstd tarball to collapse
# millions of tiny files/inodes into a few large striped files (Lustre MDS
# relief). Runs afterok on the round's map/reduce job, so the reduce stage has
# already consumed every samples.jsonl. Originals are removed ONLY after the
# archive passes a zstd stream test AND a regular-file count match.
#
# CLASSIFICATION CORPUS: the archived samples.jsonl retain EVERY solve regardless
# of termination_status -- feasible/converged AND infeasible/non-converged -- so
# the tarball is a labeled dataset for feasibility-classification training. No
# termination_status is filtered out at any stage. The convergence sidecar below
# records the feasible-vs-infeasible split and label definition.

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
cd "$ROOT"

CAMPAIGN_ID=${CAMPAIGN_ID:-ultrascale_150m}
ROUND_INDEX=${ROUND_INDEX:-0}
# zstd level: -6 is ~2x faster than -12 for ~10-15% larger output on this JSONL (~4.9x vs ~5.x).
ZSTD_LEVEL=${ZSTD_LEVEL:-6}
# Must match the map job's RUNS_ROOT so a campaign using a non-default runs-root
# (e.g. ultrascale_3b -> data/outputs/runs_3b) archives the correct sample tree.
RUNS_ROOT=${RUNS_ROOT:-data/outputs/runs}
printf -v ROUND_PAD "%03d" "$ROUND_INDEX"

RUNS_TREE="${RUNS_ROOT}/mapreduce_round_${ROUND_PAD}"
# ARCHIVE_DIR: output location for the tarball + sidecars. Override per-campaign
# to avoid round_XXX filename collisions across campaigns (round names are keyed
# only by round index), e.g. ultrascale_3b -> data/outputs/archives_3b. Relative
# paths resolve against $ROOT.
ARCHIVE_DIR=${ARCHIVE_DIR:-data/outputs/archives}
case "$ARCHIVE_DIR" in /*) : ;; *) ARCHIVE_DIR="$ROOT/$ARCHIVE_DIR" ;; esac
ARCHIVE="$ARCHIVE_DIR/round_${ROUND_PAD}_raw.tar.zst"
SHARD_LIST="$ARCHIVE_DIR/round_${ROUND_PAD}_shard_campaign_dirs.txt"
# Sibling tarball holding the campaign's curated reduce outputs (deduped ledgers +
# reduce report + config). reduce_campaign_shards.py writes these at the campaign
# ROOT, OUTSIDE the runs tree, so they are NOT in the raw tarball; folding them here
# keeps data/outputs/archives self-contained.
ARCHIVE_META="$ARCHIVE_DIR/round_${ROUND_PAD}_campaign_meta.tar.zst"
CAMPAIGN_META_LIST="$ARCHIVE_DIR/round_${ROUND_PAD}_campaign_meta_files.txt"

mkdir -p "$ARCHIVE_DIR" "$ROOT/data/scratch/logs"
command -v lfs >/dev/null 2>&1 && lfs setstripe -c 8 "$ARCHIVE_DIR" 2>/dev/null || true

if [[ ! -d "$RUNS_TREE" ]]; then
  echo "[$(date -u +%FT%TZ)] runs tree $RUNS_TREE missing; nothing to archive" >&2
  exit 2
fi

# Per-shard campaign ledger dirs (thousands of tiny dirs) archived alongside the
# sample tree. The campaign ROOT dir is kept in place (a running multi-round
# campaign appends to its ledgers), but its CURATED reduce outputs (ledgers +
# reduce report + config) are also folded into a sibling metadata tarball below so
# the archive is self-contained; the large REGENERABLE intermediates (screened
# pool, seed/selected candidates, shard files) are deterministic from seed+config
# and are deliberately excluded.
find data/outputs/campaigns -maxdepth 1 -type d -name "${CAMPAIGN_ID}__r${ROUND_PAD}__s*" \
  > "$SHARD_LIST" || true
N_SHARD_DIRS=$(wc -l < "$SHARD_LIST")

echo "[$(date -u +%FT%TZ)] archiving $RUNS_TREE + $N_SHARD_DIRS per-shard campaign dirs -> $ARCHIVE"

# Convergence tally over the raw samples (parallel grep across all shards) written
# as a sidecar next to the archive BEFORE the raw tree is removed. Every record is
# classified feasible vs infeasible for downstream classification training:
# feasible = solver reached an optimal/near-optimal point (LOCALLY_SOLVED,
# ALMOST_LOCALLY_SOLVED, OPTIMAL, converged); infeasible = every other
# termination_status. The full status histogram is kept too.
SUMMARY="$ARCHIVE_DIR/round_${ROUND_PAD}_convergence_summary.json"
if [[ -s "$SUMMARY" ]] && python3.11 -c 'import json,sys;json.load(open(sys.argv[1]))["total_records"]' "$SUMMARY" >/dev/null 2>&1; then
  echo "[$(date -u +%FT%TZ)] valid convergence summary already present, skipping tally: $SUMMARY"
else
  echo "[$(date -u +%FT%TZ)] tallying termination_status (feasible/infeasible split) across raw samples -> $SUMMARY"
  # --line-buffered: each match is one atomic <PIPE_BUF write so parallel greps do not tear lines.
  find "$RUNS_TREE" -name samples.jsonl -size +0 -print0 2>/dev/null \
    | xargs -0 -P "$(nproc)" grep -ohE --line-buffered '"termination_status"[[:space:]]*:[[:space:]]*"[^"]*"' 2>/dev/null \
    | awk -F'"' -v RI="$ROUND_INDEX" '
        function is_feasible(s) {
          return (s=="LOCALLY_SOLVED" || s=="ALMOST_LOCALLY_SOLVED" || s=="OPTIMAL" || s=="converged")
        }
        { c[$4]++; t++; if (is_feasible($4)) f++ }
        END {
          printf "{\n  \"round_index\": %d,\n  \"total_records\": %d,\n", RI, t;
          printf "  \"feasible_count\": %d,\n", f;
          printf "  \"infeasible_count\": %d,\n", t-f;
          printf "  \"feasible_fraction\": %.6f,\n", (t>0 ? f/t : 0);
          printf "  \"infeasible_fraction\": %.6f,\n", (t>0 ? (t-f)/t : 0);
          printf "  \"converged_locally_solved\": %d,\n", c["LOCALLY_SOLVED"];
          printf "  \"converged_fraction\": %.6f,\n", (t>0 ? c["LOCALLY_SOLVED"]/t : 0);
          printf "  \"classification_label\": {\"feasible\": [\"LOCALLY_SOLVED\", \"ALMOST_LOCALLY_SOLVED\", \"OPTIMAL\", \"converged\"], \"infeasible\": \"all_other_termination_status\"},\n";
          printf "  \"status_histogram\": {";
          first=1; for (k in c) { printf "%s\n    \"%s\": %d", (first?"":","), k, c[k]; first=0 }
          printf "\n  }\n}\n";
        }' > "$SUMMARY"
  echo "[$(date -u +%FT%TZ)] convergence summary:"; cat "$SUMMARY"
fi

tar --use-compress-program="zstd -T0 -$ZSTD_LEVEL" -cf "$ARCHIVE" "$RUNS_TREE" -T "$SHARD_LIST"
echo "[$(date -u +%FT%TZ)] archive written: $(du -h "$ARCHIVE" | cut -f1)"

# Integrity: decompress-and-discard the full stream.
zstd -t "$ARCHIVE"
echo "[$(date -u +%FT%TZ)] zstd stream integrity OK"

# Regular-file count must match between archive members and source.
ARCH_FILES=$(tar --use-compress-program='zstd -d -T0' -tf "$ARCHIVE" | grep -vc '/$')
SRC_FILES=$( { find "$RUNS_TREE" -type f
  while read -r d; do [[ -n "$d" && -d "$d" ]] && find "$d" -type f; done < "$SHARD_LIST"
} | wc -l )
echo "[$(date -u +%FT%TZ)] file counts archive=$ARCH_FILES source=$SRC_FILES"
if [[ "$ARCH_FILES" -ne "$SRC_FILES" ]]; then
  echo "FILE COUNT MISMATCH -> keeping originals, aborting cleanup" >&2
  exit 3
fi

sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
echo "[$(date -u +%FT%TZ)] verification passed"

# --- Campaign-level curated outputs -> sibling self-contained metadata tarball ---
# The raw tarball above is the labeled sample corpus. reduce_campaign_shards.py
# ALSO writes deduped ledgers + a reduce report at the campaign ROOT, which live
# OUTSIDE the runs tree and so are absent from the raw tarball. Fold those + config
# into round_${PAD}_campaign_meta.tar.zst. The large REGENERABLE intermediates
# (round0_screened_candidates / seed_* / round_${PAD}_selected_candidates /
# round_${PAD}_shards) are deterministic from seed+config and are EXCLUDED.
CAMP_DIR="data/outputs/campaigns/$CAMPAIGN_ID"
: > "$CAMPAIGN_META_LIST"
# Per-round provenance (small, specific to this exact round) -- always folded.
for f in \
  "$CAMP_DIR/round_summaries/round_${ROUND_PAD}_mapreduce_reduce_report.json" \
  "$CAMP_DIR/round_summaries/round_${ROUND_PAD}.json" \
  "$CAMP_DIR/campaign_config.yaml" \
  "$CAMP_DIR/README.md"; do
  [[ -s "$f" ]] && printf '%s\n' "$f" >> "$CAMPAIGN_META_LIST"
done
# Campaign-level reduce ledgers ACCUMULATE across rounds (reduce appends), so a
# round-N tarball would carry rows for rounds 0..N. To avoid re-tarring the growing
# ledger on every round, include it only on the FINAL round (auto) or when forced.
# INCLUDE_LEDGERS=1 force include, =0 skip, =auto (default) include iff final round.
# TOTAL_ROUNDS>0 marks the final round as ROUND_INDEX==TOTAL_ROUNDS-1; unset/0 means
# a single/last round -> ledgers included. On the final round the ledger snapshot is
# complete, so archives/ carries the whole-campaign ledger exactly once.
INCLUDE_LEDGERS=${INCLUDE_LEDGERS:-auto}
TOTAL_ROUNDS=${TOTAL_ROUNDS:-0}
_include_ledgers=0
if [[ "$INCLUDE_LEDGERS" == "1" ]]; then
  _include_ledgers=1
elif [[ "$INCLUDE_LEDGERS" == "auto" ]]; then
  if [[ "$TOTAL_ROUNDS" -le 0 ]] || (( ROUND_INDEX >= TOTAL_ROUNDS - 1 )); then
    _include_ledgers=1
  fi
fi
if [[ "$_include_ledgers" == "1" ]]; then
  for led in diversity_ledger active_constraint_ledger security_boundary_ledger \
             contingency_portfolio acquisition_decisions candidate_registry screening_audit; do
    f="$CAMP_DIR/${led}.parquet.jsonl"
    [[ -s "$f" ]] && printf '%s\n' "$f" >> "$CAMPAIGN_META_LIST"
  done
  echo "[$(date -u +%FT%TZ)] campaign metadata: including cumulative reduce ledgers (INCLUDE_LEDGERS=$INCLUDE_LEDGERS, round $ROUND_INDEX/$TOTAL_ROUNDS)"
else
  echo "[$(date -u +%FT%TZ)] campaign metadata: per-round provenance only, skipping cumulative ledgers for intermediate round $ROUND_INDEX/$TOTAL_ROUNDS"
fi

if [[ -s "$CAMPAIGN_META_LIST" ]]; then
  N_META=$(wc -l < "$CAMPAIGN_META_LIST")
  echo "[$(date -u +%FT%TZ)] writing $N_META campaign metadata files -> $ARCHIVE_META"
  tar --use-compress-program="zstd -T0 -$ZSTD_LEVEL" -cf "$ARCHIVE_META" -T "$CAMPAIGN_META_LIST"
  zstd -t "$ARCHIVE_META"
  META_ARCH_FILES=$(tar --use-compress-program='zstd -d -T0' -tf "$ARCHIVE_META" | grep -vc '/$')
  META_SRC_FILES=$(wc -l < "$CAMPAIGN_META_LIST")
  echo "[$(date -u +%FT%TZ)] metadata file counts archive=$META_ARCH_FILES source=$META_SRC_FILES"
  if [[ "$META_ARCH_FILES" -ne "$META_SRC_FILES" ]]; then
    echo "METADATA FILE COUNT MISMATCH -> keeping raw archive, metadata archive suspect" >&2
    exit 4
  fi
  sha256sum "$ARCHIVE_META" > "$ARCHIVE_META.sha256"
  echo "[$(date -u +%FT%TZ)] campaign metadata archive written + verified: $(du -h "$ARCHIVE_META" | cut -f1)"
else
  echo "[$(date -u +%FT%TZ)] no campaign metadata files found to archive (skipping meta tarball)"
fi

echo "[$(date -u +%FT%TZ)] removing raw originals"

rm -rf "$RUNS_TREE"
while read -r d; do [[ -n "$d" && -d "$d" ]] && rm -rf "$d"; done < "$SHARD_LIST"

echo "[$(date -u +%FT%TZ)] done. raw=$ARCHIVE meta=${ARCHIVE_META}$([[ -s "$ARCHIVE_META" ]] || echo ' (not written)')"
