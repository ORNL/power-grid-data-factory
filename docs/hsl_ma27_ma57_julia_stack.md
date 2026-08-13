# Enabling HSL/MA27/MA57 in the Julia + Ipopt stack

## Quick start (once you have the HSL tarball)

```bash
# 1. Obtain the free academic licence and download the CoinHSL tarball:
#    https://licences.stfc.ac.uk/product/coin-hsl
#    The file is named coinhsl-YYYY.MM.DD.tar.gz

# 2. Run the project install script (builds libcoinhsl.so, installs to
#    external/coinhsl/lib/, copies alongside Julia's libipopt.so, smoke-tests):
bash scripts/install_hsl_ipopt.sh /path/to/coinhsl-*.tar.gz ma57

# 3. Activate in your shell (or Slurm script) before running campaigns:
source external/coinhsl/env.sh   # sets LD_LIBRARY_PATH + IPOPT_LINEAR_SOLVER=ma57

# 4. Run as normal; run_opf.jl reads IPOPT_LINEAR_SOLVER automatically.
```

The Python adapter (`powermodels_adapter.py`) also auto-injects
`external/coinhsl/lib/` into `LD_LIBRARY_PATH` whenever that directory exists,
so Julia subprocess launches pick up the library without any shell sourcing.

---

## Status in this workspace

The current workspace has a local Ipopt installation, but it is not fully usable for HSL-backed linear solves.

Verified findings from the active environment:

- `julia` is not currently on the shell `PATH` in the active session.
- the installed `libipopt.so` contains `Ma27TSolverInterface` and `Ma57TSolverInterface` symbols
- the corresponding HSL runtime library files (`libhsl*`, `libcoinhsl*`, `*ma27*`, `*ma57*`) are not present in the visible install tree
- this means the Ipopt binary is compiled with HSL hooks, but the runtime library that actually loads MA27/MA57 is missing

This is the exact blocker for `linear_solver = "ma57"` or `linear_solver = "ma27"`.

## Why this matters

PowerModels + Ipopt will usually run with the default linear solver. That is often a generic sparse direct method, but it is not the same as a tuned HSL backend.

For difficult or near-boundary OPF instances, MA27 or MA57 can be materially more robust and faster. The relevant runtime setting is:

```julia
using JuMP, Ipopt
m = Model(Ipopt.Optimizer)
set_optimizer_attribute(m, "linear_solver", "ma57")
```

or:

```julia
set_optimizer_attribute(m, "linear_solver", "ma27")
```

If HSL is not installed, this will fail at runtime or silently fall back to the default solver path depending on the build.

## Required stack pieces

To enable HSL-backed linear solvers, the machine must provide all of the following:

1. a valid HSL/MA27/MA57 library package or license
2. an Ipopt build linked against that HSL implementation
3. a Julia environment that loads that Ipopt build
4. runtime library paths exposed to the dynamic linker

## Step-by-step procedure to add HSL to the installed Julia stack

The sequence below is the one to follow on Andes or any similar machine where we want to add HSL support to the project’s Julia stack.

### 1) Start from a clean shell and load the compiler stack

```bash
module purge
module use /sw/andes/spack-envs/modules/gcc/14.2.0
module load gcc-14.2.0/openmpi/5.0.5 openmpi-5.0.5/gcc-14.2.0/petsc/3.22.1-mpi
module load julia/1.8.2
```

Check that Julia resolves correctly:

```bash
which julia
julia --version
```

### 2) Inspect the current Ipopt installation and confirm the missing HSL layer

```bash
IPOPT_ROOT=/tmp/mlupopa/spack-install/linux-zen2/ipopt-3.14.14-z2ayx5wqcfaqtc444w22rlygqerfhr42
find "$IPOPT_ROOT" -maxdepth 3 \( -iname 'libhsl*' -o -iname 'libcoinhsl*' -o -iname '*ma27*' -o -iname '*ma57*' \)
strings "$IPOPT_ROOT/lib/libipopt.so.3.14.14" | grep -Ei 'ma27|ma57|coinhsl|hsl' | head -50
```

The expected result is that `libhsl` / `libcoinhsl` is present and usable. In the current environment it is not, which is consistent with the observed runtime failure.

### 3) Obtain or expose the HSL library

On a Linux cluster, this usually means one of the following:

- installing the HSL/MA27/MA57 package from the system package manager or site-managed software stack
- adding the site-provided HSL library path to `LD_LIBRARY_PATH`
- rebuilding Ipopt from a Spack install that explicitly links against CoinHSL/HSL

If the package is already installed but not in the default library path, do:

```bash
export LD_LIBRARY_PATH=/path/to/hsl/lib:${LD_LIBRARY_PATH}
export LIBRARY_PATH=/path/to/hsl/lib:${LIBRARY_PATH}
export CPATH=/path/to/hsl/include:${CPATH}
```

If it is installed via Spack, locate it first:

```bash
spack find | grep -Ei 'ipopt|hsl|coinhsl'
find /tmp /opt /sw -type f \( -iname 'libhsl*' -o -iname 'libcoinhsl*' -o -iname '*ma27*' -o -iname '*ma57*' \) 2>/dev/null | head -200
```

### 4) Rebuild or relink Ipopt against HSL

If the HSL library exists but Ipopt was built without it, rebuild Ipopt with the HSL-enabled option. The exact Spack command depends on the local environment, but the pattern is:

```bash
spack install ipopt +hsl
```

or, if a direct build is used:

```bash
./configure --with-hsl=/path/to/hsl --with-blas=... --with-lapack=...
make -j$(nproc)
make install
```

This is the key step that turns the solver backend from a generic Ipopt build into a MA27/MA57-capable one.

### 5) Rebuild or reinstall the project’s Julia stack against the new Ipopt

Once the HSL-capable Ipopt is in place, ensure the Julia environment sees that install. The repo already supports machine-specific Julia project stacks via:

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
export JULIA_DEPOT_PATH=$PWD/.julia_depot_andes_profile
julia --project=julia/lockfiles/andes -e 'using Pkg; Pkg.resolve(); Pkg.instantiate()'
```

If the system Ipopt is not the one Julia should use, make the library path explicit before launch:

```bash
export LD_LIBRARY_PATH=/path/to/hsl/lib:/path/to/ipopt/lib:${LD_LIBRARY_PATH}
```

Then start Julia and confirm the linear solver is available:

```bash
julia --project=julia/lockfiles/andes -e 'using JuMP, Ipopt; m = Model(Ipopt.Optimizer); set_optimizer_attribute(m, "print_level", 0); set_optimizer_attribute(m, "linear_solver", "ma57"); println("MA57_AVAILABLE")'
```

and similarly:

```bash
julia --project=julia/lockfiles/andes -e 'using JuMP, Ipopt; m = Model(Ipopt.Optimizer); set_optimizer_attribute(m, "print_level", 0); set_optimizer_attribute(m, "linear_solver", "ma27"); println("MA27_AVAILABLE")'
```

### 6) Update the runtime config in the project

Once MA27 or MA57 is confirmed to work, set the solver in the project’s AC-OPF runner. The relevant file is:

- `julia/run_opf.jl`

The optimizer configuration currently does not pin a linear solver. Add the relevant option there:

```julia
optimizer = optimizer_with_attributes(
    Ipopt.Optimizer,
    "print_level" => 0,
    "sb" => "yes",
    "tol" => 1e-8,
    "linear_solver" => "ma57",
)
```

or:

```julia
"linear_solver" => "ma27"
```

The user-visible behavior is then consistent across the Julia AC-OPF runs.

### 7) Validate on a small subset of real failed cases

After the stack is rebuilt, compare the same cases under:

- default Ipopt
- `ma27`
- `ma57`

Measure:

- solve convergence
- objective value
- iteration count
- runtime
- status (`LOCALLY_SOLVED`, `INFEASIBLE`, `ITERATION_LIMIT`, etc.)

This is the only trustworthy way to decide whether MA27/MA57 materially improve robustness on the campaign failures.

## Current state for this repository

This repository still has the blocker that prevented the test from being completed:

- Julia is not on the active `PATH`
- the visible Ipopt installation does not have a real HSL runtime present
- therefore MA27/MA57 are not currently enabled in the active toolchain

The fix is system-level, not a Python-side or Julia-side syntax change alone.

## Practical recommendation

If this needs to be done on Andes with the project’s current workflow, use the local Spack install or the site-managed HSL package, then rebuild Ipopt and the Julia project stack so both the runtime linker and Julia see the same HSL-enabled library.

Only after that should the project switch the solver config to `ma27` or `ma57` for the actual AC-OPF campaign.
