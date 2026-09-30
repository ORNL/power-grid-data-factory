#!/bin/bash
#SBATCH -J pgdf_repair_2cases
#SBATCH -A lrn070
#SBATCH -p batch
#SBATCH --qos=normal
#SBATCH -N 16
#SBATCH --ntasks-per-node=32
#SBATCH --cpus-per-task=1
#SBATCH -t 36:00:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
PYBIN=${PYBIN:-$ROOT/.venv-riker/bin/python}
REPAIR_ID=${REPAIR_ID:-riker_activsg2000_case300_r000_r005_parallel_v1}
ROUNDS=${ROUNDS:-0-5}
CASES=${CASES:-"activsg2000 pglib_opf_case300_ieee"}
ALL_CASES=${ALL_CASES:-0}
SOURCE_RUNS_ROOT=${SOURCE_RUNS_ROOT:-data/outputs/runs_3b}
WORK_ROOT=${WORK_ROOT:-data/outputs/repairs/$REPAIR_ID}
SHARD_COUNT=${SHARD_COUNT:-$(( ${SLURM_NTASKS:-512} * 8 ))}
TIMEOUT_S=${TIMEOUT_S:-1800}
SOLVER_ID=${SOLVER_ID:-powermodels_ac_opf_ipopt_campaign}
LINEAR_SOLVER_FALLBACKS=${LINEAR_SOLVER_FALLBACKS:-ma27,ma57}
LINEAR_SOLVERS=${LINEAR_SOLVERS:-}
CAMPAIGN_PREFIX=${CAMPAIGN_PREFIX:-repair_activsg2000_case300}
APPLY_MERGE=${APPLY_MERGE:-1}
RUN_MERGE=${RUN_MERGE:-1}
SHARD_GROUP_COUNT=${SHARD_GROUP_COUNT:-1}
SHARD_GROUP_INDEX=${SHARD_GROUP_INDEX:-0}
PREFLIGHT_ONLY=${PREFLIGHT_ONLY:-0}

PREPARE_SCRIPT=$ROOT/scripts/repair_failed_campaign_cases.py
SHARD_SCRIPT=$ROOT/scripts/shard_selected_candidates.py
RUN_SCRIPT=$ROOT/scripts/run_campaign_ac_opf_round.py
CANDIDATES=$ROOT/$WORK_ROOT/failed_candidates.jsonl
PREPARE_MANIFEST=$ROOT/$WORK_ROOT/prepare_manifest.json
SOURCE_LIST=$ROOT/$WORK_ROOT/source_files.txt
PREPARE_PARTS_DIR=$ROOT/$WORK_ROOT/prepare_parts
SHARD_DIR=$ROOT/$WORK_ROOT/shards
REPAIR_RUNS_ROOT=$ROOT/$WORK_ROOT/runs
DONE_DIR=$ROOT/$WORK_ROOT/done
PROGRESS_DIR=$ROOT/$WORK_ROOT/progress
MERGE_MANIFEST=$ROOT/$WORK_ROOT/merge_manifest.json

cd "$ROOT"
mkdir -p data/scratch/logs "$ROOT/$WORK_ROOT" "$DONE_DIR" "$PROGRESS_DIR"
unset PYTHONUSERBASE PYTHONNOUSERSITE
export PGDF_JULIA_BIN=${PGDF_JULIA_BIN:-$ROOT/.software/bin/julia}
export PGDF_JULIA_PROJECT_DIR=${PGDF_JULIA_PROJECT_DIR:-$ROOT/julia/lockfiles/riker}
export JULIA_DEPOT_PATH=${JULIA_DEPOT_PATH:-$ROOT/.julia_depot_riker}
export OPENBLAS_NUM_THREADS=1
export JULIA_NUM_THREADS=1

[[ -x "$PYBIN" ]] || { echo "Missing Python: $PYBIN" >&2; exit 2; }
[[ -x "$PGDF_JULIA_BIN" ]] || { echo "Missing Julia wrapper: $PGDF_JULIA_BIN" >&2; exit 2; }
(( SHARD_GROUP_COUNT >= 1 )) || { echo "SHARD_GROUP_COUNT must be at least 1" >&2; exit 2; }
(( SHARD_GROUP_INDEX >= 0 && SHARD_GROUP_INDEX < SHARD_GROUP_COUNT )) || {
    echo "SHARD_GROUP_INDEX must be in [0, SHARD_GROUP_COUNT)" >&2
    exit 2
}
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
print(f"repair rounds={sorted(rounds)}; later rounds excluded")
PY

grep -qF 'linear_solvers = [str(solver).strip()' src/grid_data_factory/solvers/powermodels_adapter.py || {
    echo "Missing AC-OPF linear-solver fallback implementation." >&2
    exit 2
}
grep -qF 'request_payload = {"task": task, "options": self.options' src/grid_data_factory/solvers/powermodels_adapter.py || {
    echo "Persistent AC-OPF session does not pass fallback options." >&2
    exit 2
}
PYTHONPATH="$ROOT/src" "$PYBIN" -c 'import grid_data_factory, pydantic, pyarrow, yaml'

COINHSL_ENV=${COINHSL_ENV:-$HOME/.local/coinhsl/current/env.sh}
[[ -r "$COINHSL_ENV" ]] || { echo "Missing private Coin-HSL activation: $COINHSL_ENV" >&2; exit 2; }
source "$COINHSL_ENV"
[[ -r "${IPOPT_HSL_LIBRARY:-}" ]] || { echo "Missing Coin-HSL library" >&2; exit 2; }
case "$(readlink -f "$IPOPT_HSL_LIBRARY")" in
    "$ROOT"|"$ROOT"/*) echo "Coin-HSL must not be loaded from the shared repository" >&2; exit 2 ;;
esac

if [[ "$PREFLIGHT_ONLY" == "1" ]]; then
    echo "Riker repair preflight passed; no source or repair data was modified."
    exit 0
fi

if [[ ! -s "$PREPARE_MANIFEST" || ! -s "$CANDIDATES" ]]; then
    [[ -s "$SOURCE_LIST" ]] || {
        echo "Missing prepared source list: $SOURCE_LIST" >&2
        echo "Submit configs/slurm/riker_repair_prepare.sbatch before this compute job." >&2
        exit 2
    }
    mkdir -p "$PREPARE_PARTS_DIR"
    echo "[$(date -u +%FT%TZ)] parallel prepare: sources=$(wc -l < "$SOURCE_LIST") tasks=$SLURM_NTASKS"
    # Preserve completed parts when a task exhausts its transient-I/O retries.
    srun --ntasks="$SLURM_NTASKS" --nodes="$SLURM_JOB_NUM_NODES" --kill-on-bad-exit=0 bash -lc '
set -euo pipefail
ROOT="'$ROOT'"
PYBIN="'$PYBIN'"
PREPARE_SCRIPT="'$PREPARE_SCRIPT'"
SOURCE_LIST="'$SOURCE_LIST'"
PREPARE_PARTS_DIR="'$PREPARE_PARTS_DIR'"
CASES="'"$CASES"'"
ALL_CASES="'$ALL_CASES'"
PART_INDEX=${SLURM_PROCID:?}
PART_COUNT=${SLURM_NTASKS:?}
part=$(printf "%05d" "$PART_INDEX")
output="$PREPARE_PARTS_DIR/part_${part}.jsonl"
manifest="$PREPARE_PARTS_DIR/part_${part}.json"
if [[ -f "$output" && -s "$manifest" ]]; then
    exit 0
fi
args=(
    --source-list "$SOURCE_LIST"
    --part-index "$PART_INDEX"
    --part-count "$PART_COUNT"
    --output "$output"
    --manifest "$manifest"
)
if [[ "$ALL_CASES" == "1" ]]; then
    args+=(--all-cases)
else
    read -r -a case_array <<< "$CASES"
    args+=(--cases "${case_array[@]}")
fi
for attempt in 1 2 3 4 5; do
    if PYTHONPATH="$ROOT/src" "$PYBIN" "$PREPARE_SCRIPT" prepare-part "${args[@]}"; then
        exit 0
    fi
    rm -f "$output.in_progress" "$manifest.in_progress"
    echo "prepare-part $PART_INDEX failed on attempt $attempt/5" >&2
done
exit 1
'
    FINALIZE_ARGS=(
        --parts-dir "$PREPARE_PARTS_DIR"
        --part-count "$SLURM_NTASKS"
        --runs-root "$SOURCE_RUNS_ROOT"
        --rounds "$ROUNDS"
        --output "$CANDIDATES"
        --manifest "$PREPARE_MANIFEST"
    )
    if [[ "$ALL_CASES" == "1" ]]; then
        FINALIZE_ARGS+=(--all-cases)
    else
        read -r -a CASE_ARRAY <<< "$CASES"
        FINALIZE_ARGS+=(--cases "${CASE_ARRAY[@]}")
    fi
    PYTHONPATH="$ROOT/src" "$PYBIN" "$PREPARE_SCRIPT" finalize-prepare "${FINALIZE_ARGS[@]}"
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

mapfile -t ALL_SHARDS < <(find "$SHARD_DIR" -maxdepth 1 -name 'repair_*.jsonl' -size +0 -printf '%f\n' | sort)
if (( ${#ALL_SHARDS[@]} == 0 )); then
    echo "No failed target-case candidates found; nothing to repair."
    exit 0
fi
SHARDS=()
for ((index=SHARD_GROUP_INDEX; index<${#ALL_SHARDS[@]}; index+=SHARD_GROUP_COUNT)); do
    SHARDS+=("${ALL_SHARDS[$index]}")
done
SHARD_LIST="$ROOT/$WORK_ROOT/shards_group_${SHARD_GROUP_INDEX}_of_${SHARD_GROUP_COUNT}.txt"
printf '%s\n' "${SHARDS[@]}" > "$SHARD_LIST"

echo "[$(date -u +%FT%TZ)] repair_id=$REPAIR_ID rounds=$ROUNDS shards=${#SHARDS[@]} tasks=$SLURM_NTASKS group=$SHARD_GROUP_INDEX/$SHARD_GROUP_COUNT"
echo "[$(date -u +%FT%TZ)] source=$SOURCE_RUNS_ROOT isolated_retries=$REPAIR_RUNS_ROOT apply_merge=$APPLY_MERGE"
if [[ -n "$LINEAR_SOLVERS" ]]; then
    echo "[$(date -u +%FT%TZ)] solver_sequence=$LINEAR_SOLVERS"
else
    echo "[$(date -u +%FT%TZ)] solver_sequence=default,$LINEAR_SOLVER_FALLBACKS"
fi

srun --ntasks="$SLURM_NTASKS" --nodes="$SLURM_JOB_NUM_NODES" --signal=USR1@120 bash -lc '
set -euo pipefail
ROOT="'$ROOT'"
WORK_ROOT="'$WORK_ROOT'"
SHARD_DIR="'$SHARD_DIR'"
REPAIR_RUNS_ROOT="'$REPAIR_RUNS_ROOT'"
DONE_DIR="'$DONE_DIR'"
PROGRESS_DIR="'$PROGRESS_DIR'"
RUN_SCRIPT="'$RUN_SCRIPT'"
PYBIN="'$PYBIN'"
TIMEOUT_S="'$TIMEOUT_S'"
SOLVER_ID="'$SOLVER_ID'"
LINEAR_SOLVER_FALLBACKS="'$LINEAR_SOLVER_FALLBACKS'"
LINEAR_SOLVERS="'$LINEAR_SOLVERS'"
CAMPAIGN_PREFIX="'$CAMPAIGN_PREFIX'"
SHARD_LIST="'$SHARD_LIST'"
export PGDF_JULIA_BIN="'$PGDF_JULIA_BIN'" PGDF_JULIA_PROJECT_DIR="'$PGDF_JULIA_PROJECT_DIR'" JULIA_DEPOT_PATH="'$JULIA_DEPOT_PATH'"
export IPOPT_HSL_LIBRARY="'${IPOPT_HSL_LIBRARY:-}'" LD_LIBRARY_PATH="'${LD_LIBRARY_PATH:-}'" OPENBLAS_NUM_THREADS=1 JULIA_NUM_THREADS=1
TASK_ID=${SLURM_PROCID:-0}
TASK_COUNT=${SLURM_NTASKS:-1}
mapfile -t SHARDS < "$SHARD_LIST"
for ((index=TASK_ID; index<${#SHARDS[@]}; index+=TASK_COUNT)); do
    shard=${SHARDS[$index]}
    shard_number=${shard#repair_}
    shard_number=${shard_number%.jsonl}
    [[ -f "$DONE_DIR/$shard_number" ]] && continue
    SOLVER_ARGS=(--linear-solver-fallbacks "$LINEAR_SOLVER_FALLBACKS")
    if [[ -n "$LINEAR_SOLVERS" ]]; then
        SOLVER_ARGS=(--linear-solvers "$LINEAR_SOLVERS")
    fi
    PYTHONPATH="$ROOT/src" "$PYBIN" "$RUN_SCRIPT" \
        --campaign-id "${CAMPAIGN_PREFIX}__s${shard_number}" \
        --round-index 0 \
        --selected-candidates-jsonl "$SHARD_DIR/$shard" \
        --config configs/campaign_ultrascale_3b.yaml \
        --runs-root "$REPAIR_RUNS_ROOT/shard_${shard_number}" \
        --solver-id "$SOLVER_ID" \
        --timeout-s "$TIMEOUT_S" \
        --progress-file "$PROGRESS_DIR/shard_${shard_number}.json" \
        --retry-termination-statuses process_error,exception,server_exception \
        "${SOLVER_ARGS[@]}" \
        --continue-on-error \
        --max-failure-fraction 1.0 \
        --resume
    touch "$DONE_DIR/$shard_number"
done
'

if [[ "$RUN_MERGE" == "1" ]]; then
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
else
    echo "[$(date -u +%FT%TZ)] repair compute group complete; merge deferred"
fi