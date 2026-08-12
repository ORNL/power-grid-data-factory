#!/bin/bash
#SBATCH -A lrn087
#SBATCH -J exago-camp-verbose
#SBATCH -N 1
#SBATCH -n 1
#SBATCH --gpus=1
#SBATCH -t 00:30:00

set -euo pipefail

ROOT="/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory"
cd "$ROOT"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

# Defense in depth: no core dumps from the worker or its srun tasks.
ulimit -c 0 || true

# Same runtime env as the single-case smoke test.
export SRCDIR="$ROOT/external/ExaGO"
export EXTRA_CMAKE_ARGS="${EXTRA_CMAKE_ARGS:-}"
set +u
source "$ROOT/external/ExaGO/buildsystem/clang-hip/frontierVariables.sh"
set -u
export LD_LIBRARY_PATH="/opt/rocm-6.3.1/lib:${LD_LIBRARY_PATH:-}"

# Enable maximum ExaGO verbosity + per-shard solver_verbose.log.
export PGDF_EXAGO_VERBOSE=1
# opflow is MPI-linked; launch each solve as its own single-task GPU step.
export PGDF_EXAGO_SRUN_PREFIX="srun --overlap --exact -N1 -n1 -c7 --gpus-per-task=1 --gpu-bind=closest"

RUNS_ROOT="$ROOT/data/outputs/campaigns/verbose_smoke/runs_robustness"
mkdir -p "$RUNS_ROOT"

python3.11 scripts/run_campaign_exago_ac_opf_round.py \
  --campaign-id verbose_smoke \
  --round-index 0 \
  --selected-candidates-jsonl data/outputs/campaigns/verbose_smoke/selected_robustness.jsonl \
  --runs-root data/outputs/campaigns/verbose_smoke/runs_robustness \
  --max-candidates 3 \
  --solver-mode gpu_then_ipopt \
  --continue-on-error

echo "=== solver_verbose.log tail ==="
tail -n 40 "$RUNS_ROOT/ac_opf/solver_verbose.log" 2>/dev/null || echo "(no solver_verbose.log found)"

echo "=== core-dump audit (expect NONE) ==="
find "$ROOT" "$ROOT/external/ExaGO" -maxdepth 1 \( -name core -o -name 'gpucore.*' \) -printf '%p %s\n' 2>/dev/null || true
echo "=== end audit ==="
