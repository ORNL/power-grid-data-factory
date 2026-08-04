# Evolution Log

This is an informal running log of meaningful changes to the data factory strategy and workflow.

Use this file to record:

- what changed
- why it changed
- expected data impact
- rollback note

Keep entries concise and append-only.

## Entry Template

Copy this block for each update:

```markdown
## YYYY-MM-DD - short title

- Change:
- Why:
- Expected data impact:
- Rollback note:
- Related files:
- Related run/campaign ids:
```

## 2026-08-03 - Adaptive campaign execution pipeline implemented

- Change: Added executable adaptive campaign pipeline scripts for operating-point generation, contingency expansion, screening, queue-based selection, and AC-OPF round execution with ledger updates.
- Why: Move from scaffold/stub behavior to reproducible round-based campaign execution.
- Expected data impact: Increased richness and traceability; selected candidates and post-solve descriptors are persisted for each round.
- Rollback note: Revert to prior script revisions and use minimal non-adaptive generation path.
- Related files: docs/adaptive_campaign_strategy.md, docs/scripts_reference.md, scripts/bootstrap_adaptive_campaign.py, scripts/run_adaptive_campaign_round.py, scripts/run_campaign_ac_opf_round.py
- Related run/campaign ids: pilot_pglib_14_57_118

## 2026-08-04 - Slurm global scheduler switched to campaign-round dispatch

- Change: Updated Andes Slurm AC-OPF template to run adaptive campaign round dispatch (optional bootstrap + selected-candidate AC execution) instead of static MATPOWER file sweeping.
- Why: Align cluster scheduling with queue-based adaptive acquisition policy and avoid spending compute on unselected candidates.
- Expected data impact: Slurm jobs now write campaign round reports/ledgers keyed by campaign id and round index; execution scope follows budgeted selected-candidate sets.
- Rollback note: Restore prior `configs/slurm/andes_powermodels_acopf_small_10n_36h.sbatch` that builds a global case-file queue and runs `scripts/run_ac_opf.py` per case.
- Related files: configs/slurm/andes_powermodels_acopf_small_10n_36h.sbatch, configs/slurm/README.md
- Related run/campaign ids: all new Slurm submissions using campaign mode

## 2026-08-04 - Parallel map/reduce Slurm campaign execution added

- Change: Added deterministic candidate sharding and shard reducer utilities, plus a map/reduce Slurm template that executes shard campaigns in parallel and merges shard ledgers/reports into the target campaign.
- Why: Scale AC execution throughput while preserving deterministic, auditable ledger updates.
- Expected data impact: New shard campaign artifacts appear under `data/campaigns/<campaign_id>__r<round>__s<shard>/`; reducer emits a per-round merge report and appends merged rows to target campaign ledgers.
- Rollback note: Use the single-writer campaign-round Slurm template and ignore shard/reducer scripts.
- Related files: configs/slurm/andes_powermodels_acopf_mapreduce_10n_36h.sbatch, scripts/shard_selected_candidates.py, scripts/reduce_campaign_shards.py, configs/slurm/README.md, docs/scripts_reference.md
- Related run/campaign ids: map/reduce jobs for adaptive campaign rounds

## 2026-08-04 - Map/reduce dynamic shard pickup and coverage gates

- Change: Added dynamic shard pickup (work stealing) for map workers and added shard prechecks for coverage enforcement with deterministic pool-based backfill.
- Why: Improve cluster utilization under variable solve times and ensure selected round inputs include required dataset/topology buckets.
- Expected data impact: Shard manifests now include coverage diagnostics and optional backfill additions; map stage shard completion order may vary while reduce output remains deterministic.
- Rollback note: Set coverage/backfill options off and use fixed task-to-shard mapping behavior.
- Related files: configs/slurm/andes_powermodels_acopf_mapreduce_10n_36h.sbatch, scripts/shard_selected_candidates.py, configs/slurm/README.md
- Related run/campaign ids: map/reduce jobs with dynamic shard scheduling

## 2026-08-04 - Ultra-scale campaign launch profile and chained scheduler

- Change: Added an ultra-scale campaign config profile, default ultra-scale case list, and a launcher script for planning/submitting chained map/reduce rounds with full parameter exports.
- Why: Make long-running, high-throughput campaign execution reproducible and operationally manageable at very large solve counts.
- Expected data impact: Campaign runs can be scheduled as dependency-chained round sequences with explicit budget growth, shard counts, and coverage/backfill controls captured in submission plans.
- Rollback note: Submit rounds manually with direct `sbatch --export` invocations against the map/reduce Slurm template.
- Related files: configs/campaign_ultrascale.yaml, configs/slurm/ultrascale_cases.txt, scripts/launch_ultrascale_campaign.py, configs/slurm/README.md, docs/scripts_reference.md
- Related run/campaign ids: ultra-scale round chains launched via `scripts/launch_ultrascale_campaign.py`

## 2026-08-04 - Resumable campaigns, contingency-in-path naming, and top-level driver

- Change: Made attempt-directory allocation collision-safe under many concurrent workers (`create_next_attempt_directory` with an atomic in-progress marker and a finalize-name recheck); encoded the applied contingency into non-SCOPF run paths via a deterministic `contingency_slug`; added run-level resume (`--resume` skips candidates with a finalized attempt) and shard-level resume (`RESUME=1` reuses shards, skips `queue/done` shards, and marks shards done); and added `scripts/drive_campaign.py`, which resubmits the furthest incomplete round with `RESUME=1` and optionally chains remaining rounds via `afterok`.
- Why: Allow very large campaigns (tens of millions of solves) to run safely across many workers and to resume across multiple Slurm jobs without redoing finished work, while making each output directory self-describing.
- Expected data impact: Non-SCOPF run paths gain a `<contingency_slug>` level (base case is `ctg_base`, backward compatible); round reports gain `skipped_count` and compute `failure_fraction` over solvable candidates; resumed rounds produce identical merged ledgers and round markers.
- Rollback note: Revert to `create_attempt_directory` with local next-index helpers, omit the contingency path level and `contingency_set_id`, and submit rounds without `RESUME`/`drive_campaign.py`.
- Related files: src/grid_data_factory/storage/layout.py, src/grid_data_factory/contingencies/apply.py, scripts/run_campaign_ac_opf_round.py, scripts/run_ac_opf.py, scripts/run_exago_ac_opf.py, scripts/run_pandapower_ac_opf.py, scripts/drive_campaign.py, configs/slurm/andes_powermodels_acopf_mapreduce_10n_36h.sbatch, docs/resumable_campaigns.md, docs/architecture.md, docs/scripts_reference.md, configs/slurm/README.md
- Related run/campaign ids: ultra-scale 60M AC-OPF campaigns resumed via `scripts/drive_campaign.py`

## 2026-08-03 - K>2 contingency generation enabled

- Change: Extended contingency enumeration to support simultaneous order K>=3 via configurable max order and per-order counts.
- Why: Enable richer contingency portfolios beyond N-1 and N-2.
- Expected data impact: Higher-order contingencies appear in candidate sets; severity and compute-cost estimates increase with order.
- Rollback note: Set max order back to 2 or revert script change.
- Related files: scripts/enumerate_contingencies.py, docs/scripts_reference.md
- Related run/campaign ids: local smoke run on /tmp/ctg_k3.jsonl

## 2026-08-03 - Solver runtime provenance captured (MPI/GPU/timing)

- Change: Added runtime metadata capture for solver executions, including MPI process count, GPU usage/type, and wall-clock runtime; persisted in attempt artifacts and run registry fields.
- Why: Improve reproducibility and cross-machine auditability for Frontier/Andes and mixed solver workflows.
- Expected data impact: Each attempt now carries execution context in timing/runtime_metadata.json, and run registry records include wallclock_seconds, mpi_processes, gpu_enabled, and gpu_type.
- Rollback note: Remove runtime metadata write paths and registry columns if strict backward output shape is required.
- Related files: src/grid_data_factory/runtime_metadata.py, src/grid_data_factory/solvers/powermodels_adapter.py, scripts/run_ac_opf.py, scripts/run_campaign_ac_opf_round.py, scripts/compare_solver_consistency.py, src/grid_data_factory/storage/registry.py
- Related run/campaign ids: applies to all new attempts generated after this change

## 2026-08-03 - Dedicated ExaGO and pandapower AC-OPF run scripts

- Change: Added preservation-first AC-OPF runners for ExaGO and pandapower with per-attempt runtime metadata and run-registry append.
- Why: Ensure all supported solver families can be executed through consistent attempt layout and provenance capture.
- Expected data impact: New attempts for ExaGO and pandapower now include timing/runtime_metadata.json and registry fields for MPI/GPU/timing context.
- Rollback note: Use previous comparison-only path for ExaGO/pandapower and avoid solver-specific run scripts.
- Related files: scripts/run_exago_ac_opf.py, scripts/run_pandapower_ac_opf.py, docs/scripts_reference.md
- Related run/campaign ids: applies to new ExaGO/pandapower AC-OPF runs after this change

## 2026-08-03 - Native ExaGO JSON solution export enabled

- Change: Enabled ExaGO `-save_output` with `-opflow_output_format JSON` in the ExaGO AC-OPF runner and ingest this native file as the primary structured solution source.
- Why: Improve fidelity of saved labels (including reactive branch flows) for downstream HydraGNN OPF training conversion.
- Expected data impact: ExaGO attempts now preserve `raw_outputs/solver_native_files/exago_solution.json` and expose richer `raw_result.solution` fields than stdout-only parsing.
- Rollback note: Fall back to stdout-table parsing by removing native export flags from runner.
- Related files: scripts/run_exago_ac_opf.py
- Related run/campaign ids: case5 smoke attempts after this update

## 2026-08-03 - Compact run artifact path layout enabled

- Change: Simplified default attempt path layout by removing verbose segment folders while preserving meaningful identifiers (case, topology, operating point, solver).
- Why: Reduce path length and improve readability while retaining key context in directory names.
- Expected data impact: New runs use `data/runs/<task>/<case_id>/<topology_id>/<operating_point_id>/<solver_id>/attempts/<attempt_id>/`.
- Rollback note: Restore the previous path-segment implementation in `storage/layout.py` if verbose hierarchy is needed again.
- Related files: src/grid_data_factory/storage/layout.py, docs/architecture.md, docs/first_reproducible_run.md
- Related run/campaign ids: applies to all new attempts after this update
