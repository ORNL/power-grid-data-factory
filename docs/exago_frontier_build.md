# ExaGO Frontier Build Notes

This page records the exact ExaGO build and smoke-test flow used in this workspace.

## Source and revision

- Repository: https://github.com/ORNL/ExaGO
- Local path: `external/ExaGO`
- Commit built: `545a8deb6fa35552f0ee402ca83672fe1255f61a`

## Why this is worth documenting

- Frontier module behavior can change over time.
- ExaGO provides machine-specific build helpers that should be preferred over ad hoc flags.
- A known-good command sequence reduces troubleshooting time for future rebuilds.

## Build sequence used

From repository root (`external/ExaGO`):

```bash
git submodule update --init --recursive
source buildsystem/clang-hip/frontierVariables.sh
mkdir -p builds/frontier/build
cd builds/frontier/build
cmake -C ../../../buildsystem/clang-hip/cache.cmake ../../.. -DCMAKE_BUILD_TYPE=Release
make -j 12 install
```

## Recommended multi-machine layout

Use machine-scoped build and install prefixes so different systems do not overwrite each other:

- `external/ExaGO/builds/frontier/build`
- `external/ExaGO/builds/frontier/install`
- `external/ExaGO/builds/<other-machine>/build`
- `external/ExaGO/builds/<other-machine>/install`

## Preferred helper script

From repository root (`power_grid_data_factory`), run the helper to configure machine-scoped paths automatically:

```bash
PYTHONPATH=src python3.11 scripts/configure_exago_build.py \
  --exago-root external/ExaGO \
  --preset frontier-gpu \
  --build-type Release
```

To run configure + build + install in one shot:

```bash
PYTHONPATH=src python3.11 scripts/configure_exago_build.py \
  --exago-root external/ExaGO \
  --preset frontier-gpu \
  --build --install --parallel 12
```

The `frontier-gpu` preset applies:

- `profile=frontier`
- cache file `buildsystem/clang-hip/cache.cmake`
- environment setup script `buildsystem/clang-hip/frontierVariables.sh`
- default defines enabling GPU and IPOPT

To enforce install isolation, include an explicit install prefix during configure:

```bash
cmake -C ../../../buildsystem/clang-hip/cache.cmake ../../.. \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=$PWD/../install
```

## Important observations

- No ExaGO source files or cache files were edited for this build.
- Build used upstream Frontier presets:
  - `buildsystem/clang-hip/frontierVariables.sh`
  - `buildsystem/clang-hip/cache.cmake`
- The module script reset and reloaded the programming environment automatically.
- CMake successfully detected required dependencies in this environment, including PETSc and Ipopt.

## Install location

- Binaries and libraries were installed to:
  - `external/ExaGO/builds/frontier/install`

## Smoke test run

From `external/ExaGO`:

```bash
source buildsystem/clang-hip/frontierVariables.sh
./builds/frontier/install/bin/opflow -netfile datafiles/case9/case9mod.m -opflow_solver IPOPT -print_output 1
```

Observed result:

- Ipopt reported: `EXIT: Optimal Solution Found.`
- ExaGO reported: `Convergence status CONVERGED`
- Objective value: `4144.46`

## Minimal rerun checklist

1. `external/ExaGO` exists and matches intended commit.
2. Submodules are initialized.
3. Frontier variables script is sourced in the active shell.
4. Build directory config succeeds with `cache.cmake`.
5. `builds/frontier/install/bin/opflow` exists and smoke test converges.
