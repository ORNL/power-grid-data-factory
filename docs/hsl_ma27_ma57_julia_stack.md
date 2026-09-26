# Coin-HSL MA27/MA57 for Julia and Ipopt

Coin-HSL is licensed software. Keep its archive, source, build tree, and shared
libraries in private user storage. Never commit or redistribute them through
this repository.

## Validated Private Stacks

Coin-HSL installations are machine-scoped so incompatible compiler runtimes are
never mixed. Andes uses `$HOME/.local/coinhsl/andes/current`; Riker uses
`$HOME/.local/coinhsl/riker/current`. Both MA27 and MA57 have been validated
with their corresponding Julia and compiler stacks.

Ipopt requires both settings:

- `IPOPT_LINEAR_SOLVER=ma27` or `ma57`;
- `IPOPT_HSL_LIBRARY=/absolute/path/to/libcoinhsl.so`.

`LD_LIBRARY_PATH` alone is not sufficient for this Ipopt build. The Julia PF,
AC-OPF, SCOPF, batch, and expansion runners pass both variables to Ipopt.

## Private Installation

Obtain the academic Coin-HSL archive directly from STFC, then run:

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
bash scripts/install_hsl_ipopt.sh \
  "$HOME/coinhsl-2023.11.17.tar.gz" ma57
```

The installer:

- refuses to overwrite an existing version;
- installs under `$HOME/.local/coinhsl/andes/<version>` by default;
- uses the Andes Julia environment's LP64 OpenBLAS;
- validates both MA27 and MA57 with Ipopt;
- creates `$HOME/.local/coinhsl/andes/current` only after validation.

Activate it with:

```bash
source "$HOME/.local/coinhsl/andes/current/env.sh"
```

Select MA27 instead of the default MA57 with:

```bash
export IPOPT_LINEAR_SOLVER=ma27
```

## Verification

Confirm the activation and licensed library without printing or copying its
contents:

```bash
source "$HOME/.local/coinhsl/andes/current/env.sh"
test -r "$IPOPT_HSL_LIBRARY"
nm -D "$IPOPT_HSL_LIBRARY" | grep ' ma27ad_'
nm -D "$IPOPT_HSL_LIBRARY" | grep ' ma57ad_'
```

Run a PF campaign smoke before any large submission:

```bash
WALLTIME=01:00:00 configs/slurm/submit_riker_pf_smoke.sh
```

For each candidate, the scheduler tries default Ipopt, then MA27, then MA57,
stopping after the first converged solve. The selected solver and all attempted
statuses are preserved in the result. Submission wrappers source and validate
the private activation before `sbatch`; the map/reduce job propagates `hsllib`,
OpenBLAS, and dynamic-library paths to every `srun` worker.

## Interpretation

Compare default Ipopt, MA27, and MA57 on identical candidate inputs when
evaluating robustness. Record termination status, objective, iterations, and
runtime. `LOCALLY_INFEASIBLE` remains a nonconvergent numerical outcome in this
campaign; it is not proof that the physical case is infeasible.