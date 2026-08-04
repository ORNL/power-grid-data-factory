#!/bin/bash
# Build a PackageCompiler sysimage so campaign solves skip Julia's first-run
# compilation. Run ONCE per platform (on a compute node or via sbatch) before the
# big run; the solver adapter then auto-detects
#   julia/sysimages/<platform>/pgdf_sysimage.so
# and launches Julia with --sysimage instead of --compiled-modules=no.
#
# The build uses the same JULIA_DEPOT_PATH the campaign uses, so export it first
# (e.g. export JULIA_DEPOT_PATH=$PWD/.julia_depot_andes_profile).
#
# Requires the PackageCompiler package (declared in julia/build/Project.toml).
# Compute nodes usually lack network, so install it into the depot from a LOGIN
# node first:  julia --project=julia/build -e 'using Pkg; Pkg.instantiate()'.
#
# Overridable: PLATFORM, PGDF_SYSIMAGE_PROJECT, PGDF_SYSIMAGE_OUTPUT, PGDF_BUILD_ENV.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

module load julia 2>/dev/null || true

PLATFORM=${PLATFORM:-}
if [[ -z "$PLATFORM" ]]; then
  host="$(hostname | tr '[:upper:]' '[:lower:]')"
  if [[ "$host" == *andes* ]]; then PLATFORM=andes
  elif [[ "$host" == *frontier* ]]; then PLATFORM=frontier
  else PLATFORM=local; fi
fi

PROJECT_REL=${PGDF_SYSIMAGE_PROJECT:-julia/lockfiles/$PLATFORM}
if [[ ! -f "$REPO_ROOT/$PROJECT_REL/Project.toml" ]]; then
  PROJECT_REL=julia
fi
OUTPUT_REL=${PGDF_SYSIMAGE_OUTPUT:-julia/sysimages/$PLATFORM/pgdf_sysimage.so}
BUILD_ENV=${PGDF_BUILD_ENV:-julia/build}

export PGDF_SYSIMAGE_PROJECT="$REPO_ROOT/$PROJECT_REL"
export PGDF_SYSIMAGE_OUTPUT="$REPO_ROOT/$OUTPUT_REL"

echo "[build_julia_sysimage] platform=$PLATFORM"
echo "[build_julia_sysimage] project=$PGDF_SYSIMAGE_PROJECT"
echo "[build_julia_sysimage] output=$PGDF_SYSIMAGE_OUTPUT"
echo "[build_julia_sysimage] depot=${JULIA_DEPOT_PATH:-<default ~/.julia>}"

# Ensure the solve project is instantiated (packages present in the depot).
julia --project="$PROJECT_REL" -e 'using Pkg; Pkg.instantiate()'

# Build using a dedicated environment so the solve lockfiles stay untouched.
exec julia --project="$BUILD_ENV" "$REPO_ROOT/julia/build_sysimage.jl"
