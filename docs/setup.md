# Environment and Setup Guide

## Prerequisites

- Python 3.10+ (3.11 recommended on this system).
- Git.
- Julia for PowerModels workflows.
- Sufficient filesystem quota for run artifacts.

## Create a Python environment

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e '.[analysis-mpi]'
```

If your system `python` is older, use a known Python 3.11 interpreter.
On Andes, the repository setup helper loads OpenMPI, creates or updates `.venv`,
installs the mpi4py MPI-ABI wheel, and verifies that it resolves to the active
MPI library:

```bash
bash scripts/setup_andes_venv.sh
```

On Frontier, use `bash scripts/setup_frontier_venv.sh`; it installs the same
analysis extra against the active Cray compiler wrapper.

## Validate the base scaffold

```bash
PYTHONPATH=src python scripts/validate_run_layout.py --runs-root data/runs
PYTHONPATH=src python scripts/audit_preservation.py --runs-root data/runs
```

Expected result for a fresh scaffold is `ok: true` with zero attempts.

## Julia setup for PowerModels

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
julia --project=julia julia/setup_environment.jl
```

### Riker PF campaign environment

Riker uses an isolated container-backed Julia 1.10.10 environment rather than
the Andes module stack:

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
bash scripts/setup_riker_environment.sh

export PGDF_JULIA_BIN=$PWD/.software/bin/julia
export PGDF_JULIA_PROJECT_DIR=$PWD/julia/lockfiles/riker
export JULIA_DEPOT_PATH=$PWD/.julia_depot_riker

.venv-riker/bin/python -c 'import grid_data_factory, pydantic, pyarrow, yaml'
$PGDF_JULIA_BIN --project="$PGDF_JULIA_PROJECT_DIR" \
	-e 'import PowerModels, Ipopt, JSON3; println("RUNTIME_OK")'
```

See [Riker Complementary PF Campaign](riker_pf_campaign.md) for anchor-index
creation, smoke testing, large submission, output layout, and resume behavior.

## HSL / MA27 / MA57 for the Julia + Ipopt stack

Coin-HSL is installed in private user storage because its source and binaries
must not be committed or redistributed. Install and validate both solvers with:

```bash
bash scripts/install_hsl_ipopt.sh "$HOME/coinhsl-2023.11.17.tar.gz" ma57
source "$HOME/.local/coinhsl/current/env.sh"
```

The Julia runners consume both `IPOPT_LINEAR_SOLVER` and
`IPOPT_HSL_LIBRARY`. See [the HSL guide](hsl_ma27_ma57_julia_stack.md) for the
validated Riker stack and licensing constraints.

## Machine-scoped Julia lockfiles

To prevent dependency lockfile conflicts across machines, use profile-specific project directories:

- `julia/lockfiles/andes/`
- `julia/lockfiles/frontier/`
- `julia/lockfiles/local/`
- `julia/lockfiles/riker/`

Initialize the profile on each machine before running PowerModels:

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
module load julia/1.8.2
export JULIA_DEPOT_PATH=$PWD/.julia_depot_andes_profile
julia --project=julia/lockfiles/andes -e 'using Pkg; Pkg.Registry.add("General"); Pkg.resolve(); Pkg.instantiate()'
```

The profile installs `PowerModelsSecurityConstrained` for coupled AC SCOPF in
addition to the base PowerModels stack. Verify the SCOPF dependency with:

```bash
julia --project=julia/lockfiles/andes -e 'using PowerModelsSecurityConstrained; println("PMSC_OK")'
```

Then run with the same profile:

```bash
OPENBLAS_NUM_THREADS=1 JULIA_NUM_THREADS=1 JULIA_PKG_PRECOMPILE_AUTO=0 julia --project=julia/lockfiles/andes --compiled-modules=no julia/run_opf.jl <case_json> <payload_json> <out_json>
```

See `julia/lockfiles/README.md` for details.

PowerModels Python workflows now auto-select a Julia project profile based on hostname:

- hosts containing `andes` -> `julia/lockfiles/andes`
- hosts containing `frontier` -> `julia/lockfiles/frontier`
- hosts containing `riker` -> `julia/lockfiles/riker`
- otherwise -> `julia/lockfiles/local` (fallback: `julia/`)

You can override selection explicitly with:

```bash
export PGDF_JULIA_PROJECT_DIR=julia/lockfiles/andes
```

## PowerModels security-constrained OPF

`PowerModelsAdapter.solve_scopf(case, contingencies, options)` uses
`PowerModelsSecurityConstrained.run_c1_scopf` and its explicit multinetwork
formulation. Each contingency must be a static N-1 event with one `branch` or
`generator` component, for example:

```python
result = adapter.solve_scopf(
	case,
	[{
		"contingency_id": "line_1_out",
		"event_type": "simultaneous",
		"components": [{"type": "branch", "id": "branch_000001"}],
	}],
)
```

Sequential events and simultaneous N-k events are rejected because PMSC 0.12's
coupled formulation represents one component outage per contingency network.
PMSC also does not support nonempty PowerModels `storage`, `dcline`, or `switch`
components.

Run the preservation-first command with a MATPOWER case and contingency set:

```bash
python scripts/run_scopf.py \
	--case-file external/ExaGO/datafiles/case5.m \
	--contingency-set data/contingency_set_registry/case5_n1.json \
	--timeout-s 1800
```

The contingency file may be JSONL campaign rows or a JSON object such as:

```json
{
	"contingency_set_id": "case5_n1",
	"contingencies": [
		{
			"contingency_id": "branch_000001_out",
			"event_type": "simultaneous",
			"components": [{"type": "branch", "id": "branch_000001"}]
		}
	]
}
```

If your environment has precompile instability, use conservative runtime settings:

```bash
OPENBLAS_NUM_THREADS=1 JULIA_NUM_THREADS=1 JULIA_PKG_PRECOMPILE_AUTO=0 julia --project=julia --compiled-modules=no julia/run_opf.jl <case_json> <payload_json> <out_json>
```

## ExaGO source

ExaGO source is cloned at:

`external/ExaGO`

Current pinned commit in this workspace:

`545a8deb6fa35552f0ee402ca83672fe1255f61a`

Building ExaGO binaries is a separate step and is site-specific.

## Andes CPU-only ExaGO build profile

Andes workflows in this project should use a dedicated CPU-only ExaGO profile so
they do not interfere with Frontier GPU builds:

- Build dir: `external/ExaGO/builds/andes-cpu/build`
- Install dir: `external/ExaGO/builds/andes-cpu/install`

Configure the isolated profile with GPU-disabled flags:

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
PYTHONPATH=src python3.11 scripts/configure_exago_build.py \
	--exago-root external/ExaGO \
	--preset andes-cpu \
	--build-type Release
```

Equivalent explicit form (without preset) remains supported with `--profile` and
`--define` flags.

Do not use the HIP cache preset (`buildsystem/clang-hip/cache.cmake`) for this
Andes CPU profile.

Current known blocker on this machine: ExaGO configure requires PETSc >= 3.24,
while the loaded default Andes software stacks provide older PETSc versions.
Load/activate an environment with PETSc >= 3.24 and Ipopt before attempting the
full build/install step.

For the exact successful Frontier command sequence used in this workspace, see:

- `docs/exago_frontier_build.md`

## Frontier GPU ExaGO build profile

Frontier workflows should use a dedicated GPU-enabled profile with upstream HIP
cache and machine environment script:

- Build dir: `external/ExaGO/builds/frontier/build`
- Install dir: `external/ExaGO/builds/frontier/install`

Dry-run the full command setup:

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
PYTHONPATH=src python3.11 scripts/configure_exago_build.py \
	--exago-root external/ExaGO \
	--preset frontier-gpu \
	--dry-run
```

Run configure + build + install:

```bash
PYTHONPATH=src python3.11 scripts/configure_exago_build.py \
	--exago-root external/ExaGO \
	--preset frontier-gpu \
	--build --install --parallel 12
```

## Frontier ExaGO campaign runtime environment

The map/reduce campaign driver and workers require a Python environment with
`pandas` + `pyarrow` so every campaign writes uniform Parquet ledgers. If no
parquet engine is importable the ledgers degrade to a `.parquet.jsonl` fallback,
which fragments the corpus across campaigns; the Frontier sbatch prints a warning
in that case rather than failing.

Build the canonical virtualenv once from a login node:

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
bash scripts/setup_frontier_venv.sh
```

This sources the Frontier module environment (so the venv matches the runtime
interpreter), creates `.venv`, and installs `requirements.txt` (`pandas>=2.0`,
`pyarrow>=14.0`). The campaign sbatch
`configs/slurm/frontier_exago_acopf_mapreduce_8n_2h.sbatch` defaults
`PGDF_VENV=$ROOT/.venv` and activates it automatically after loading the
ROCm/spack modules. Override with `PGDF_VENV=/path/to/venv`, or set `PGDF_VENV=`
(empty) to force the module Python.

## Recommended first checks

1. Confirm config files parse and contain expected solver IDs.
2. Run one local attempt with a tiny synthetic case.
3. Finalize and verify attempt integrity.
4. Inspect generated manifest and checksum files.
