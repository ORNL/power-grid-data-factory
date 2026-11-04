#!/usr/bin/env bash
# install_hsl_ipopt.sh — Build CoinHSL from source and wire it into the project's
# Julia/Ipopt stack so MA27 and MA57 are available to PowerModels solves.
#
# Usage on Riker:
#   bash scripts/install_hsl_ipopt.sh [coinhsl-YYYY.MM.DD.tar.gz] [ma27|ma57]
#
# The HSL source tarball requires a free academic licence:
#   https://licences.stfc.ac.uk/product/coin-hsl
#
# What this script does:
#   1. Extracts and compiles libcoinhsl.so using Meson, gfortran, and gcc
#   2. Installs privately to ~/.local/coinhsl/<version>/install
#   3. Creates libhsl.so, the default runtime name used by Ipopt
#   4. Generates a private env.sh for PowerModels jobs
#   5. Solves a nonlinear model with both MA27 and MA57
#
# ExaGO is intentionally not modified by this installer.

set -euo pipefail
umask 077

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
ARCHIVE_NAME=$(basename "$HSL_TARBALL")
VERSION=$(printf '%s\n' "$ARCHIVE_NAME" | sed -E 's/^coinhsl-([0-9.]+)\.tar\.gz$/\1/')
if [[ "$VERSION" == "$ARCHIVE_NAME" ]]; then
    echo "ERROR: expected archive name coinhsl-YYYY.MM.DD.tar.gz, got: $ARCHIVE_NAME" >&2
    exit 1
fi
PRIVATE_ROOT="${COINHSL_INSTALL_ROOT:-$HOME/.local/coinhsl}"
VERSION_DIR="$PRIVATE_ROOT/$VERSION"
INSTALL_DIR="$VERSION_DIR/install"
BUILD_DIR="$VERSION_DIR/build"
BUILD_TOOLS_DIR="$PRIVATE_ROOT/build-tools"
LIB_DIR="$INSTALL_DIR/lib64"

case "$INSTALL_DIR" in
    "$ROOT"|"$ROOT"/*)
        echo "ERROR: Coin-HSL must be installed in private storage, not under the shared repository." >&2
        exit 1
        ;;
esac
if [[ -e "$VERSION_DIR" ]]; then
    echo "ERROR: private version directory already exists: $VERSION_DIR" >&2
    echo "Remove it explicitly before reinstalling this version." >&2
    exit 1
fi

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
check_tool gfortran
check_tool gcc

BUILD_PYTHON=${HSL_BUILD_PYTHON:-$ROOT/.venv-riker/bin/python}
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
JULIA_BIN="${PGDF_JULIA_BIN:-$ROOT/.software/bin/julia}"
if [[ ! -x "$JULIA_BIN" ]]; then
    echo "WARNING: Julia binary not found; skipping smoke test." >&2
    JULIA_BIN=""
fi

JULIA_DEPOT="${JULIA_DEPOT_PATH:-$ROOT/.julia_depot_riker}"
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

echo ""
echo "--- Configuring ---"
CC=gcc FC=gfortran "$MESON" setup "$HSL_SRC_DIR/build" "$HSL_SRC_DIR" \
    --buildtype=release \
    --prefix="$INSTALL_DIR" \
    --libdir=lib64 \
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
cat > "$VERSION_DIR/env.sh" <<EOF
# Source this file only for Julia PowerModels jobs.
# Generated by scripts/install_hsl_ipopt.sh
export COINHSL_ROOT="$INSTALL_DIR"
export COINHSL_LIBDIR="$LIB_DIR"
export COINHSL_OPENBLAS_DIR="$OPENBLAS_LIB_DIR"
export LD_LIBRARY_PATH="\$COINHSL_LIBDIR:\$COINHSL_OPENBLAS_DIR\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
export IPOPT_LINEAR_SOLVER="\${IPOPT_LINEAR_SOLVER:-$SOLVER}"
export IPOPT_HSL_LIBRARY="\$COINHSL_LIBDIR/libcoinhsl.so"
EOF
chmod 600 "$VERSION_DIR/env.sh"

echo ""
echo "--- Environment snippet written to $VERSION_DIR/env.sh ---"
echo "  source $VERSION_DIR/env.sh"

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
    echo "  $JULIA_BIN --project=\$JULIA_PROJECT -e '"
    echo "    using JuMP, Ipopt"
    echo "    m = Model(Ipopt.Optimizer)"
    echo "    set_optimizer_attribute(m, \"print_level\", 0)"
    echo "    set_optimizer_attribute(m, \"linear_solver\", \"$SOLVER\")"
    echo "    println(\"${SOLVER^^}_SMOKE_OK\")'"
    exit 0
fi

echo ""
echo "--- Julia smoke tests (MA27 and MA57) ---"
JULIA_PROJECT="${PGDF_JULIA_PROJECT_DIR:-$ROOT/julia/lockfiles/riker}"
for smoke_solver in ma27 ma57; do
    SMOKE_RESULT=$(
        LD_LIBRARY_PATH="$LIB_DIR:$OPENBLAS_LIB_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
        JULIA_DEPOT_PATH="$JULIA_DEPOT" \
            "$JULIA_BIN" \
                --project="$JULIA_PROJECT" \
                -e "using JuMP, Ipopt
m = Model(Ipopt.Optimizer)
set_silent(m)
set_optimizer_attribute(m, \"linear_solver\", \"$smoke_solver\")
set_optimizer_attribute(m, \"hsllib\", \"$CANONICAL\")
@variable(m, x >= 0.0, start = 2.0)
@NLobjective(m, Min, (x - 1.0)^2)
optimize!(m)
is_solved_and_feasible(m) || error(\"$smoke_solver solve failed: \" * string(termination_status(m)))
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

ln -sfn "$VERSION" "$PRIVATE_ROOT/current"

echo ""
echo "================================================================"
echo "  HSL install complete."
echo ""
echo "  Library : $CANONICAL"
echo "  Solver  : $SOLVER"
echo ""
echo "  To activate in your shell:"
echo "    source $PRIVATE_ROOT/current/env.sh"
echo ""
echo "  To select the other solver after activation:"
echo "    export IPOPT_LINEAR_SOLVER=ma27"
echo ""
echo "  The Julia runners pass IPOPT_LINEAR_SOLVER and"
echo "  IPOPT_HSL_LIBRARY to Ipopt automatically."
echo "  ExaGO configuration is unchanged."
echo "================================================================"
