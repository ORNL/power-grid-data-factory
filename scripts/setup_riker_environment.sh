#!/bin/bash
# Install an isolated Python and Julia environment for PowerModels campaigns on Riker.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
JULIA_VERSION=${JULIA_VERSION:-1.10.10}
SOFTWARE_ROOT=${PGDF_SOFTWARE_ROOT:-$ROOT/.software}
JULIA_IMAGE="$SOFTWARE_ROOT/julia-$JULIA_VERSION.sif"
JULIA_WRAPPER="$SOFTWARE_ROOT/bin/julia"
VENV=${PGDF_VENV:-$ROOT/.venv-riker}
DEPOT=${JULIA_DEPOT_PATH:-$ROOT/.julia_depot_riker}
PROJECT=$ROOT/julia/lockfiles/riker

module load python/3.14.5

mkdir -p "$SOFTWARE_ROOT"
if [[ ! -f "$JULIA_IMAGE" ]]; then
  echo "[setup] pulling Julia $JULIA_VERSION Apptainer image"
  apptainer pull "$JULIA_IMAGE" "docker://julia:$JULIA_VERSION"
fi

mkdir -p "$(dirname "$JULIA_WRAPPER")"
cat > "$JULIA_WRAPPER" <<EOF
#!/bin/bash
set -euo pipefail
export APPTAINERENV_JULIA_DEPOT_PATH="\${JULIA_DEPOT_PATH:-}"
export APPTAINERENV_JULIA_PKG_PRECOMPILE_AUTO="\${JULIA_PKG_PRECOMPILE_AUTO:-0}"
exec apptainer exec --bind "$ROOT:$ROOT" "$JULIA_IMAGE" julia "\$@"
EOF
chmod +x "$JULIA_WRAPPER"

export PATH="$(dirname "$JULIA_WRAPPER"):$PATH"
export PGDF_JULIA_BIN="$JULIA_WRAPPER"
export JULIA_DEPOT_PATH="$DEPOT"
export JULIA_PKG_PRECOMPILE_AUTO=0

echo "[setup] creating Python environment at $VENV"
python -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r "$ROOT/requirements.txt"
"$VENV/bin/python" -m pip install -e "$ROOT"

echo "[setup] instantiating Julia environment at $PROJECT"
julia --project="$PROJECT" -e 'using Pkg; isempty(Pkg.Registry.reachable_registries()) && Pkg.Registry.add("General"); Pkg.resolve(); Pkg.instantiate(; allow_autoprecomp=false)'

echo "[setup] validating imports"
"$VENV/bin/python" -c 'import grid_data_factory, pandas, pyarrow, pydantic, yaml; print("PYTHON_OK")'
julia --project="$PROJECT" -e 'using JSON3, PowerModels, Ipopt; println("JULIA_OK")'

cat <<EOF
[setup] Riker environment is ready.
export PATH=$(dirname "$JULIA_WRAPPER"):\$PATH
export PGDF_VENV=$VENV
export PGDF_JULIA_BIN=$JULIA_WRAPPER
export PGDF_JULIA_PROJECT_DIR=$PROJECT
export JULIA_DEPOT_PATH=$DEPOT
EOF