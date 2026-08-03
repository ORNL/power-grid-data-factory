# ExaGO Andes CPU Build and Run

This page documents the known-good, latest-code ExaGO workflow for Andes CPU-only runs in this workspace.

## Scope

- Machine: Andes (CPU-only path, no GPU/HIP cache)
- ExaGO source root: `external/ExaGO-andes-cpu-latest`
- Build profile: `andes-cpu-latest`
- Solver path validated: `opflow` with `IPOPT`

## Isolated directories

- Build: `external/ExaGO-andes-cpu-latest/builds/andes-cpu-latest/build`
- Install: `external/ExaGO-andes-cpu-latest/builds/andes-cpu-latest/install`
- Helper launcher: `scripts/run_exago_andes_opflow.sh`

## Module stack (required)

```bash
module purge
module use /sw/andes/spack-envs/modules/gcc/14.2.0
module load gcc-14.2.0/openmpi/5.0.5 openmpi-5.0.5/gcc-14.2.0/petsc/3.22.1-mpi
```

Keep the MPI/PETSc stack consistent for configure, build, install, and runtime.

## Configure, build, install

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory

PYTHONPATH=src python3.11 scripts/configure_exago_build.py \
  --exago-root external/ExaGO-andes-cpu-latest \
  --profile andes-cpu-latest \
  --build-type Release \
  --define CMAKE_C_COMPILER=mpicc \
  --define CMAKE_CXX_COMPILER=mpicxx \
  --define EXAGO_ENABLE_GPU=OFF \
  --define EXAGO_ENABLE_HIOP=OFF \
  --define EXAGO_ENABLE_IPOPT=ON \
  --define EXAGO_ENABLE_PYTHON=OFF \
  --define EXAGO_ENABLE_OMP=ON \
  --define EXAGO_PETSC_MIN_VERSION=3.22.1 \
  --define IPOPT_DIR=/tmp/mlupopa/spack-install/linux-zen2/ipopt-3.14.14-z2ayx5wqcfaqtc444w22rlygqerfhr42 \
  --define OpenBLAS_DIR=/tmp/mlupopa/spack-install/linux-zen2/openblas-0.3.30-kganzs2hof2ugi7kg5bimgn7ugvbwj52 \
  --define CMAKE_EXE_LINKER_FLAGS=-lgomp \
  --define CMAKE_SHARED_LINKER_FLAGS=-lgomp

cmake --build external/ExaGO-andes-cpu-latest/builds/andes-cpu-latest/build --parallel 12
cmake --install external/ExaGO-andes-cpu-latest/builds/andes-cpu-latest/build
```

## Smoke test

Run default case9 test:

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
bash scripts/run_exago_andes_opflow.sh
```

Or run a custom command through the helper:

```bash
bash scripts/run_exago_andes_opflow.sh \
  -netfile external/ExaGO-andes-cpu-latest/datafiles/case9/case9mod.m \
  -opflow_solver IPOPT \
  -opflow_model POWER_BALANCE_POLAR \
  -print_output 1
```

Expected key lines:

- `EXIT: Optimal Solution Found.`
- `Convergence status                  CONVERGED`
- `Objective value                     4144.46`

## Notes

- Do not use `buildsystem/clang-hip/cache.cmake` on Andes CPU workflow.
- Keep this flow isolated from Frontier GPU builds.
- If local Spack paths change, update `IPOPT_DIR` and `OpenBLAS_DIR` accordingly.
