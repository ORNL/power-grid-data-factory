#!/usr/bin/env bash
# install_hsl_ipopt.sh — Build CoinHSL from source and wire it into the project's
# Julia/Ipopt stack so MA27 and MA57 are available to PowerModels solves.
#
# Usage:
#   bash scripts/install_hsl_ipopt.sh /path/to/coinhsl-YYYY.MM.DD.tar.gz [ma27|ma57]
#
# The HSL source tarball requires a free academic licence:
#   https://licences.stfc.ac.uk/product/coin-hsl
#
# What this script does:
#   1. Extracts and compiles libcoinhsl.so using gfortran/gcc
#   2. Installs to external/coinhsl/lib/  (project-local, stable path)
#   3. Copies libcoinhsl.so alongside the active Julia Ipopt_jll libipopt.so
#      so Ipopt finds it via dlopen without requiring LD_LIBRARY_PATH changes
#   4. Generates external/coinhsl/env.sh  (source this to add the lib to the path)
#   5. Runs a Julia smoke test to confirm the chosen solver loads correctly
#
# After a successful run, set IPOPT_LINEAR_SOLVER=ma57 (or ma27) in the
# environment before launching a campaign.  The project's run_opf.jl and
# run_opf_expansion.jl read that variable automatically.

set -euo pipefail

# ── arguments ─────────────────────────────────────────────────────────────────
HSL_TARBALL="${1:-}"
SOLVER="${2:-ma57}"   # ma27 or ma57

if [[ -z "$HSL_TARBALL" ]]; then
    echo "Usage: bash scripts/install_hsl_ipopt.sh /path/to/coinhsl-*.tar.gz [ma27|ma57]" >&2
    echo ""
    echo "  Obtain the CoinHSL tarball from:" >&2
    echo "    https://licences.stfc.ac.uk/product/coin-hsl" >&2
    exit 1
fi

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
check_tool gfortran
check_tool gcc
check_tool make

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

# ── resolve Julia's active libipopt.so directory ──────────────────────────────
JULIA_IPOPT_LIBDIR=""
if [[ -n "$JULIA_BIN" ]]; then
    JULIA_PROJECT="${PGDF_JULIA_PROJECT_DIR:-$ROOT/julia/lockfiles/andes}"
    JULIA_DEPOT="${JULIA_DEPOT_PATH:-$ROOT/.julia_depot_andes_profile}"
    JULIA_IPOPT_LIBDIR=$(
        JULIA_DEPOT_PATH="$JULIA_DEPOT" "$JULIA_BIN" \
            --project="$JULIA_PROJECT" \
            -e 'using Ipopt; println(dirname(Ipopt.libipopt))' 2>/dev/null || true
    )
    if [[ -n "$JULIA_IPOPT_LIBDIR" && -d "$JULIA_IPOPT_LIBDIR" ]]; then
        echo "Julia Ipopt lib dir : $JULIA_IPOPT_LIBDIR"
    else
        echo "WARNING: could not determine Julia Ipopt lib dir; will rely on LD_LIBRARY_PATH" >&2
        JULIA_IPOPT_LIBDIR=""
    fi
fi

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

if [[ ! -f "$HSL_SRC_DIR/configure" ]]; then
    echo "ERROR: expected an autoconf configure script inside the HSL tarball." >&2
    echo "  Check that the tarball is the CoinHSL academic package from STFC." >&2
    exit 1
fi

mkdir -p "$LIB_DIR"

echo ""
echo "--- Configuring ---"
pushd "$HSL_SRC_DIR" >/dev/null
./configure \
    --prefix="$INSTALL_DIR" \
    --enable-shared \
    --disable-static \
    CC=gcc \
    F77=gfortran \
    FC=gfortran \
    CFLAGS="-O2 -fPIC -fno-common" \
    FFLAGS="-O2 -fPIC -fno-common" \
    FCFLAGS="-O2 -fPIC -fno-common"

echo ""
echo "--- Building ($(nproc) threads) ---"
make -j"$(nproc)"

echo ""
echo "--- Installing to $INSTALL_DIR ---"
make install
popd >/dev/null

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

# Create a canonical symlink libcoinhsl.so -> actual versioned name
CANONICAL="$LIB_DIR/libcoinhsl.so"
if [[ ! -f "$CANONICAL" ]]; then
    ln -sf "$(basename "$COINHSL_LIB")" "$CANONICAL"
fi

# ── copy alongside Julia's libipopt.so ───────────────────────────────────────
if [[ -n "$JULIA_IPOPT_LIBDIR" && -d "$JULIA_IPOPT_LIBDIR" ]]; then
    echo "Copying libcoinhsl.so -> $JULIA_IPOPT_LIBDIR/"
    cp -f "$CANONICAL" "$JULIA_IPOPT_LIBDIR/libcoinhsl.so"
fi

# ── generate env activation snippet ──────────────────────────────────────────
cat > "$INSTALL_DIR/env.sh" <<EOF
# Source this file to make CoinHSL/MA27/MA57 visible to Ipopt at runtime.
# Generated by scripts/install_hsl_ipopt.sh
export LD_LIBRARY_PATH="$LIB_DIR\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
export IPOPT_LINEAR_SOLVER="$SOLVER"
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
    echo "  $JULIA_BIN --project=\$JULIA_PROJECT -e '"
    echo "    using JuMP, Ipopt"
    echo "    m = Model(Ipopt.Optimizer)"
    echo "    set_optimizer_attribute(m, \"print_level\", 0)"
    echo "    set_optimizer_attribute(m, \"linear_solver\", \"$SOLVER\")"
    echo "    println(\"${SOLVER^^}_SMOKE_OK\")'"
    exit 0
fi

echo ""
echo "--- Julia smoke test ($SOLVER) ---"
JULIA_PROJECT="${PGDF_JULIA_PROJECT_DIR:-$ROOT/julia/lockfiles/andes}"
JULIA_DEPOT="${JULIA_DEPOT_PATH:-$ROOT/.julia_depot_andes_profile}"
SMOKE_RESULT=$(
    LD_LIBRARY_PATH="$LIB_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    JULIA_DEPOT_PATH="$JULIA_DEPOT" \
        "$JULIA_BIN" \
            --project="$JULIA_PROJECT" \
            -e "using JuMP, Ipopt
m = Model(Ipopt.Optimizer)
set_optimizer_attribute(m, \"print_level\", 0)
set_optimizer_attribute(m, \"linear_solver\", \"$SOLVER\")
println(\"${SOLVER^^}_SMOKE_OK\")" 2>&1
)

if echo "$SMOKE_RESULT" | grep -q "${SOLVER^^}_SMOKE_OK"; then
    echo "PASSED: $SOLVER loaded successfully."
else
    echo "FAILED: smoke test did not confirm $SOLVER." >&2
    echo "--- Julia output ---" >&2
    echo "$SMOKE_RESULT" >&2
    echo "" >&2
    echo "The library was installed to $LIB_DIR but Ipopt may not be finding it." >&2
    echo "Ensure LD_LIBRARY_PATH is set and/or libcoinhsl.so is in the Ipopt lib dir." >&2
    exit 1
fi

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
echo "  To activate in Slurm sbatch:"
echo "    source \$ROOT/external/coinhsl/env.sh"
echo ""
echo "  The project's run_opf.jl reads IPOPT_LINEAR_SOLVER from the"
echo "  environment and passes it to Ipopt automatically."
echo "================================================================"
