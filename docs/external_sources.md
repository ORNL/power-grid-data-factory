# External Dependencies and Data Sources

This page lists external solver and data repositories needed to reproduce simulation campaigns.

## Solver repositories

## PowerModels.jl

- Repository: https://github.com/lanl-ansi/PowerModels.jl
- Use for: AC-PF, DC-OPF, AC-OPF, relaxation-based OPF workflows.
- Runtime: Julia.

## ExaGO

- Repository: https://github.com/ORNL/ExaGO
- Local clone path: `external/ExaGO`
- Workspace clone commit: `545a8deb6fa35552f0ee402ca83672fe1255f61a`
- Use for: OPFLOW/SCOPFLOW workflows, large-scale HPC workloads.

## MATPOWER (optional)

- Repository: https://github.com/MATPOWER/matpower
- Runtime: GNU Octave or MATLAB.
- Use for: independent cross-solver reference checks.

## Grid data repositories

## PGLib-OPF

- Repository: https://github.com/power-grid-lib/pglib-opf
- Typical local path: `external/pglib-opf`
- Use for: baseline and scaling portfolio across many case sizes.

### TAMU-derived cases available through PGLib (automatic)

These cases have explicit topology correspondence and can be acquired by cloning PGLib:

- `ACTIVSg200` -> `pglib_opf_case200_activ.m`
- `EPIGRIDS Florida` -> `pglib_opf_case5658_epigrids.m`
- `EPIGRIDS Texas` -> `pglib_opf_case7336_epigrids.m`
- `EPIGRIDS Midwest` -> `pglib_opf_case10192_epigrids.m`
- `EPIGRIDS Western Network` -> `pglib_opf_case20758_epigrids.m`
- `EPIGRIDS Eastern Network` -> `pglib_opf_case78484_epigrids.m`

### Non-equivalence rule

A similar bus count does not imply an identical topology. Cases are treated as equivalent only with explicit verified source relationships.

- `pglib_opf_case500_goc.m` is not `ACTIVSg500`
- `pglib_opf_case2000_goc.m` is not `ACTIVSg2000`
- `pglib_opf_case10000_goc.m` is not `ACTIVSg10k`

Grid families remain distinct even when bus counts are similar:

- `goc`
- `activsg`
- `epigrids`

## RTS-GMLC

- Repository: https://github.com/GridMod/RTS-GMLC
- Typical local path: `external/RTS-GMLC`
- Use for: chronological operating-point and temporal split workflows.

## Texas A&M Synthetic Grids

- Index: https://electricgrids.engr.tamu.edu/electric-grid-test-cases/
- Typical local path root: `external/tamu`
- Use for: large synthetic grid families and stress testing.

The following TAMU cases currently require manual acquisition (no assumed PGLib equivalence):

- `ACTIVSg500`
- `ACTIVSg2000`
- `ACTIVSg10k`
- `ACTIVSg25k`
- `ACTIVSg70k`
- `ACTIVSg82k`
- `Memphis 2026`
- `EPIGRIDS New England 250`
- `EPIGRIDS Wisconsin 1664`

Store manually downloaded archives under `external/tamu/<case_id>/raw/` and do not bypass case-form workflows.

## ARPA-E GO Competition Challenge 1

- OEDI page: https://data.openei.org/submissions/6153
- Typical local path root: `external/go_challenge1`
- Use for: parser validation and SCOPF-relevant contingency-oriented scenarios.

## Expected local organization

- `external/ExaGO`
- `external/pglib-opf`
- `external/RTS-GMLC`
- `external/tamu/`
- `external/go_challenge1/`

## Provenance expectations

For each external source used in runs, record:

- source URL
- downloaded or cloned timestamp
- exact commit (for git repos)
- archive checksum (for downloaded archives)
- license or usage notes
