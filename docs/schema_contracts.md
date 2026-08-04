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
