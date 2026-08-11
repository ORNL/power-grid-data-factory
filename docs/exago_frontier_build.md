# ExaGO GPU Build on Frontier

This page records the source provenance, Frontier environment, local fixes, and
clean rebuild procedure for the ExaGO GPU executable used by Power Grid Data
Factory. Generated build and install trees under `external/` are intentionally
untracked; the fork commits and `scripts/build_exago_frontier.sh` are the
reproducible build inputs.

## Pinned source revisions

| Component | Upstream fork point | Frontier source | Built revision |
| --- | --- | --- | --- |
| ExaGO | [`ORNL/ExaGO@545a8deb`](https://github.com/ORNL/ExaGO/commit/545a8deb6fa35552f0ee402ca83672fe1255f61a) | [`allaffa/ExaGO`, branch `frontier-ginkgo-hiop-config`](https://github.com/allaffa/ExaGO/tree/frontier-ginkgo-hiop-config) | `8b3a06fd2eee0e0b9dd6aaf2f67ff507cb500959` |
| Ginkgo | [`ginkgo-project/ginkgo@e234eab1`](https://github.com/ginkgo-project/ginkgo/commit/e234eab1bd7afe85dd594638e291a2caf464bfb1) | [`allaffa/ginkgo`, branch `frontier-rocm-build-fixes`](https://github.com/allaffa/ginkgo/tree/frontier-rocm-build-fixes) | `fcce3847eaf8ebd019871d13a485b616b43591e8` |
| HiOp | [`LLNL/hiop` tag `v1.1.1`](https://github.com/LLNL/hiop/tree/v1.1.1) | upstream, unchanged | `d8762e05150b2040a27f69d8bf6603f22190a869` |

The fork points above are the direct parents of the Frontier fix commits. They
are not the current tips of the upstream `develop` branches.

## Changes in the forks

### Ginkgo

Commit `fcce3847e` updates the older Ginkgo HIP backend for ROCm 6.3.1 and
Frontier's `gfx90a` GPUs:

- raises the CMake minimum to 3.21 and enables HIP as a native project language;
- replaces legacy `hip_add_library` handling with native CMake HIP sources and
  target compile options;
- changes `--amdgpu-target` to the current `--offload-arch` compiler option;
- updates ROCm header names and include paths for hipBLAS, hipRAND, hipSPARSE,
  rocPRIM, and rocThrust;
- repairs HIP compilation in distributed, FFT, and triangular-solve kernels;
- adds missing C declarations in the bundled experimental GLU sparse direct
  solver.

The complete build changes are preserved in the fork's `CMakeLists.txt`,
`cmake/hip.cmake`, `hip/CMakeLists.txt`, HIP sources, and bundled GLU sources.

### ExaGO

Commit `8b3a06fd` configures both HiOp sparse solver paths explicitly:

- `opflow_hiopsparse.cpp` uses hybrid compute with Ginkgo on the HIP executor;
- `opflow_hiopsparsegpu.cpp` uses GPU/device compute with Ginkgo on the HIP
  executor.

This avoids automatic selection of a host-oriented sparse solver and ensures
that the campaign's `HIOPSPARSEGPU` path uses the locally built Ginkgo/GLU
backend.

## Frontier environment

The rebuild script sources the forked ExaGO environment file:

```text
external/ExaGO/buildsystem/clang-hip/frontierVariables.sh
```

That file sources `buildsystem/clang-hip/frontier/base.sh` and the generated
Spack dependency module list. The system modules are:

```bash
module reset
module load PrgEnv-gnu
module load craype-x86-trento
module load craype-accel-amd-gfx90a
module load rocm/6.3.1
module load cray-mpich
module load libfabric
module load cmake
module load cray-python
```

It also adds the shared module tree at
`/lustre/orion/stf006/world-shared/nkouk/exago-02-2026/spack-install/modules/linux-sles15-zen3`
and loads the complete generated dependency list, including MAGMA, RAJA,
Umpire, CoinHSL, SuiteSparse, Ipopt, PETSc, and their transitive dependencies.
The rebuild does not modify that shared installation. Ginkgo and HiOp are built
locally under `external/` and passed to downstream CMake configurations by
explicit package paths.

The validated compiler and target settings are ROCm 6.3.1
`amdclang`/`amdclang++`, CMake's HIP compiler, and `gfx90a`.

## Clean rebuild

Run on Frontier from the Power Grid Data Factory root:

```bash
make frontier-exago-dry-run
PARALLEL=12 make frontier-exago-clean
```

The dry run verifies existing checkout revisions and prints every configure,
build, and install command. The clean target deletes only generated build and
install directories, then builds in dependency order:

1. the Ginkgo fork with HIP, the reference executor, and bundled GLU;
2. HiOp `v1.1.1` with sparse, HIP, Ginkgo, MAGMA, MPI, RAJA, and CoinHSL;
3. the ExaGO fork with its Frontier cache and explicit local HiOp/Ginkgo paths.

If a source checkout is absent, the script clones it. If an existing checkout
does not match its pinned commit, the script stops instead of changing or
discarding local work. Set `EXTERNAL_DIR` to build in a separate source tree.

CMake generates the dependency Makefiles in each build directory. There is no
additional handwritten dependency Makefile: the tracked top-level `Makefile`
only exposes the reproducible driver targets, while the patched CMake build
definitions are versioned in the two forks.

## Install locations

```text
external/Ginkgo/install
external/HiOp/install
external/ExaGO/install
```

The corresponding generated build trees are `external/Ginkgo/build`,
`external/HiOp/build-frontier`, and `external/ExaGO/build-frontier`. The separate
HiOp directory preserves the source repository's tracked `build/.gitkeep`.

## Validation

Every MPI-linked `opflow` invocation on Frontier must run in its own singleton
Slurm step. From an allocated compute node:

```bash
source external/ExaGO/buildsystem/clang-hip/frontierVariables.sh
srun -N1 -n1 external/ExaGO/install/bin/opflow \
  -netfile external/ExaGO/datafiles/case118.m \
  -opflow_model PBPOLRAJAHIOPSPARSE \
  -opflow_solver HIOPSPARSEGPU \
  -print_output 1
```

Also confirm that the installed package metadata resolves to the local stack:

```bash
test -f external/Ginkgo/install/lib64/cmake/Ginkgo/GinkgoConfig.cmake
test -d external/HiOp/install/share/hiop/cmake
test -x external/ExaGO/install/bin/opflow
```

Do not run MPI-linked solves directly inside a multi-rank campaign step; the
inherited MPI environment can produce incorrect initialization or termination.
