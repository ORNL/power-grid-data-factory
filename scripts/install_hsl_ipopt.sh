#!/usr/bin/env bash
# install_hsl_ipopt.sh — Build CoinHSL from source and wire it into the project's
# Julia/Ipopt stack so MA27 and MA57 are available to PowerModels solves.
#
# Usage on Andes:
#   bash scripts/install_hsl_ipopt.sh [coinhsl-YYYY.MM.DD.tar.gz] [ma27|ma57]
#
# The HSL source tarball requires a free academic licence:
#   https://licences.stfc.ac.uk/product/coin-hsl
#
# What this script does:
#   1. Extracts and compiles libcoinhsl.so using Meson, gfortran, and gcc
#   2. Installs to external/coinhsl/lib/  (project-local, stable path)
#   3. Creates libhsl.so, the default runtime name used by Ipopt
#   4. Generates external/coinhsl/env.sh for PowerModels jobs
#   5. Solves a nonlinear model with both MA27 and MA57
#
# ExaGO is intentionally not modified by this installer.

set -euo pipefail

# ── arguments ─────────────────────────────────────────────────────────────────
HSL_TARBALL="${1:-${HSL_TARBALL:-$HOME/coinhsl-2023.11.17.tar.gz}}"
SOLVER="${2:-ma57}"   # ma27 or ma57

if [[ ! -f "$HSL_TARBALL" ]]; then
    echo "ERROR: HSL tarball not found: $HSL_TARBALL" >&2
    exit 1
fi

case "$SOLVER" in
    ma27|ma57) ;;
    *) echo "ERROR: solver must be 'ma27' or 'ma57', got: $SOLVER" >&2; exit 1 ;;
esac

# ── paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="$ROOT/external/coinhsl"
BUILD_DIR="$ROOT/external/coinhsl_build"
BUILD_TOOLS_DIR="$ROOT/external/coinhsl_build_tools"
LIB_DIR="$INSTALL_DIR/lib"

echo "================================================================"
echo "  CoinHSL/Ipopt install for project at $ROOT"
echo "  Tarball  : $HSL_TARBALL"
echo "  Solver   : $SOLVER"
echo "  Install  : $INSTALL_DIR"
echo "================================================================"

# ── toolchain ─────────────────────────────────────────────────────────────────
check_tool() {
    if ! command -v "$1" &>/dev/null; then
        echo "ERROR: required tool not found in PATH: $1" >&2
        exit 1
    fi
}
module load gcc/9.3.0 2>/dev/null || true
module load julia/1.8.2 2>/dev/null || true

check_tool gfortran
check_tool gcc

BUILD_PYTHON=${HSL_BUILD_PYTHON:-$ROOT/.venv/bin/python}
if [[ ! -x "$BUILD_PYTHON" ]]; then
    BUILD_PYTHON=$(command -v python3.11 2>/dev/null || true)
fi
if [[ -z "$BUILD_PYTHON" || ! -x "$BUILD_PYTHON" ]]; then
    echo "ERROR: Python 3.11 is required to install current Meson/Ninja build tools." >&2
    exit 1
fi

GFORTRAN_VER=$(gfortran --version | head -1)
echo "Using: $GFORTRAN_VER"

# ── Julia binary ──────────────────────────────────────────────────────────────
JULIA_BIN="${JULIA_BIN:-$(command -v julia 2>/dev/null || true)}"
if [[ -z "$JULIA_BIN" ]]; then
    # Fall back to known Andes location
    JULIA_BIN=/sw/andes/spack-envs/base/opt/linux-rhel8-x86_64/gcc-8.3.1/julia-1.8.2-qdclp4ykfk65oo4m3rzxuymz3igonngd/bin/julia
fi
if [[ ! -x "$JULIA_BIN" ]]; then
    echo "WARNING: Julia binary not found; skipping smoke test." >&2
    JULIA_BIN=""
fi

JULIA_DEPOT="${JULIA_DEPOT_PATH:-$ROOT/.julia_depot_andes_profile}"
OPENBLAS_LIB=""
while IFS= read -r candidate; do
    if nm -D "$candidate" 2>/dev/null | grep ' dgemm_$' >/dev/null; then
        OPENBLAS_LIB="$candidate"
        break
    fi
done < <(find "$JULIA_DEPOT/artifacts" -path '*/lib/libopenblas.so' -type f -o -path '*/lib/libopenblas.so' -type l 2>/dev/null | sort)
if [[ -z "$OPENBLAS_LIB" ]]; then
    echo "ERROR: no installed Julia OpenBLAS artifact exposes the required LP64 BLAS ABI." >&2
    echo "Instantiate the Andes Julia project before installing Coin-HSL." >&2
    exit 1
fi
OPENBLAS_LIB=$(readlink -f "$OPENBLAS_LIB")
OPENBLAS_LIB_DIR=$(dirname "$OPENBLAS_LIB")
echo "Using OpenBLAS32: $OPENBLAS_LIB_DIR/libopenblas.so"

# Keep build tooling isolated from the project's runtime virtual environment.
BUILD_TOOLS_PYTHON_OK=0
if [[ -x "$BUILD_TOOLS_DIR/bin/python" ]] && "$BUILD_TOOLS_DIR/bin/python" -c 'import sys; raise SystemExit(sys.version_info < (3, 9))'; then
    BUILD_TOOLS_PYTHON_OK=1
fi
if [[ "$BUILD_TOOLS_PYTHON_OK" != "1" || ! -x "$BUILD_TOOLS_DIR/bin/meson" || ! -x "$BUILD_TOOLS_DIR/bin/ninja" ]]; then
    echo "--- Installing Meson build tools into $BUILD_TOOLS_DIR ---"
    rm -rf "$BUILD_TOOLS_DIR"
    "$BUILD_PYTHON" -m venv "$BUILD_TOOLS_DIR"
    "$BUILD_TOOLS_DIR/bin/python" -m pip install --upgrade meson ninja
fi
MESON="$BUILD_TOOLS_DIR/bin/meson"
export PATH="$BUILD_TOOLS_DIR/bin:$PATH"

# ── build ─────────────────────────────────────────────────────────────────────
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

echo ""
echo "--- Extracting HSL tarball ---"
tar -xzf "$HSL_TARBALL" -C "$BUILD_DIR"

# The tarball typically contains a single top-level directory
HSL_SRC_DIR=$(find "$BUILD_DIR" -mindepth 1 -maxdepth 1 -type d | head -1)
if [[ -z "$HSL_SRC_DIR" ]]; then
    echo "ERROR: could not find source directory after extracting tarball." >&2
    exit 1
fi
echo "Source dir: $HSL_SRC_DIR"

if [[ ! -f "$HSL_SRC_DIR/meson.build" ]]; then
    echo "ERROR: expected meson.build inside the Coin-HSL tarball." >&2
    echo "  Check that the tarball is the CoinHSL academic package from STFC." >&2
    exit 1
fi

rm -rf "$INSTALL_DIR"

echo ""
echo "--- Configuring ---"
CC=gcc FC=gfortran "$MESON" setup "$HSL_SRC_DIR/build" "$HSL_SRC_DIR" \
    --buildtype=release \
    --prefix="$INSTALL_DIR" \
    --libdir=lib \
    -Dmodules=false \
    -Dlibblas=openblas \
    -Dliblapack=openblas \
    -Dlibblas_path="$OPENBLAS_LIB_DIR" \
    -Dliblapack_path="$OPENBLAS_LIB_DIR"

echo ""
echo "--- Building ($(nproc) threads) ---"
"$MESON" compile -C "$HSL_SRC_DIR/build" -j "$(nproc)"

echo ""
echo "--- Installing to $INSTALL_DIR ---"
"$MESON" install -C "$HSL_SRC_DIR/build"

# ── verify the library was produced ──────────────────────────────────────────
COINHSL_LIB="$LIB_DIR/libcoinhsl.so"
if [[ ! -f "$COINHSL_LIB" ]]; then
    # Some HSL versions name the output differently
    COINHSL_LIB=$(find "$LIB_DIR" -name 'libcoinhsl*' -name '*.so*' | head -1 || true)
    if [[ -z "$COINHSL_LIB" ]]; then
        echo "ERROR: libcoinhsl.so not found under $LIB_DIR after install." >&2
        echo "  Contents:" >&2
        ls -la "$LIB_DIR" >&2 || true
        exit 1
    fi
fi
echo ""
echo "Built: $COINHSL_LIB"

# Create canonical loader names. Ipopt searches for libhsl.so by default.
CANONICAL="$LIB_DIR/libcoinhsl.so"
if [[ ! -f "$CANONICAL" ]]; then
    ln -sf "$(basename "$COINHSL_LIB")" "$CANONICAL"
fi
ln -sfn "$(basename "$CANONICAL")" "$LIB_DIR/libhsl.so"

# ── generate env activation snippet ──────────────────────────────────────────
cat > "$INSTALL_DIR/env.sh" <<EOF
# Source this file only for Julia PowerModels jobs.
# Generated by scripts/install_hsl_ipopt.sh
export LD_LIBRARY_PATH="$LIB_DIR:$OPENBLAS_LIB_DIR\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
export IPOPT_LINEAR_SOLVER="\${POWER_MODELS_LINEAR_SOLVER:-$SOLVER}"
EOF

echo ""
echo "--- Environment snippet written to $INSTALL_DIR/env.sh ---"
echo "  source $INSTALL_DIR/env.sh"

# ── clean build tree ─────────────────────────────────────────────────────────
rm -rf "$BUILD_DIR"
echo ""
echo "Cleaned build dir."

# ── Julia smoke test ─────────────────────────────────────────────────────────
if [[ -z "$JULIA_BIN" ]]; then
    echo ""
    echo "Skipping Julia smoke test (Julia not found)."
    echo ""
    echo "Run manually after sourcing the env:"
    echo "  source $INSTALL_DIR/env.sh"
    echo "  rerun this installer with JULIA_BIN set to an executable Julia path"
    exit 0
fi

echo ""
echo "--- Julia smoke tests (MA27 and MA57) ---"
JULIA_PROJECT="${PGDF_JULIA_PROJECT_DIR:-$ROOT/julia/lockfiles/andes}"
for smoke_solver in ma27 ma57; do
    SMOKE_RESULT=$(
        LD_LIBRARY_PATH="$LIB_DIR:$OPENBLAS_LIB_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
        JULIA_DEPOT_PATH="$JULIA_DEPOT" \
            "$JULIA_BIN" \
                --project="$JULIA_PROJECT" \
                -e "using Ipopt
eval_f(x) = (x[1] - 1.0)^2
eval_g(x, g) = nothing
eval_grad_f(x, grad) = (grad[1] = 2.0 * (x[1] - 1.0); nothing)
eval_jac_g(x, rows, cols, values) = nothing
function eval_h(x, rows, cols, obj_factor, lambda, values)
    if values === nothing
        rows[1] = 1
        cols[1] = 1
    else
        values[1] = 2.0 * obj_factor
    end
end
prob = Ipopt.CreateIpoptProblem(1, [-10.0], [10.0], 0, Float64[], Float64[], 0, 1, eval_f, eval_g, eval_grad_f, eval_jac_g, eval_h)
prob.x = [2.0]
Ipopt.AddIpoptIntOption(prob, \"print_level\", 0)
Ipopt.AddIpoptStrOption(prob, \"linear_solver\", \"$smoke_solver\")
status = Ipopt.IpoptSolve(prob)
status == 0 || error(\"$smoke_solver solve failed with status \" * string(status))
abs(prob.x[1] - 1.0) < 1e-6 || error(\"$smoke_solver returned x=\" * string(prob.x[1]))
println(\"${smoke_solver^^}_SMOKE_OK\")" 2>&1
    )
    if echo "$SMOKE_RESULT" | grep -q "${smoke_solver^^}_SMOKE_OK"; then
        echo "PASSED: $smoke_solver solved the nonlinear smoke model."
    else
        echo "FAILED: smoke test did not confirm $smoke_solver." >&2
        echo "$SMOKE_RESULT" >&2
        exit 1
    fi
done

echo ""
echo "================================================================"
echo "  HSL install complete."
echo ""
echo "  Library : $CANONICAL"
echo "  Solver  : $SOLVER"
echo ""
echo "  To activate in your shell:"
echo "    source $INSTALL_DIR/env.sh"
echo ""
echo "  To activate in an Andes PowerModels job:"
echo "    sbatch --export=ALL,POWER_MODELS_LINEAR_SOLVER=$SOLVER ..."
echo ""
echo "  The project's run_opf.jl reads IPOPT_LINEAR_SOLVER from the"
echo "  environment and passes it to Ipopt automatically."
echo "  ExaGO configuration is unchanged."
echo "================================================================"
