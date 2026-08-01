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

## RTS-GMLC

- Repository: https://github.com/GridMod/RTS-GMLC
- Typical local path: `external/RTS-GMLC`
- Use for: chronological operating-point and temporal split workflows.

## Texas A&M Synthetic Grids

- Index: https://electricgrids.engr.tamu.edu/electric-grid-test-cases/
- Typical local path root: `external/tamu`
- Use for: large synthetic grid families and stress testing.

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
