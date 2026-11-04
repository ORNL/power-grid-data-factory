#!/bin/bash
#SBATCH -J pgdf_cost_rescale
#SBATCH -A LRN116
#SBATCH -p batch
#SBATCH --qos=normal
#SBATCH -N 16
#SBATCH --ntasks-per-node=32
#SBATCH --cpus-per-task=1
#SBATCH -t 08:00:00
#SBATCH -o /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err
# Exact per-unit cost rescale of legacy linear-cost PowerModels AC-OPF records.
# Idempotent and resumable: completed parts are skipped, converted records are
# marked with result.cost_convention and never touched again. Quadratic-cost
# records are left unchanged for re-solve (riker_quadratic_resolve.sh).

set -euo pipefail
ulimit -c 0

ROOT=/lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory
PYBIN=${PYBIN:-$ROOT/.venv-riker/bin/python}
BACKFILL_ID=${BACKFILL_ID:-cost_rescale_v2}
ROUNDS=${ROUNDS:-0-6}
DRY_RUN=${DRY_RUN:-0}
WORK=$ROOT/data/outputs/repairs/$BACKFILL_ID
SOURCE_LIST=$WORK/source_files.tsv
PARTS=$WORK/parts
[[ "$DRY_RUN" == "1" ]] && PARTS=$WORK/parts_dry_run
SCRIPT=$ROOT/scripts/backfill_cost_scaling.py

cd "$ROOT"
unset PYTHONUSERBASE PYTHONNOUSERSITE
mkdir -p data/scratch/logs "$PARTS"
[[ -x "$PYBIN" ]] || { echo "Missing Python: $PYBIN" >&2; exit 2; }

if [[ ! -s "$SOURCE_LIST" ]]; then
    tmp="$SOURCE_LIST.in_progress"
    rm -f "$tmp"
    "$PYBIN" "$SCRIPT" list-sources --runs-root data/outputs/runs_3b --rounds "$ROUNDS" \
        --campaign-prefix ultrascale_3b --output "$tmp" --append
    "$PYBIN" "$SCRIPT" list-sources --runs-root data/outputs/runs_3b_activsg_hsl_almost_restart --rounds 0 \
        --shard-count 331 --campaign-prefix activsg_hsl_almost_restart --output "$tmp" --append
    "$PYBIN" "$SCRIPT" list-sources --runs-root data/outputs/benchmarks/activsg_hsl_ma27_ma57/runs --rounds 0 \
        --shard-count 40 --campaign-prefix activsg_hsl_ma27_ma57 --output "$tmp" --append
    # activsg_hsl_full_3b has no per-shard reports; samples only.
    "$PYBIN" "$SCRIPT" list-sources --runs-root data/outputs/runs_3b_activsg_hsl_full --rounds 0 \
        --shard-count 1024 --output "$tmp" --append
    for bench in ultrascale_3b_grid_readiness ultrascale_3b_long_timeout; do
        for item in $(ls "data/outputs/benchmarks/$bench/runs"); do
            printf '%s\t\n' "$ROOT/data/outputs/benchmarks/$bench/runs/$item/ac_opf/samples.jsonl" >> "$tmp"
        done
    done
    mv "$tmp" "$SOURCE_LIST"
fi
echo "[$(date -u +%FT%TZ)] sources=$(wc -l < "$SOURCE_LIST") tasks=$SLURM_NTASKS dry_run=$DRY_RUN"

srun --ntasks="$SLURM_NTASKS" --nodes="$SLURM_JOB_NUM_NODES" --kill-on-bad-exit=0 bash -c '
set -euo pipefail
ulimit -c 0
part=$(printf "%05d" "$SLURM_PROCID")
manifest="'$PARTS'/part_${part}.json"
[[ -s "$manifest" ]] && exit 0
args=(--source-list "'$SOURCE_LIST'" --part-index "$SLURM_PROCID" --part-count "$SLURM_NTASKS" --manifest "$manifest")
[[ "'$DRY_RUN'" == "1" ]] && args+=(--dry-run)
for attempt in {1..10}; do
    "'$PYBIN'" "'$SCRIPT'" run-part "${args[@]}" > /dev/null && exit 0
    echo "part $SLURM_PROCID failed attempt $attempt" >&2
done
exit 1
' || echo "some parts failed; resubmit to resume" >&2

"$PYBIN" "$SCRIPT" finalize --parts-dir "$PARTS" --part-count "$SLURM_NTASKS" --output "$PARTS/summary.json" \
    | "$PYBIN" -c 'import json,sys; d=json.load(sys.stdin); print({k: d[k] for k in ("missing_parts","files_processed","files_rewritten","missing_files","report_rows_updated","counts")})'
"$PYBIN" -c 'import json,sys; sys.exit(1 if json.load(open(sys.argv[1]))["missing_parts"] else 0)' "$PARTS/summary.json"
echo "[$(date -u +%FT%TZ)] cost rescale complete: $PARTS/summary.json"
