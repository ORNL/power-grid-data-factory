# Configuration Reference

This document describes every configuration file under [`configs/`](../configs). For
each file it lists its purpose, the key fields it defines, and the script or module
that consumes it. Files are grouped by whether they are actively loaded by code or
kept as declarative reference specifications.

Unless noted otherwise, all files are YAML and are read with `yaml.safe_load`.

## Actively consumed by code

### `sources.yaml`
- **Purpose:** Canonical registry of external case sources and per-case source
  identity metadata (grid family, origin, MATPOWER file name, bus count, equivalence
  relationships).
- **Key fields:** `sources.<repo>.type` (`git`), `url`/`repository`, `destination`,
  `git_commit`, and per-case `cases[]` entries.
- **Consumed by:** [src/grid_data_factory/sources/registry.py](../src/grid_data_factory/sources/registry.py)
  and the source scripts ([download_sources.py](../scripts/download_sources.py),
  [inspect_sources.py](../scripts/inspect_sources.py),
  [audit_case_sources.py](../scripts/audit_case_sources.py),
  [validate_sources.py](../scripts/validate_sources.py), and the manual-download and
  AC-OPF runners).

### `operating_points.yaml`
- **Purpose:** Declares the operating regimes and correlated-sampling noise used when
  generating operating points for a case.
- **Key fields:** `operating_points.regimes[]`, `correlated_sampling.local_noise_stddev`.
- **Consumed by:** [scripts/create_operating_point.py](../scripts/create_operating_point.py).

### `phase1_calibration.yaml`
- **Purpose:** Defines the Phase-1 cross-solver calibration gate: which cases to run,
  convergence requirements per solver, accepted PowerModels termination statuses, the
  ExaGO-vs-pandapower agreement tolerance, and the report path.
- **Key fields:** `phase1.cases[]`, `require_*_converged`,
  `accepted_powermodels_termination_status[]`,
  `max_pandapower_vs_exago_ipopt_relative_diff`, `report_path`.
- **Consumed by:** [scripts/phase1_gate.py](../scripts/phase1_gate.py).

### `campaign_default.yaml`
- **Purpose:** Default adaptive-campaign strategy: acquisition-budget queue fractions,
  portfolio constraints, operating regimes, security-margin bands, screening thresholds,
  contingency policy, and stopping criteria.
- **Key fields:** `acquisition_budget.*` (queue fractions), `portfolio_constraints.*`,
  `operating_regimes[]`, `security_margin_bands.*`, `screening.*`,
  `contingency_policy.*`, `stopping_criteria.*`.
- **Consumed by:** [src/grid_data_factory/campaigns/adaptive_campaign.py](../src/grid_data_factory/campaigns/adaptive_campaign.py)
  via the campaign round scripts
  ([run_adaptive_campaign_round.py](../scripts/run_adaptive_campaign_round.py),
  [run_campaign_ac_opf_round.py](../scripts/run_campaign_ac_opf_round.py),
  [bootstrap_adaptive_campaign.py](../scripts/bootstrap_adaptive_campaign.py),
  [reduce_campaign_shards.py](../scripts/reduce_campaign_shards.py)) and the Slurm
  map/reduce sbatch scripts. The `stopping_criteria` block is read by
  [StoppingConfig.from_campaign_config](../src/grid_data_factory/campaigns/scheduler.py).

### `campaign_ultrascale.yaml`
- **Purpose:** Ultra-scale variant of `campaign_default.yaml` with rebalanced queue
  fractions and non-zero per-bucket portfolio caps for very large round budgets.
- **Key fields:** same schema as `campaign_default.yaml`; notably non-zero
  `portfolio_constraints.max_per_grid/max_per_regime/max_per_contingency_class`.
- **Consumed by:** [scripts/launch_ultrascale_campaign.py](../scripts/launch_ultrascale_campaign.py)
  (default `--config`).

## Declarative reference specifications

These files describe intended solver, storage, and validation behavior. They are
committed as the project's specification of record but are not yet loaded by a runtime
config loader; individual scripts currently encode the equivalent settings directly.

### `solvers.yaml`
- **Purpose:** Registry of solver identities across frameworks (framework, task,
  formulation, optimizer) — e.g. PowerModels DC/AC OPF, ExaGO SCOPF, pandapower AC-OPF.
- **Key fields:** `solvers[].solver_id`, `framework`, `task`, `formulation`, `optimizer`.

### `powermodels.yaml`
- **Purpose:** PowerModels.jl formulation/optimizer choices per task and Julia runtime
  tuning.
- **Key fields:** `powermodels.dc_opf`, `ac_opf`, `ac_pf`, `runtime.*`
  (`compiled_modules`, `julia_num_threads`, `openblas_num_threads`).

### `exago.yaml`
- **Purpose:** Enables ExaGO OPFLOW and SCOPFLOW paths and binds them to solver ids.
- **Key fields:** `exago.opflow.enabled/solver_id`, `exago.scopflow.enabled/solver_id`.

### `contingencies.yaml`
- **Purpose:** Contingency enumeration policy and the selection-stream mix used to
  balance coverage vs. severity vs. diversity vs. uncertainty vs. extreme events.
- **Key fields:** `contingencies.enable_n1/enable_n2/enable_sequential_n1n1`,
  `selection_stream_mix.*`.

### `validation.yaml`
- **Purpose:** Numerical tolerances for post-solve validation of solutions.
- **Key fields:** `validation.power_balance_tolerance_pu`, `voltage_tolerance_pu`,
  `generator_limit_tolerance_pu`, `branch_limit_tolerance_pu`,
  `objective_relative_tolerance`.

### `storage.yaml`
- **Purpose:** Materialization mode for stored artifacts.
- **Key fields:** `storage.materialization_mode` (e.g. `full`).

### `preservation.yaml`
- **Purpose:** Preservation and archiving policy for run outputs.
- **Key fields:** `preservation.retain_local_directory`, `create_archive`,
  `archive_format`, `verify_after_archive`, `secondary_copy.*`.
- **Referenced by:** [docs/production_readiness_checklist.md](production_readiness_checklist.md).

### `case_portfolio.yaml`
- **Purpose:** The intended case portfolio, split into cases available now from the
  PGLib clone and cases that require manual TAMU data acquisition.
- **Key fields:** `portfolio.automatic_available_now[]`,
  `portfolio.add_after_manual_tamu_acquisition[]`.

## Slurm configuration

The [`configs/slurm/`](../configs/slurm) directory holds HPC batch scripts and the
default ultra-scale case list. See [configs/slurm/README.md](../configs/slurm/README.md)
and [scripts/launch_ultrascale_campaign.py](../scripts/launch_ultrascale_campaign.py)
for how rounds are planned and submitted.
