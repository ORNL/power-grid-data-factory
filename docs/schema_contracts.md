# Schema Contracts (Informal)

This page documents practical field-level expectations for campaign artifacts.

Goal: reduce accidental schema drift while keeping process lightweight.

## Compatibility Rules

- Additive changes are preferred.
- Do not rename or remove existing fields without a documented migration note.
- Preserve semantic meaning of existing fields across runs.
- When behavior changes, update docs/evolution_log.md with expected data impact.

## Candidate JSONL Contract

Expected top-level fields for round execution inputs:

- candidate_id: unique candidate identifier string
- case_id: canonical grid case identifier
- operating_point: object with per-category perturbation content
- contingency: object describing event type and components
- contingency_order: integer order (1, 2, ..., K)
- contingency_class: ontology class string
- novelty_score: float in [0,1]
- active_constraint_score: float in [0,1]
- security_boundary_score: float in [0,1]
- contingency_severity_score: float in [0,1]
- physical_credibility_score: float in [0,1]
- model_uncertainty_score: float in [0,1]
- estimated_compute_cost: positive float

Notes:

- Additional fields are allowed.
- Missing optional score fields may be defaulted by pipeline scripts.

## Campaign Ledger Contract

Campaign root:

- data/campaigns/<campaign_id>/

Expected artifacts:

- candidate_registry.parquet or candidate_registry.jsonl
- acquisition_decisions.parquet or acquisition_decisions.jsonl
- diversity_ledger.parquet or diversity_ledger.jsonl
- active_constraint_ledger.parquet or active_constraint_ledger.jsonl
- security_boundary_ledger.parquet or security_boundary_ledger.jsonl
- contingency_portfolio.parquet or contingency_portfolio.jsonl
- screening_audit.parquet or screening_audit.jsonl
- round_summaries/

Fallback note:

- JSONL is an accepted fallback when parquet dependencies are unavailable in environment.

## Sample Record Contract

Per-shard solve records:

- `<runs_root>/ac_opf/samples.jsonl`

Written by both campaign map workers ([run_campaign_ac_opf_round.py](../scripts/run_campaign_ac_opf_round.py),
[run_campaign_exago_ac_opf_round.py](../scripts/run_campaign_exago_ac_opf_round.py))
through the shared `round_runner._sample_record`. Current `schema_version` is `1.1`.

Every solved candidate is persisted regardless of outcome, so the corpus stays
comprehensive of both feasible and infeasible operating configurations.

Core fields:

- schema_version, run_id, candidate_id, task, case_id, topology_id, operating_point_id, contingency_set_id, solver_id
- success: boolean (true only for a converged/optimal solve)
- termination_status: raw backend status string (e.g. `LOCALLY_SOLVED`, `LOCALLY_INFEASIBLE`, `timeout`, `nonconverged`)
- feasibility_label: normalized label for feasibility-vs-infeasibility classification — one of `feasible`, `infeasible`, `indeterminate`, `error`
- objective, solve_time, wallclock_seconds
- inputs: `{resolved_case, candidate}` (full parametrization retained as classification features)
- result: lean solver result (stdout/stderr stripped; ExaGO bus records carry `mult_Pmis`/`mult_Qmis` duals)
- runtime_metadata

Label semantics (`classify_feasibility`):

- `feasible` — `success == true`, or a solved/optimal status.
- `infeasible` — proven-infeasible status (contains `INFEASIBLE`, excluding the ambiguous `DUAL_INFEASIBLE`/`INFEASIBLE_OR_UNBOUNDED`).
- `indeterminate` — solver gave up without proving infeasibility (timeouts, iteration/limit, ambiguous statuses).
- `error` — model/software/numerical fault (`INVALID_MODEL`, exceptions, `NUMERICAL_ERROR`, ...).

Binary feasibility classifiers should train on the `feasible` vs `infeasible`
subset and treat `indeterminate`/`error` as excluded/unlabeled. ExaGO reports a
generic `nonconverged` status, which maps to `indeterminate` unless the adapter
surfaces a proven-infeasibility signal.

## PF Candidate and Sample Contract

PF candidates use the validated `PFCandidate` schema and include `task: pf`,
AC-OPF source lineage, parent network/topology IDs, a preassigned dataset split,
both parent and variant topology hashes, the exact resolved anchor case, complete stable-ID generator
controls, control-distance metadata, and an explicit response policy. Requiring
the resolved case prevents workers from reconstructing a different operating
condition from a mutable base case.

`controls` contains stable-ID generator `pg`, optional `qg`, and `vg` values,
plus transformer taps/shifts and shunts. Active generators must have controls;
unknown generator IDs and conflicting voltage targets at a shared generator bus
are rejected. `response_policy.policy_id` is one of
`fixed_controls_slack_loss`, `reserve_participation`, `fixed_participation`, or
`governor_droop`; the latter two require explicit weight maps.

PF samples are written separately to `<runs_root>/pf/samples.jsonl`, schema
version `1.1`. In addition to the common fields they contain source and parent
lineage, split, control method/distance/stratum, response policy ID, and an
`outcome_class`: `converged_valid`, `converged_invalid`, `nonconvergent`,
`islanded`, or `software_model_error`.

Schema 1.1 also provides orthogonal filtering labels:

- `solver_convergence`: `converged`, `not_converged`, or `error`;
- `equation_balance_status`: `satisfied`, `violated`, or `not_evaluated`;
- `operational_status`: `within_limits`, `limit_violating`, or `not_evaluated`;
- `anchor_consistency_status`: `consistent`, `inconsistent`, `not_applicable`,
  or `not_evaluated`.

A converged PF state with near-zero equation residuals and voltage or thermal
violations is therefore labeled `converged` / `satisfied` /
`limit_violating`. It remains a usable solved state for equation-learning tasks
but is excluded by an `operational_status == within_limits` filter when building
secure-operation datasets. `outcome_class` remains for compatibility and
partition layout.

The AC-OPF anchor index is Parquet with JSON strings for controls, resolved
cases, and source candidates. Its sidecar manifest records source paths, row
count, schema version, source SHA-256 values, and the index SHA-256. A sibling
`parent_split_registry.parquet` assigns each parent network to exactly one of
`train`, `validation`, `test`, or `ood`.

PF workers also write one outcome-specific copy under
`<runs_root>/pf/outcomes/<outcome_class>/samples.jsonl`. Campaign metadata uses
`diversity_ledger`, `active_constraint_ledger`, `security_boundary_ledger`, and
`pf_coverage_ledger`; the reducer appends ordinary rows and aggregates active
constraints by `(constraint_family, component_id)`.

## Run Registry Contract

Run registry files:

- data/runs/run_registry.jsonl
- data/runs/run_registry.parquet

Core execution fields include:

- run_id, task, case_id, topology_id, operating_point_id, solver_id, attempt_id
- numerical_status, preservation_status, objective
- runtime
- wallclock_seconds
- mpi_processes
- gpu_enabled
- gpu_type

## Round Summary Contract

Round summary directory:

- data/campaigns/<campaign_id>/round_summaries/

Expected files per round:

- round_<idx>_summary.json
- round_<idx>_selected_candidates.jsonl

Minimum summary metadata:

- campaign_id
- round_index
- budget
- selected_count
- queue_counts
- timestamp_utc
