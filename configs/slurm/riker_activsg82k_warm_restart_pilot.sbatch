#!/bin/bash
#SBATCH -J pgdf_82k_warm_pilot
#SBATCH -A LRN116
#SBATCH -p batch
#SBATCH --qos=normal
#SBATCH -N 1
#SBATCH --ntasks-per-node=40
#SBATCH --cpus-per-task=1
#SBATCH -t 36:00:00
#SBATCH -o /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

# Warm-restart pilot for activsg82k ITERATION_LIMIT records of the independent
# activsg_hsl_full_3b campaign. Read-only on its runs root; writes to PILOT_RUNS_ROOT.
# Arm A: primal warm start, Ipopt defaults. Arm B: primal warm start, small mu_init/bound_push.

set -euo pipefail
ulimit -c 0

ROOT=/lustre/orion/lrn116/proj-shared/mlupopa/OPF/power_grid_data_factory
cd "$ROOT"
SOURCE_RUNS_ROOT=${SOURCE_RUNS_ROOT:-data/outputs/runs_3b_activsg_hsl_full}
PILOT_RUNS_ROOT=${PILOT_RUNS_ROOT:-data/outputs/runs_activsg82k_warm_pilot}
PILOT_ID=${PILOT_ID:-activsg82k_warm_pilot}
PILOT_COUNT=${PILOT_COUNT:-20}
SOURCE_SHARDS=${SOURCE_SHARDS:-"00000 00064 00128 00192 00256 00300 00384 00448 00512 00576 00640 00700 00768 00832 00900 00960 01023"}
MAX_ITER=${MAX_ITER:-10000}
TIMEOUT_S=${TIMEOUT_S:-122400}
LINEAR_SOLVER=${LINEAR_SOLVER:-ma57}
ARM_B_OPTIONS=${ARM_B_OPTIONS:-"mu_init=1e-4 bound_push=1e-6 bound_frac=1e-6"}

export PGDF_JULIA_BIN=${PGDF_JULIA_BIN:-$ROOT/.software/bin/julia}
export PGDF_JULIA_PROJECT_DIR=${PGDF_JULIA_PROJECT_DIR:-$ROOT/julia/lockfiles/riker}
export PGDF_JULIA_SYSIMAGE=${PGDF_JULIA_SYSIMAGE:-$ROOT/julia/sysimages/riker/pgdf_sysimage.so}
export JULIA_DEPOT_PATH=${JULIA_DEPOT_PATH:-$ROOT/.julia_depot_riker}
export OPENBLAS_NUM_THREADS=1 JULIA_NUM_THREADS=1
unset PYTHONUSERBASE PYTHONNOUSERSITE IPOPT_LINEAR_SOLVER
PYBIN=${PYBIN:-$ROOT/.venv-riker/bin/python}
COINHSL_ENV=${COINHSL_ENV:-$HOME/.local/coinhsl/riker/env.sh}
[[ -r "$COINHSL_ENV" ]] || { echo "Missing Coin-HSL activation: $COINHSL_ENV" >&2; exit 2; }
source "$COINHSL_ENV"
[[ -r "${IPOPT_HSL_LIBRARY:-}" ]] || { echo "Missing Coin-HSL library" >&2; exit 2; }

PILOT_DIR="$ROOT/$PILOT_RUNS_ROOT"
SELECTED="$PILOT_DIR/selected_candidates.jsonl"
mkdir -p "$PILOT_DIR/inputs"

if [[ ! -s "$SELECTED" ]]; then
  SAMPLE_ARGS=()
  for s in $SOURCE_SHARDS; do
    SAMPLE_ARGS+=(--samples-path "mapreduce_round_000/shard_${s}/ac_opf/samples.jsonl")
  done
  PYTHONPATH="$ROOT/src" "$PYBIN" scripts/prepare_powermodels_restart_campaign.py \
    --source-runs-root "$SOURCE_RUNS_ROOT" \
    --termination-status ITERATION_LIMIT \
    --case-id activsg82k \
    --limit "$PILOT_COUNT" \
    "${SAMPLE_ARGS[@]}" \
    --output "$SELECTED"
fi
N=$(wc -l < "$SELECTED")
(( N > 0 )) || { echo "No activsg82k ITERATION_LIMIT candidates found" >&2; exit 2; }
(( 2 * N <= SLURM_NTASKS )) || { echo "Need $((2 * N)) tasks, have $SLURM_NTASKS" >&2; exit 2; }
split -l 1 -d -a 3 "$SELECTED" "$PILOT_DIR/inputs/cand_"
echo "[$(date -u +%FT%TZ)] JOB=$SLURM_JOB_ID candidates=$N max_iter=$MAX_ITER timeout_s=$TIMEOUT_S linear_solver=$LINEAR_SOLVER arm_b='$ARM_B_OPTIONS'"

export ROOT PILOT_RUNS_ROOT PILOT_DIR PILOT_ID N MAX_ITER TIMEOUT_S LINEAR_SOLVER ARM_B_OPTIONS PYBIN
srun --ntasks=$((2 * N)) --signal=USR1@120 bash -c '
set -euo pipefail
ulimit -c 0
t=${SLURM_PROCID}
idx=$(( t % N )); arm=$([[ $t -lt $N ]] && echo A || echo B)
printf -v pad "%03d" "$idx"
EXTRA=()
if [[ $arm == B ]]; then
  for opt in $ARM_B_OPTIONS; do EXTRA+=(--ipopt-option "$opt"); done
fi
PYTHONPATH="$ROOT/src" "$PYBIN" scripts/run_campaign_ac_opf_round.py \
  --campaign-id "${PILOT_ID}__arm${arm}__c${pad}" \
  --round-index 0 \
  --selected-candidates-jsonl "$PILOT_DIR/inputs/cand_${pad}" \
  --runs-root "$PILOT_RUNS_ROOT/arm_${arm}/cand_${pad}" \
  --timeout-s "$TIMEOUT_S" \
  --linear-solvers "$LINEAR_SOLVER" \
  --max-iter "$MAX_ITER" \
  --continue-on-error --max-failure-fraction 1.0 \
  --resume \
  "${EXTRA[@]}"
'
echo "[$(date -u +%FT%TZ)] pilot complete"
