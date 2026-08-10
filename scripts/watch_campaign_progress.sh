#!/bin/bash
# Lightweight progress watcher for a running map/reduce campaign.
# Read-only: inspects SLURM queue and exact on-disk campaign metadata. Safe to
# run any time (single snapshot); pass a loop interval as $1 to keep refreshing.
#
# Usage:
#   scripts/watch_campaign_progress.sh                 # one snapshot
#   scripts/watch_campaign_progress.sh 300             # refresh every 300s
# Env overrides:
#   CAMPAIGN_ID (ultrascale_3b)  RUNS_ROOT (data/outputs/runs_3b)
#   TOTAL_ROUNDS (150)
set -uo pipefail
cd "$(dirname "$0")/.."

CAMPAIGN_ID=${CAMPAIGN_ID:-ultrascale_3b}
RUNS_ROOT=${RUNS_ROOT:-data/outputs/runs_3b}
TOTAL_ROUNDS=${TOTAL_ROUNDS:-150}
INTERVAL=${1:-0}

snapshot() {
  local now; now=$(date -u +%FT%TZ)
  echo "================ $CAMPAIGN_ID @ $now ================"

  # --- SLURM queue ---
  local run pend
  run=$(squeue -u "$USER" -h -t RUNNING -o "%i %j %M %L %D" 2>/dev/null | grep -i "$CAMPAIGN_ID\|pgdf_mr" || true)
  pend=$(squeue -u "$USER" -h -t PENDING -o "%i" 2>/dev/null | grep -c . || echo 0)
  echo "queue: RUNNING=$(printf '%s' "$run" | grep -c . || echo 0)  PENDING=$pend"
  [[ -n "$run" ]] && printf '  %s\n' "$run" | sed 's/^/  run /'

  # --- furthest round with a runs tree ---
  local rp round_dir round_idx
  round_dir=$(ls -d "$RUNS_ROOT"/mapreduce_round_* 2>/dev/null | sort | tail -1)
  if [[ -z "$round_dir" ]]; then echo "no round tree under $RUNS_ROOT yet"; return; fi
  round_idx=$(basename "$round_dir" | grep -oE '[0-9]+$')

  # --- exact map metadata (does not scan multi-TB JSONL contents) ---
  local summary_dir shard_dir total_shards done active selected
  summary_dir="data/outputs/campaigns/$CAMPAIGN_ID/round_summaries"
  shard_dir="$summary_dir/round_${round_idx}_shards"
  total_shards=$(find "$shard_dir" -maxdepth 1 -name 'selected_*.jsonl' 2>/dev/null | wc -l)
  done=$(find "$shard_dir/queue/done" -type f 2>/dev/null | wc -l)
  active=$(find "$round_dir" -name samples.jsonl -size +0 2>/dev/null | wc -l)
  selected=$(python3.11 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("selected_count", "unknown"))' \
    "$summary_dir/round_${round_idx}.json" 2>/dev/null || echo unknown)

  echo "round $round_idx: selected_candidates=$selected  output_size=$(du -sh "$round_dir" 2>/dev/null | cut -f1)"
  echo "  shards: done=$done/$total_shards  with_output=$active"

  # --- reduced rounds so far ---
  local reduced
  reduced=$(ls -1 "data/outputs/campaigns/$CAMPAIGN_ID/round_summaries/"*_mapreduce_reduce_report.json 2>/dev/null | wc -l)
  echo "rounds reduced: $reduced/$TOTAL_ROUNDS"
}

if [[ "$INTERVAL" -gt 0 ]]; then
  while true; do snapshot; echo; sleep "$INTERVAL"; done
else
  snapshot
fi
