#!/bin/bash
#SBATCH -J pgdf_rebuild_r0_6
#SBATCH -A LRN116
#SBATCH -p batch
#SBATCH --qos=normal
#SBATCH -N 16
#SBATCH --ntasks-per-node=64
#SBATCH --cpus-per-task=1
#SBATCH -t 12:00:00
#SBATCH -o /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

# After the round 0-6 quadratic re-solve merge: rebuild every shard report and ledger
# from the merged samples, then re-reduce each round. Resumable; resubmit on failure.
set -euo pipefail
ulimit -c 0

ROOT=/lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory
PYBIN=${PYBIN:-$ROOT/.venv-riker/bin/python}
CAMPAIGN_ID=ultrascale_3b
ROUNDS=0-6
CONFIG=configs/campaign_ultrascale_3b.yaml
REPAIR=$ROOT/data/outputs/repairs/riker_quadratic_resolve_v2_r000_r006
WORK=$REPAIR/rebuild
DONE_MARKER=$REPAIR/round_summaries_rebuilt.json
SUMMARIES=$ROOT/data/outputs/campaigns/$CAMPAIGN_ID/round_summaries

cd "$ROOT"
unset PYTHONUSERBASE PYTHONNOUSERSITE
export PYTHONPATH="$ROOT/src"

"$PYBIN" -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get("mode")=="merge_finalize" and d.get("complete") else 1)' \
    "$REPAIR/merge_manifest.json" || { echo "Quadratic re-solve merge not finalized; nothing to rebuild." >&2; exit 2; }
for r in 0 1 2 3 4 5 6; do
    printf -v pad "%03d" "$r"
    grep -q '"ok": true' "$SUMMARIES/round_${pad}_mapreduce_reduce_report.json" 2>/dev/null \
        || { echo "Round $r has no successful reduce yet; rebuild deferred." >&2; exit 2; }
done

mkdir -p "$WORK/parts"
echo "[$(date -u +%FT%TZ)] rebuilding shard reports and ledgers for rounds $ROUNDS with $SLURM_NTASKS tasks"
srun --ntasks="$SLURM_NTASKS" --nodes="$SLURM_JOB_NUM_NODES" --kill-on-bad-exit=0 bash -c '
"'"$PYBIN"'" "'"$ROOT"'/scripts/rebuild_round_shard_summaries.py" run-part \
    --campaign-id "'"$CAMPAIGN_ID"'" --rounds "'"$ROUNDS"'" --runs-root data/outputs/runs_3b \
    --config "'"$CONFIG"'" --execution-policy configs/campaign_execution_tiers_3b.yaml --timeout-s 1800 \
    --work-dir "'"$WORK"'" --part-index "$SLURM_PROCID" --part-count "$SLURM_NTASKS" >/dev/null
' || echo "some rebuild parts failed; resubmit to resume" >&2

"$PYBIN" scripts/rebuild_round_shard_summaries.py finalize \
    --work-dir "$WORK" --part-count "$SLURM_NTASKS" --output "$WORK/rebuild_manifest.json"

for r in 0 1 2 3 4 5 6; do
    printf -v pad "%03d" "$r"
    echo "[$(date -u +%FT%TZ)] re-reducing round $r"
    "$PYBIN" scripts/reduce_campaign_shards.py --campaign-id "$CAMPAIGN_ID" --round-index "$r" --config "$CONFIG" \
        --shard-campaign-ids-file "$SUMMARIES/round_${pad}_shards/shard_campaign_ids.txt" --force >/dev/null
done

cp "$WORK/rebuild_manifest.json" "$DONE_MARKER"
echo "[$(date -u +%FT%TZ)] round 0-6 summaries rebuilt: $DONE_MARKER"
