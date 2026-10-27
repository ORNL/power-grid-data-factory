# Implemented Capabilities and Limitations

This page distinguishes implemented workflows from planned scope and active
limitations. It complements the [capabilities overview](data_generation_capabilities.md),
which describes the dimensions and scale the campaign architecture targets.

## Usable workflows

- **PF campaign generation:** the Riker PowerModels workflow generates control
  and topology variants from AC-OPF anchors, validates and labels outcomes, and
  supports resumable map/reduce execution. See [Riker Complementary PF Campaign](riker_pf_campaign.md).
- **AC-OPF campaigns:** PowerModels and ExaGO campaign runners execute sharded,
  resumable rounds with per-sample provenance. The exact solver and hardware
  combinations are environment-specific; consult the corresponding runbooks.
- **SCOPF:** `scripts/run_scopf.py` supports static branch and generator N-1
  events. Optional screen-then-solve mode labels excluded contingencies with
  relaxation classes and evidence. `scripts/scopf_campaign.py` prepares,
  executes, and reports resumable multi-case campaigns. See [Setup](setup.md#scopf-campaigns-isolated-output-tree)
  and [Schema Contracts](schema_contracts.md#scopf-contingency-screening-contract).
- **Cost-convention migration:** `scripts/backfill_cost_scaling.py` exactly
  rescales verified legacy linear-cost PowerModels records and marks records
  already matching the current convention. Legacy quadratic-cost records require
  re-solving with `scripts/repair_failed_campaign_cases.py --select legacy-quadratic`.
  These are maintenance workflows, not new dataset-generation modes. Check the
  current campaign/job status before treating a migration as complete.
- **Preservation and analysis:** attempt integrity, artifact preservation,
  campaign reduction, and diversity-analysis utilities are available; see the
  [Script Reference](scripts_reference.md).

## Not implemented as standalone workflows

The following commands remain stubs and are not production execution paths:

- `scripts/canonicalize_cases.py`
- `scripts/register_case.py`
- `scripts/run_pf.py`
- `scripts/run_dc_opf.py`

PF dataset generation is available through the Riker campaign workflow, and
PowerModels also has campaign-oriented solver paths; the stub status above only
applies to those four standalone commands.

## Scale and reproducibility

Campaign tools support deterministic candidate generation, resumable sharding,
round chaining, and Lustre-aware storage patterns. Billion-configuration
campaigns are representable by the tooling, but this describes the design scale,
not a guarantee of completed end-to-end runs or a throughput promise. Solver
results may also depend on solver version, platform, numerical libraries, and
hardware; reproducibility claims should specify the workflow and environment.

## SCOPF scope

The current SCOPF runner accepts static branch or generator N-1 events and
rejects sequential or simultaneous N-k inputs. Screen-then-solve identifies
whether each supported single contingency can be secured by preventive dispatch;
it does not implement corrective redispatch. The campaign configuration and
runtime prerequisites are documented in [Setup](setup.md#scopf-campaigns-isolated-output-tree).
