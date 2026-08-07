# Documentation Index

This documentation describes the purpose, architecture, setup, and reproducibility workflow for Power Grid Data Factory.

## Start here

1. [Project Purpose and Scope](project_scope.md)
2. [Data Generation Capabilities](data_generation_capabilities.md) — variability, parametrization, and HPC scalability at a glance
3. [Architecture and Data Layout](architecture.md)
4. [Environment and Setup Guide](setup.md)
5. [External Dependencies and Data Sources](external_sources.md)
6. [ExaGO Frontier Build Notes](exago_frontier_build.md)
7. [Reproducibility Workflow](reproducibility.md)
8. [Script Reference](scripts_reference.md)
9. [First Reproducible Run Walkthrough](first_reproducible_run.md)
10. [First Real-Case Input Run with Registry Append](first_real_case_run.md)
11. [Three-Solver Runbook (ExaGO + pandapower + PowerModels)](three_solver_runbook.md)
12. [Production Readiness Checklist](production_readiness_checklist.md)
13. [Adaptive Campaign Strategy (Default)](adaptive_campaign_strategy.md)
14. [ExaGO Andes CPU Build and Run](exago_andes_cpu_build.md)
15. [GO Challenge MATPOWER Duplicate Audit](go_challenge_duplicate_audit.md)
16. [Schema Contracts](schema_contracts.md)
17. [Evolution Log](evolution_log.md)
18. [Configuration Reference](configuration_reference.md)
19. [Resumable Campaigns and the Top-Level Driver](resumable_campaigns.md)
20. [Enumeration-Time Feasibility Prefiltering](feasibility_prefiltering.md)

## Intended audience

- Researchers generating PF, DC-OPF, AC-OPF, and SCOPF datasets.
- Engineers validating solver outputs and preservation integrity.
- Contributors extending adapters, schemas, and HPC execution paths.

## Design intent

The codebase is preservation-first:

- Every simulation attempt is treated as an immutable scientific record.
- Raw outputs are never replaced by normalized or derived artifacts.
- Integrity checks and manifests are first-class requirements, not optional extras.
