#!/bin/bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
EXTERNAL_DIR=${EXTERNAL_DIR:-"${ROOT_DIR}/external"}
PARALLEL=${PARALLEL:-12}

EXAGO_URL=https://github.com/allaffa/ExaGO.git
EXAGO_COMMIT=ef9a781a5e32603c1a93e1be5dab5fea3838af67
GINKGO_URL=https://github.com/allaffa/ginkgo.git
GINKGO_COMMIT=fcce3847eaf8ebd019871d13a485b616b43591e8
HIOP_URL=https://github.com/allaffa/hiop.git
HIOP_COMMIT=71ab56e8dd52adeb4aef3b1ef15b42f549d0d2e1

clean=0
dry_run=0

usage() {
    cat <<'EOF'
Usage: scripts/build_exago_frontier.sh [--clean] [--dry-run]

Build the pinned Ginkgo, HiOp, and ExaGO Frontier GPU stack.

  --clean    Remove generated build and install directories before configuring.
  --dry-run  Print commands and validate existing checkout revisions without building.
EOF
}

while (($#)); do
    case "$1" in
        --clean) clean=1 ;;
        --dry-run) dry_run=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

print_command() {
    printf '+'
    printf ' %q' "$@"
    printf '\n'
}

run() {
    print_command "$@"
    if ((dry_run == 0)); then
        "$@"
    fi
}

prepare_checkout() {
    local name=$1
    local url=$2
    local commit=$3
    local path=$4

    if [[ ! -d "${path}/.git" ]]; then
        run git clone "$url" "$path"
        run git -C "$path" checkout --detach "$commit"
        return
    fi

    local actual
    actual=$(git -C "$path" rev-parse HEAD)
    if [[ "$actual" != "$commit" ]]; then
        echo "$name checkout is at $actual; expected $commit" >&2
        echo "Use a separate EXTERNAL_DIR or explicitly restore the pinned revision." >&2
        exit 2
    fi
    echo "$name: $actual"
}

mkdir -p "$EXTERNAL_DIR"

GINKGO_SRC=${EXTERNAL_DIR}/Ginkgo
HIOP_SRC=${EXTERNAL_DIR}/HiOp
EXAGO_SRC=${EXTERNAL_DIR}/ExaGO
HIOP_BUILD=${HIOP_SRC}/build-frontier

prepare_checkout Ginkgo "$GINKGO_URL" "$GINKGO_COMMIT" "$GINKGO_SRC"
prepare_checkout HiOp "$HIOP_URL" "$HIOP_COMMIT" "$HIOP_SRC"
prepare_checkout ExaGO "$EXAGO_URL" "$EXAGO_COMMIT" "$EXAGO_SRC"

if ((dry_run)); then
    print_command env SRCDIR="$EXAGO_SRC" source "$EXAGO_SRC/buildsystem/clang-hip/frontierVariables.sh"
else
    export EXTRA_CMAKE_ARGS=${EXTRA_CMAKE_ARGS:-}
    export SRCDIR=$EXAGO_SRC
    set +u
    source "$EXAGO_SRC/buildsystem/clang-hip/frontierVariables.sh"
    set -u
fi

export CMAKE_PREFIX_PATH="${GINKGO_SRC}/install:${HIOP_SRC}/install:${CMAKE_PREFIX_PATH:-}"

if ((clean)); then
    run rm -rf \
        "$GINKGO_SRC/build" "$GINKGO_SRC/install" \
        "$HIOP_BUILD" "$HIOP_SRC/install" \
        "$EXAGO_SRC/build-frontier" "$EXAGO_SRC/install"
fi

run cmake -S "$GINKGO_SRC" -B "$GINKGO_SRC/build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$GINKGO_SRC/install" \
    -DCMAKE_C_COMPILER=/opt/rocm-6.3.1/llvm/bin/amdclang \
    -DCMAKE_CXX_COMPILER=/opt/rocm-6.3.1/llvm/bin/amdclang++ \
    -DCMAKE_HIP_COMPILER=/opt/rocm-6.3.1/llvm/bin/clang++ \
    -DCMAKE_HIP_ARCHITECTURES=gfx90a \
    -DAMDGPU_TARGETS=gfx90a \
    -DGINKGO_HIP_AMDGPU=gfx90a \
    -DGINKGO_BUILD_HIP=ON \
    -DGINKGO_BUILD_REFERENCE=ON \
    -DGINKGO_BUILD_CUDA=OFF \
    -DGINKGO_BUILD_DPCPP=OFF \
    -DGINKGO_BUILD_OMP=OFF \
    -DGINKGO_BUILD_MPI=OFF \
    -DGINKGO_BUILD_TESTS=OFF \
    -DGINKGO_BUILD_EXAMPLES=OFF \
    -DGINKGO_BUILD_BENCHMARKS=OFF \
    -DGINKGO_BUILD_DOC=OFF \
    -DGINKGO_HIP_THRUST_PATH=/opt/rocm-6.3.1/include
run cmake --build "$GINKGO_SRC/build" --parallel "$PARALLEL"
run cmake --install "$GINKGO_SRC/build"

run cmake -S "$HIOP_SRC" -B "$HIOP_BUILD" \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DCMAKE_INSTALL_PREFIX="$HIOP_SRC/install" \
    -DCMAKE_C_COMPILER=/opt/rocm-6.3.1/llvm/bin/amdclang \
    -DCMAKE_CXX_COMPILER=/opt/rocm-6.3.1/llvm/bin/amdclang++ \
    -DCMAKE_HIP_COMPILER=/opt/rocm-6.3.1/llvm/bin/clang++ \
    -DCMAKE_HIP_ARCHITECTURES=gfx90a \
    -DAMDGPU_TARGETS=gfx90a \
    -DGPU_TARGETS=gfx90a \
    -DGinkgo_DIR="$GINKGO_SRC/install/lib64/cmake/Ginkgo" \
    -DHIOP_GINKGO_DIR="$GINKGO_SRC/install" \
    -DHIOP_BUILD_SHARED=OFF \
    -DHIOP_BUILD_STATIC=ON \
    -DHIOP_SPARSE=ON \
    -DHIOP_USE_GPU=ON \
    -DHIOP_USE_HIP=ON \
    -DHIOP_USE_CUDA=OFF \
    -DHIOP_USE_GINKGO=ON \
    -DHIOP_USE_MAGMA=ON \
    -DHIOP_USE_MPI=ON \
    -DHIOP_USE_RAJA=ON \
    -DHIOP_USE_COINHSL=ON \
    -DHIOP_USE_RESOLVE=OFF
run cmake --build "$HIOP_BUILD" --parallel "$PARALLEL"
run cmake --install "$HIOP_BUILD"

run cmake -S "$EXAGO_SRC" -B "$EXAGO_SRC/build-frontier" \
    -C "$EXAGO_SRC/buildsystem/clang-hip/cache.cmake" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$EXAGO_SRC/install" \
    -DCMAKE_C_COMPILER=/opt/rocm-6.3.1/llvm/bin/amdclang \
    -DCMAKE_CXX_COMPILER=/opt/rocm-6.3.1/llvm/bin/amdclang++ \
    -DCMAKE_HIP_COMPILER=/opt/rocm-6.3.1/llvm/bin/clang++ \
    -DCMAKE_HIP_ARCHITECTURES=gfx90a \
    -DAMDGPU_TARGETS=gfx90a \
    -DGinkgo_DIR="$GINKGO_SRC/install/lib64/cmake/Ginkgo" \
    -DHiOp_DIR="$HIOP_SRC/install/share/hiop/cmake"
run cmake --build "$EXAGO_SRC/build-frontier" --parallel "$PARALLEL"
run cmake --install "$EXAGO_SRC/build-frontier"

echo "ExaGO installed at ${EXAGO_SRC}/install"