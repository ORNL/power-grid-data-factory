#!/bin/bash
# Create or update the Andes Python environment, including mpi4py verified
# against the active OpenMPI runtime.
set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
VENV=${PGDF_VENV:-$ROOT/.venv}
PYBIN=${PYBIN:-python3.11}

module load python/.3.11-anaconda3 2>/dev/null || true
module load openmpi 2>/dev/null || true

MPI_COMPILER=${MPICC:-$(command -v mpicc || true)}
if [[ -z "$MPI_COMPILER" || ! -x "$MPI_COMPILER" ]]; then
  echo "MPI compiler wrapper not found. Load the Andes OpenMPI module or set MPICC." >&2
  exit 2
fi

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "[setup] creating venv at $VENV using $PYBIN"
  "$PYBIN" -m venv "$VENV"
else
  echo "[setup] updating existing venv at $VENV"
fi

unset PYTHONUSERBASE PYTHONNOUSERSITE
"$VENV/bin/python" -m pip install --upgrade pip
# The mpi4py MPI-ABI wheel avoids Anaconda compiler_compat linker failures on
# Andes. The verification below ensures that it resolves to the loaded OpenMPI.
MPICC="$MPI_COMPILER" "$VENV/bin/python" -m pip install -e "$ROOT[analysis-mpi]"

echo "[setup] verifying Python analysis environment"
PYTHONPATH="$ROOT/src" "$VENV/bin/python" - <<'PY'
from mpi4py import MPI
import numpy
import pandas
import pyarrow
import yaml

print("numpy", numpy.__version__)
print("pandas", pandas.__version__)
print("pyarrow", pyarrow.__version__)
print("mpi4py", MPI.Get_version())
print(MPI.Get_library_version().strip())
PY

echo "[setup] complete: $VENV"