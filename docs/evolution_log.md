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

## 2026-10-23 - PowerModels generator costs scaled to per-unit power

- Change: `to_powermodels_data` in `julia/run_opf.jl` and `julia/run_opf_expansion.jl` now multiplies the cost coefficients by `baseMVA^2` (quadratic) and `baseMVA` (linear). This matches `PowerModels.make_per_unit!`.
- Why: MATPOWER/pglib costs are in $/MWh, but `pg` is in per-unit. Before this fix, objectives with linear-only costs were `baseMVA` times too small (case300: 5652 vs 565219.97). Cases with quadratic costs also solved a different dispatch tradeoff from pglib.
- Expected data impact:
  - PowerModels AC-OPF (and DC-OPF, relaxed and corrective tasks) and SCOPF objectives are now in $/h and match pglib (case5/14/300 verified exactly).
  - Nodal-balance duals (LMPs) scale by the same factor.
  - In the 25 pglib cases with nonzero `c2`, the dispatch can also change.
  - Attempts created before this change use the old scale. For linear-only cases, multiply their objective by `baseMVA`; quadratic-cost cases need a re-solve to be comparable.
- Rollback note: Remove the `base_mva` factors on `c2`/`c1`.
- Related files: julia/run_opf.jl, julia/run_opf_expansion.jl
- Related run/campaign ids: every PowerModels attempt that started after this change, including ultrascale_3b rounds launched after 2026-10-23 and riker_scopf_n1_v1. SCOPF pilots 194284/194365 and earlier AC-OPF rounds use the old scale.

## 2026-10-23 - SCOPF screen-then-solve with explained contingency exclusions

- Change: Added `--screen-contingencies` to `scripts/run_scopf.py` (the `screen_contingencies` option in `julia/run_batch.jl`). Each N-1 contingency is solved alone first. Failures are re-solved with constraint groups removed (post-contingency thermal, generator-voltage link, post-contingency angle, thermal and voltage link together) and labelled with `screening_outcome`, `security_status`, `infeasibility_class`, `sufficient_relaxations`, `explanation` and numeric `evidence`. The coupled PMSC `c1` SCOPF then runs on the admitted contingencies only. Also added the standalone ablation diagnostic `julia/diag_scopf.jl`, with `configs/slurm/riker_scopf_diag.sbatch`.
- Why: The `pglib_opf_case300_ieee` SCOPF pilot (5/20/50 N-1) was LOCALLY_INFEASIBLE in every set, although the base AC-OPF solves. The diagnostic traced this to 5 individual contingencies (branch_000269, branch_000364, branch_000205, gen_000011, gen_000012) that `c1` cannot secure without corrective redispatch: emergency-rating overloads and/or generator voltage-setpoint locks. All other contingencies solve.
- Expected data impact: Screened SCOPF attempts gain `normalized/contingency_screening.json`, and `admitted_contingency_count` / `excluded_contingencies` in the normalized result and the report. Unscreened runs are unchanged. Excluded contingencies are kept as labelled data, not discarded. If no contingency is admitted, the attempt ends with `termination_status = no_admissible_contingencies`.
- Rollback note: Omit `--screen-contingencies` (or `SCREEN`); the original coupled solve path is unchanged.
- Related files: julia/run_batch.jl, julia/diag_scopf.jl, scripts/run_scopf.py, configs/slurm/riker_scopf_pilot.sbatch, configs/slurm/riker_scopf_diag.sbatch, tests/test_run_scopf.py, docs/setup.md, docs/scripts_reference.md, docs/schema_contracts.md, docs/data_generation_capabilities.md
- Related run/campaign ids: pilot 194284 (unscreened, infeasible), diagnostics 194329–194331, screened pilot 194365

## 2026-08-12 - ExaGO campaign: dual capture, verbosity, core-dump robustness, and normalized feasibility labels

- Change: Hardened the ExaGO GPU campaign path and made the corpus classification-ready. (1) The ExaGO adapter now captures the power-balance duals on every bus: `_parse_exago_json_export` reads `LAM_P`/`LAM_Q` into `mult_Pmis`/`mult_Qmis`, and the stdout fallback `_parse_exago_solution` reads columns 7/8 of the bus summary — matching the multipliers the single-case and PowerModels paths already return. (2) `PGDF_EXAGO_VERBOSE` toggles maximum ExaGO/HiOp verbosity (`-hiop_verbosity_level 12 -log_view -options_left no`); when set, the worker writes one per-shard `solver_verbose.log` next to `samples.jsonl` capturing each attempt's stdout/stderr with a header (one file/shard keeps Lustre inode cost flat). (3) `opflow` is launched through a `bash -c 'ulimit -c 0; export HSA_ENABLE_COREDUMP=0; exec "$@"'` guard so a GPU/IPOPT abort on an infeasible case fails cleanly instead of writing a ~1.6 GB `core`/`gpucore.*` file into the CWD (previously a quota risk at campaign scale); the caller still falls back to IPOPT. (4) A shared `classify_feasibility(result)` in `round_runner.py` maps solver-specific `termination_status` onto a normalized `feasibility_label` (`feasible`/`infeasible`/`indeterminate`/`error`) added to every `samples.jsonl` record and to both workers' round reports; `SAMPLE_SCHEMA_VERSION` bumped `1.0`→`1.1`.
- Why: The campaign discarded the duals ExaGO already computes; infeasible solves were stored but only weakly labeled (raw `success` + backend-specific status string conflating proven infeasibility with timeouts/model errors); and GPU crashes on genuinely-infeasible contingency cases wrote multi-GB core dumps that would exhaust shared-filesystem quota across a large campaign.
- Expected data impact: `samples.jsonl` gains `feasibility_label` and `schema_version` `"1.1"` (additive; existing consumers unaffected); ExaGO bus records now carry `mult_Pmis`/`mult_Qmis`; no change to which candidates are persisted (both feasible and infeasible remain first-class). `solver_verbose.log` appears only when `PGDF_EXAGO_VERBOSE` is set. Binary feasibility classifiers should train on the `feasible` vs `infeasible` subset and exclude `indeterminate`/`error`.
- Rollback note: Revert the four files below. Unset `PGDF_EXAGO_VERBOSE` to disable the per-shard log without a revert; the core-dump guard is transparent (drop the `guarded` wrapper in `_launch_command` to restore direct launch). Older readers ignore the additive `feasibility_label` field.
- Related files: src/grid_data_factory/solvers/exago_adapter.py, src/grid_data_factory/campaigns/round_runner.py, scripts/run_campaign_exago_ac_opf_round.py, scripts/run_campaign_ac_opf_round.py, docs/scripts_reference.md, docs/schema_contracts.md
- Related run/campaign ids: verbose_smoke (jobs 5247985, 5248272, 5248408)

## 2026-08-03 - Adaptive campaign execution pipeline implemented

- Change: Added executable adaptive campaign pipeline scripts for operating-point generation, contingency expansion, screening, queue-based selection, and AC-OPF round execution with ledger updates.
- Why: Move from scaffold/stub behavior to reproducible round-based campaign execution.
- Expected data impact: Increased richness and traceability; selected candidates and post-solve descriptors are persisted for each round.
- Rollback note: Revert to prior script revisions and use minimal non-adaptive generation path.
- Related files: docs/adaptive_campaign_strategy.md, docs/scripts_reference.md, scripts/bootstrap_adaptive_campaign.py, scripts/run_adaptive_campaign_round.py, scripts/run_campaign_ac_opf_round.py
- Related run/campaign ids: pilot_pglib_14_57_118

## 2026-08-05 - HPC-aware scale, I/O, and walltime-resilience hardening

- Change: Removed hidden `O(n²)`/heavy hot paths and cut Lustre metadata pressure for 150M-scale rounds. (1) `sample_unit_vector` now draws Latin-hypercube strata via an affine-cipher bijection `(a_d·idx+b_d) mod total` (`_coprime_multiplier` + SplitMix64 `_mix64`) in `O(1)`/sample instead of reshuffling a length-`total` permutation per call; legacy path preserved as `sample_unit_vector_legacy`. (2) `run_campaign_round` records decisions via a `candidate_id → record` dict lookup instead of an `O(n²)` linear scan. (3) Ledgers are slim by default — one aggregate decision row/round and `candidate_registry` skipped — with `CAMPAIGN_FULL_LEDGERS=1` to restore full per-candidate ledgers. (4) `SampleSink` keeps one `samples.jsonl` handle open per shard (collapsing ~15M `open+write+close`/round to ~8192), flushing every 200 records. (5) `SampleSink` flushes+`fsync`s on Slurm walltime signals: SIGTERM (automatic, before SIGKILL, then terminates) and SIGUSR1 (early warning via map `srun --signal=USR1@120`, keeps solving); picker `bash` uses `trap 'true' USR1` to survive the warning. (6) `select_ac_evaluations` short-circuits when `budget >= len(candidates)`: a single `O(n)` constraint-filtered pass (label `full_budget`) that skips `build_queues` (6 sorts + a `dict()` copy of every candidate in two queues, ~20 min and ~190 GB RSS at 15M); budgets below the candidate count keep the full multi-queue strategy. (7) `shard_selected_candidates.py --stream` splits the selected JSONL line-by-line to one append handle per shard (raising `RLIMIT_NOFILE`), computing coverage counts and targeted backfill in streaming passes and scanning the pool only for its bucket set; the in-memory path previously loaded the whole selected file *and* the pool (~180 GB each at 15.12M → ~360 GB OOM) and re-read the input a second time to count lines for the manifest. (8) `run_adaptive_campaign_round.py` streams full-budget selection: when the budget covers every candidate it calls `run_campaign_round_streaming`, reading the screened JSONL line-by-line and writing the selected JSONL in place with per-bucket constraint counters, removing the last ~170 GB peak (the in-memory candidate list plus a full `dict()` copy in `assign_selection_reason`).
- Why: At `PER_CASE=210000` × 3 cases × 24 contingencies (~15M candidates/round) the `O(n²)` sampler and selection scans stalled rounds before any solve, the queue-ranking `build_queues` pass burned ~20 min and ~190 GB RSS even though the budget covered nearly all candidates, the duplicated ledgers risked OOM on 230 GB nodes, and per-sample file reopen saturated Lustre metadata; walltime kills could lose buffered samples.
- Expected data impact: `samples.jsonl` output is byte-identical to the prior writer; slim mode omits `candidate_registry` and writes one aggregate `acquisition_decisions` row/round (neither is read by the reducer); when the budget covers all candidates the selected set equals every constraint-passing candidate (only the `selection_queue` label changes to `full_budget`); resume is unaffected (`_loaded_sample_ids` tolerates a truncated trailing line). No change to solved-state records or reduce output.
- Rollback note: Revert to `sample_unit_vector_legacy`, restore the linear decision scan, set `CAMPAIGN_FULL_LEDGERS=1` (or revert the gate), use `_append_sample` in the AC map loop instead of `SampleSink`, remove the `budget >= len(candidates)` fast path in `selector.py`, and drop `--signal=USR1@120` / the `trap 'true' USR1` line from the sbatch template.
- Operational note: Submit from a clean shell (`unset PYTHONUSERBASE PYTHONNOUSERSITE`); do not `module load python/3.7-anaconda3` before `sbatch`/`drive_campaign.py`, or `PYTHONUSERBASE` leaks via `--export=ALL` and compute-node `python3.11` fails on `import yaml`. Keep the per-round budget ≥ enumerated candidate count so the selection fast path engages.
- Related files: src/grid_data_factory/scenarios/operating_point_generation.py, src/grid_data_factory/campaigns/campaign_round.py, src/grid_data_factory/campaigns/adaptive_campaign.py, src/grid_data_factory/campaigns/round_runner.py, src/grid_data_factory/acquisition/selector.py, scripts/run_adaptive_campaign_round.py, scripts/run_campaign_ac_opf_round.py, scripts/shard_selected_candidates.py, configs/slurm/andes_powermodels_acopf_mapreduce_10n_36h.sbatch, docs/resumable_campaigns.md
- Related run/campaign ids: ultrascale_150m


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

## 2026-10-23 - PowerModels cost per-unit scaling fix and legacy backfill (`per_unit_scaled_v2`)

- Change: `julia/run_opf.jl` now scales MATPOWER costs to per-unit (`c2*B^2`, `c1*B`, `c0`) and tags results with `cost_convention = per_unit_scaled_v2`. Legacy records are handled in two ways. Linear-cost records are exactly rescaled in place by `scripts/backfill_cost_scaling.py`: `pg_cost = c1*B*pg + c0`, `lam_kcl_r/i *= B`, and all three objective fields are recomputed and verified against the stored objective (rel tol 1e-6). Quadratic-cost records are re-solved with `scripts/repair_failed_campaign_cases.py --select legacy-quadratic`.
- Why: The legacy runs evaluated MW-unit costs against per-unit `pg`. Linear cases kept the correct dispatch, but their objective and LMPs were off by a factor of 1/B. Quadratic cases converged to the wrong dispatch.
- Expected data impact: Linear records get `cost_rescale.status = exact_linear` (or `no_solution`), and their report rows are updated. Quadratic records are replaced by re-solved records carrying `reason = cost_convention_quadratic_resolve` and `previous_objective_legacy`. Active generators missing from the solution (isolated buses) are excluded and counted in `absent_active_generators`. Warm-start byte offsets, PF prep and anchor audits built from pre-fix files must be regenerated.
- Rollback note: None for the rewrite. Unchanged lines are byte-identical, and rescaled lines preserve the old objective in `cost_rescale.old_objective`.
- Related files: julia/run_opf.jl, scripts/backfill_cost_scaling.py, scripts/repair_failed_campaign_cases.py, configs/slurm/riker_backfill_cost_scaling.sbatch, configs/slurm/riker_quadratic_resolve.sbatch, tests/test_backfill_cost_scaling.py
- Related run/campaign ids: backfill `cost_rescale_v2` (job 194423), re-solves `riker_quadratic_resolve_v2_r000_r006` (job 194463, runs_3b rounds 0-6) and `riker_quadratic_resolve_v2_aux` (job 194464, hsl_full, hsl_almost_restart, ma27_ma57, grid_readiness, long_timeout); merged in place atomically without backups
