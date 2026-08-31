#!/bin/bash
#SBATCH -J pgdf_exago_diag
#SBATCH -A LRN070
#SBATCH -p batch
#SBATCH -N 1
#SBATCH --ntasks-per-node=8
#SBATCH --cpus-per-task=7
#SBATCH --gpus-per-node=8
#SBATCH --gpu-bind=closest
#SBATCH -t 0:30:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
OPFLOW="${PGDF_EXAGO_OPFLOW_BIN:-$ROOT/external/ExaGO/install/bin/opflow}"
PGLIB="$ROOT/external/pglib-opf"
LOG_DIR="$ROOT/data/scratch/logs"
mkdir -p "$LOG_DIR"

# Same environment setup as the round job
export SRCDIR=$ROOT/external/ExaGO
if [[ -f "$SRCDIR/buildsystem/clang-hip/frontierVariables.sh" ]]; then
  set +eu
  source "$SRCDIR/buildsystem/clang-hip/frontierVariables.sh"
  set -eu
fi

echo "[$(date -u +%FT%TZ)] JOB=$SLURM_JOB_ID node=$(hostname) opflow=$OPFLOW"

run_case() {
    local label="$1" casefile="$2" ntasks="$3"
    local out="$LOG_DIR/diag_${label}_n${ntasks}_${SLURM_JOB_ID}.txt"
    echo ""
    echo "=== $label  n=$ntasks ==="
    if [[ ! -f "$casefile" ]]; then
        echo "ERROR: case file not found: $casefile" | tee "$out"
        return 1
    fi
    srun --overlap --exact -N1 -n"$ntasks" \
        --cpus-per-task=7 --gpus-per-task=1 --gpu-bind=closest \
        "$OPFLOW" \
        -netfile "$casefile" \
        -opflow_solver IPOPT \
        -opflow_model POWER_BALANCE_POLAR \
        -print_output 1 \
        -hiop_verbosity_level 3 \
        -options_left no \
        > "$out" 2>&1 || true
    echo "[$(date -u +%FT%TZ)] exit_code=$?  log=$out"
    tail -40 "$out"
}

# n=1 to isolate whether MPI decomposition is the cause
run_case "epigrids_midwest_10192" "$PGLIB/pglib_opf_case10192_epigrids.m" 1
run_case "epigrids_western_20758" "$PGLIB/pglib_opf_case20758_epigrids.m" 1
run_case "epigrids_eastern_78484" "$PGLIB/pglib_opf_case78484_epigrids.m" 1
# Control: a working epigrids case at n=1
run_case "epigrids_florida_5658"  "$PGLIB/pglib_opf_case5658_epigrids.m"  1

echo ""
echo "[$(date -u +%FT%TZ)] diagnostic job $SLURM_JOB_ID complete"
