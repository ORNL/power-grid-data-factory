#!/bin/bash
#SBATCH -J pgdf_repair_2cases
#SBATCH -A LRN070
#SBATCH -p batch
#SBATCH -N 64
#SBATCH --ntasks-per-node=16
#SBATCH --cpus-per-task=1
#SBATCH -t 36:00:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

set -euo pipefail
ulimit -c 0  # disable core dumps: Julia/Ipopt crashes were filling Lustre

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
if [[ -z "${PYBIN:-}" ]]; then
    if [[ -x "$ROOT/.venv/bin/python" ]]; then
        PYBIN="$ROOT/.venv/bin/python"
    else
        PYBIN=python3.11
    fi
fi
REPAIR_ID=${REPAIR_ID:-activsg2000_case300_r000_r005}
ROUNDS=${ROUNDS:-0-5}
CASES=${CASES:-"activsg2000 pglib_opf_case300_ieee"}
SOURCE_RUNS_ROOT=${SOURCE_RUNS_ROOT:-data/outputs/runs_3b}
WORK_ROOT=${WORK_ROOT:-data/outputs/repairs/$REPAIR_ID}
SHARD_COUNT=${SHARD_COUNT:-$(( ${SLURM_NTASKS:-1} * 8 ))}
TIMEOUT_S=${TIMEOUT_S:-1800}
SOLVER_ID=${SOLVER_ID:-powermodels_ac_opf_ipopt_campaign}
LINEAR_SOLVER_FALLBACKS=${LINEAR_SOLVER_FALLBACKS:-ma27,ma57}
APPLY_MERGE=${APPLY_MERGE:-1}
PREFLIGHT_ONLY=${PREFLIGHT_ONLY:-0}

PREPARE_SCRIPT=$ROOT/scripts/repair_failed_campaign_cases.py
SHARD_SCRIPT=$ROOT/scripts/shard_selected_candidates.py
RUN_SCRIPT=$ROOT/scripts/run_campaign_ac_opf_round.py
CANDIDATES=$ROOT/$WORK_ROOT/failed_candidates.jsonl
PREPARE_MANIFEST=$ROOT/$WORK_ROOT/prepare_manifest.json
SHARD_DIR=$ROOT/$WORK_ROOT/shards
REPAIR_RUNS_ROOT=$ROOT/$WORK_ROOT/runs
DONE_DIR=$ROOT/$WORK_ROOT/done
MERGE_MANIFEST=$ROOT/$WORK_ROOT/merge_manifest.json

cd "$ROOT"
mkdir -p data/scratch/logs "$ROOT/$WORK_ROOT" "$DONE_DIR"
unset PYTHONUSERBASE PYTHONNOUSERSITE
module load julia/1.8.2
export JULIA_DEPOT_PATH=${JULIA_DEPOT_PATH:-$ROOT/.julia_depot_andes_profile}
export PGDF_JULIA_SYSIMAGE=${PGDF_JULIA_SYSIMAGE:-$ROOT/julia/sysimages/andes/pgdf_sysimage.so}
export OPENBLAS_NUM_THREADS=1
export JULIA_NUM_THREADS=1

# This repair must never read or modify the active round 006.
PYTHONPATH="$ROOT/src" "$PYBIN" - "$ROUNDS" <<'PY'
import sys
rounds = set()
for token in sys.argv[1].split(","):
    token = token.strip()
    if "-" in token:
        start, end = map(int, token.split("-", 1))
        rounds.update(range(start, end + 1))
    elif token:
        rounds.add(int(token))
if not rounds or min(rounds) < 0 or max(rounds) > 5:
    raise SystemExit(f"repair is restricted to completed rounds 000-005, got {sorted(rounds)}")
print(f"repair rounds={sorted(rounds)}; active round 006 excluded")
PY

grep -q 'c2 \* base_mva\^2' julia/run_opf.jl || {
    echo "Missing per-unit generator-cost conversion in julia/run_opf.jl." >&2
    exit 2
}
grep -q 'angmin == 0.0 and angmax == 0.0' src/grid_data_factory/parsers/matpower.py || {
    echo "Missing MATPOWER zero-angle normalization." >&2
    exit 2
}
PYTHONPATH="$ROOT/src" "$PYBIN" -c 'from grid_data_factory.solvers.powermodels_adapter import PowerModelsAdapter'

COINHSL_ENV=${COINHSL_ENV:-$HOME/.local/coinhsl/andes/current/env.sh}
[[ -r "$COINHSL_ENV" ]] || { echo "Missing private Coin-HSL activation: $COINHSL_ENV" >&2; exit 2; }
source "$COINHSL_ENV"
[[ -r "${IPOPT_HSL_LIBRARY:-}" ]] || { echo "Missing Coin-HSL library" >&2; exit 2; }
case "$(readlink -f "$IPOPT_HSL_LIBRARY")" in
    "$ROOT"|"$ROOT"/*) echo "Coin-HSL must not be loaded from the shared repository" >&2; exit 2 ;;
esac

if [[ "$PREFLIGHT_ONLY" == "1" ]]; then
    echo "Repair preflight passed; no source or repair data was modified."
    exit 0
fi

if [[ ! -s "$PREPARE_MANIFEST" || ! -s "$CANDIDATES" ]]; then
    read -r -a CASE_ARRAY <<< "$CASES"
    PYTHONPATH="$ROOT/src" "$PYBIN" "$PREPARE_SCRIPT" prepare \
        --runs-root "$SOURCE_RUNS_ROOT" \
        --rounds "$ROUNDS" \
        --cases "${CASE_ARRAY[@]}" \
        --output "$CANDIDATES" \
        --manifest "$PREPARE_MANIFEST"
fi

if ! compgen -G "$SHARD_DIR/repair_*.jsonl" >/dev/null; then
    rm -rf "$SHARD_DIR"
    PYTHONPATH="$ROOT/src" "$PYBIN" "$SHARD_SCRIPT" \
        --input "$CANDIDATES" \
        --num-shards "$SHARD_COUNT" \
        --out-dir "$SHARD_DIR" \
        --prefix repair \
        --stream \
        --full-budget
fi

mapfile -t SHARDS < <(find "$SHARD_DIR" -maxdepth 1 -name 'repair_*.jsonl' -size +0 -printf '%f\n' | sort)
if (( ${#SHARDS[@]} == 0 )); then
    echo "No failed target-case candidates found; nothing to repair."
    exit 0
fi
printf '%s\n' "${SHARDS[@]}" > "$ROOT/$WORK_ROOT/shards.txt"

echo "[$(date -u +%FT%TZ)] repair_id=$REPAIR_ID rounds=$ROUNDS shards=${#SHARDS[@]} tasks=$SLURM_NTASKS"
echo "[$(date -u +%FT%TZ)] source=$SOURCE_RUNS_ROOT isolated_retries=$REPAIR_RUNS_ROOT apply_merge=$APPLY_MERGE"

srun --ntasks="$SLURM_NTASKS" --nodes="$SLURM_JOB_NUM_NODES" --signal=USR1@120 bash -lc '
set -euo pipefail
ulimit -c 0  # disable core dumps: Julia/Ipopt crashes were filling Lustre
ROOT="'$ROOT'"
WORK_ROOT="'$WORK_ROOT'"
SHARD_DIR="'$SHARD_DIR'"
REPAIR_RUNS_ROOT="'$REPAIR_RUNS_ROOT'"
DONE_DIR="'$DONE_DIR'"
RUN_SCRIPT="'$RUN_SCRIPT'"
PYBIN="'$PYBIN'"
TIMEOUT_S="'$TIMEOUT_S'"
SOLVER_ID="'$SOLVER_ID'"
LINEAR_SOLVER_FALLBACKS="'$LINEAR_SOLVER_FALLBACKS'"
TASK_ID=${SLURM_PROCID:-0}
TASK_COUNT=${SLURM_NTASKS:-1}
mapfile -t SHARDS < "'$ROOT'/'$WORK_ROOT'/shards.txt"
for ((index=TASK_ID; index<${#SHARDS[@]}; index+=TASK_COUNT)); do
    shard=${SHARDS[$index]}
    shard_number=${shard#repair_}
    shard_number=${shard_number%.jsonl}
    [[ -f "$DONE_DIR/$shard_number" ]] && continue
    PYTHONPATH="$ROOT/src" "$PYBIN" "$RUN_SCRIPT" \
        --campaign-id "repair_activsg2000_case300__s${shard_number}" \
        --round-index 0 \
        --selected-candidates-jsonl "$SHARD_DIR/$shard" \
        --config configs/campaign_ultrascale_3b.yaml \
        --runs-root "$REPAIR_RUNS_ROOT/shard_${shard_number}" \
        --solver-id "$SOLVER_ID" \
        --timeout-s "$TIMEOUT_S" \
        --linear-solver-fallbacks "$LINEAR_SOLVER_FALLBACKS" \
        --continue-on-error \
        --max-failure-fraction 1.0 \
        --resume
    touch "$DONE_DIR/$shard_number"
done
'

PYTHONPATH="$ROOT/src" "$PYBIN" "$PREPARE_SCRIPT" merge \
    --repair-runs-root "$REPAIR_RUNS_ROOT" \
    --work-dir "$ROOT/$WORK_ROOT" \
    --manifest "$MERGE_MANIFEST"

if [[ "$APPLY_MERGE" == "1" ]]; then
    PYTHONPATH="$ROOT/src" "$PYBIN" "$PREPARE_SCRIPT" merge \
        --repair-runs-root "$REPAIR_RUNS_ROOT" \
        --work-dir "$ROOT/$WORK_ROOT" \
        --manifest "$MERGE_MANIFEST" \
        --apply
else
    echo "Dry-run merge complete. Set APPLY_MERGE=1 to atomically replace converged failures."
fi

echo "[$(date -u +%FT%TZ)] repair complete; manifest=$MERGE_MANIFEST"
