# Project Purpose and Scope

Power Grid Data Factory orchestrates **large, highly parametrized simulation
campaigns** for:

- PF
- DC-OPF
- AC-OPF
- SCOPF

The goal is to produce diverse, reproducible datasets for foundation-model
training and evaluation while preserving full provenance. For a capabilities-first
overview of the variability the generator sweeps and how it scales on HPC, see
[Data Generation Capabilities](data_generation_capabilities.md).

## What the code does

The project provides infrastructure to:

1. Register and materialize grid cases and scenario variants.
2. Generate and track topology perturbations, grid reinforcements, and
   operating-point / physics perturbations (load, dispatch, per-branch admittance,
   bus shunts, cost structure).
3. Model network expansion by synthesizing new equipment on both the generation
   side (generators, greenfield buses, transformers, bus-tie splits) and the load
   side (new demand), applied singly or as cascaded, connectivity-checked
   multi-step build-outs.
4. Enumerate, screen, and prioritize contingencies across a credibility-labeled
   ontology (N-1, N-2, N-k, common-mode, sequential, cascades).
5. Run solver workflows across multiple solver frameworks (PowerModels.jl, ExaGO,
   pandapower) on CPU and GPU.
6. Preserve complete run artifacts for both feasible and infeasible runs, so the
   output is usable for classification as well as regression.
7. Validate physical consistency and record validation status.
8. Maintain queryable run registries and integrity manifests.

## Production-scale execution

The generator runs as a **resumable, chained map/reduce campaign** designed to
saturate leadership-class HPC allocations — thousands of concurrent ranks across
64+ nodes, chained across many walltime windows, with Lustre-aware sharding and
zstd archiving. Campaigns of billions of configurations are a supported operating
point. See [Data Generation Capabilities](data_generation_capabilities.md) and
[Resumable Campaigns](resumable_campaigns.md) for details.

## Governing principles

- Never mix results across tasks.
- Never overwrite prior attempts.
- Keep DC approximations explicitly labeled as DC approximations.
- Keep raw solver-native files intact.
- Separate raw, intermediate, normalized, and derived products.
