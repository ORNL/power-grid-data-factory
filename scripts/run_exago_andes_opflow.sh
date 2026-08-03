#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

EXAGO_ROOT="${EXAGO_ROOT:-$REPO_ROOT/external/ExaGO-andes-cpu-latest}"
OPFLOW_BIN="${OPFLOW_BIN:-$EXAGO_ROOT/builds/andes-cpu-latest/install/bin/opflow}"
DEFAULT_NETFILE="$EXAGO_ROOT/datafiles/case9/case9mod.m"

if [[ ! -x "$OPFLOW_BIN" ]]; then
  echo "error: opflow binary not found or not executable: $OPFLOW_BIN" >&2
  echo "hint: build/install ExaGO first for profile andes-cpu-latest." >&2
  exit 1
fi

if ! command -v module >/dev/null 2>&1; then
  if [[ -f /etc/profile.d/modules.sh ]]; then
    # shellcheck disable=SC1091
    source /etc/profile.d/modules.sh
  elif [[ -f /usr/share/lmod/lmod/init/bash ]]; then
    # shellcheck disable=SC1091
    source /usr/share/lmod/lmod/init/bash
  fi
fi

if ! command -v module >/dev/null 2>&1; then
  echo "error: environment modules command is unavailable in this shell." >&2
  exit 1
fi

module purge
module use /sw/andes/spack-envs/modules/gcc/14.2.0
module load gcc-14.2.0/openmpi/5.0.5 openmpi-5.0.5/gcc-14.2.0/petsc/3.22.1-mpi

if [[ $# -eq 0 ]]; then
  set -- \
    -netfile "$DEFAULT_NETFILE" \
    -opflow_solver IPOPT \
    -opflow_model POWER_BALANCE_POLAR \
    -print_output 1
fi

echo "Running: $OPFLOW_BIN $*"
exec "$OPFLOW_BIN" "$@"
