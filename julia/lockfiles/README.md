# Machine-Scoped Julia Lockfiles

Use separate Julia project directories per machine to avoid dependency conflicts across environments.

## Layout

- `julia/lockfiles/andes/`
- `julia/lockfiles/frontier/`
- `julia/lockfiles/local/`

Each profile directory contains:

- `Project.toml` (shared dependency intent)
- `Manifest.toml` (machine-specific lockfile, generated on that machine)

## Initialize a Profile

Example for Andes:

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
module load julia/1.8.2
julia --project=julia/lockfiles/andes -e 'using Pkg; Pkg.Registry.add("General"); Pkg.resolve(); Pkg.instantiate()'
```

Example for Frontier:

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
module load julia
julia --project=julia/lockfiles/frontier -e 'using Pkg; Pkg.Registry.add("General"); Pkg.resolve(); Pkg.instantiate()'
```

## Use a Profile at Runtime

Run PowerModels with the profile for the current machine:

```bash
OPENBLAS_NUM_THREADS=1 JULIA_NUM_THREADS=1 JULIA_PKG_PRECOMPILE_AUTO=0 julia --project=julia/lockfiles/andes --compiled-modules=no julia/run_opf.jl <case_json> <payload_json> <out_json>
```

If using the Python adapter, pass the chosen profile directory as `julia_project_dir`.