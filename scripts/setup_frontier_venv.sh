#!/bin/bash
# Build the canonical campaign virtualenv (pandas+pyarrow) on Frontier so every
# ExaGO campaign writes uniform parquet ledgers. Run once from a login node.
#
#   bash scripts/setup_frontier_venv.sh
#
# Override the target location with PGDF_VENV=/path bash scripts/setup_frontier_venv.sh
set -euo pipefail

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
VENV=${PGDF_VENV:-$ROOT/.venv}
REQ=$ROOT/requirements.txt

# Match the runtime interpreter used by the sbatch (module cray-python).
export SRCDIR=$ROOT/external/ExaGO
if [[ -f "$SRCDIR/buildsystem/clang-hip/frontier/frontierExago.sh" ]]; then
  # base.sh references unset vars (e.g. EXTRA_CMAKE_ARGS); relax -e/-u to source.
  set +eu
  # shellcheck disable=SC1091
  source "$SRCDIR/buildsystem/clang-hip/frontier/frontierExago.sh"
  set -eu
fi

PYBIN=${PYBIN:-python3.11}

echo "[setup] creating venv at $VENV using $PYBIN"
"$PYBIN" -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"

python -m pip install --upgrade pip
python -m pip install -r "$REQ"

echo "[setup] verifying parquet engine"
python - <<'PY'
import pandas, pyarrow
print("pandas", pandas.__version__, "pyarrow", pyarrow.__version__)
PY

echo "[setup] done. The sbatch defaults PGDF_VENV to $VENV and will use it automatically."
