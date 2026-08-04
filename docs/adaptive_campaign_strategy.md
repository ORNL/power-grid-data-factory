# Adaptive Campaign Strategy (Default)

This project now treats dataset richness as a first-class optimization, validation, and auditing objective.

## Policy

Do not use random-perturbation-only generation followed by unilateral DC gating.

Use a coverage-constrained, multi-fidelity adaptive campaign with:

1. Independent acquisition queues.
2. Explicit budget quotas per queue.
3. Multi-trigger AC escalation.
4. Rejected-region AC audits.
5. Post-solve diversity and active-constraint ledgers.
6. Security-boundary trajectory analysis.
7. Physically credible contingency ontology and dependency-aware generation.

## Candidate Definition

Each candidate is represented as:

- grid case
- operating point
- topology or contingency
- task
- candidate-generation mechanism

Each candidate stores separate acquisition scores (not one collapsed scalar):

- novelty score
- active-constraint score
- security-boundary score
- contingency-severity score
- physical-credibility score
- model-uncertainty score
- estimated computational cost

## Queue-Based Selection

The selection stack uses independent queues:

- coverage
- active_set
- boundary
- credible_contingency
- severity_uncertainty
- audit

Default queue allocations are defined in [configs/campaign_default.yaml](../configs/campaign_default.yaml).

Selection is quota-driven with deduplication and portfolio constraints. Every candidate keeps all score fields regardless of which queue selected it.

## Required Campaign Artifacts

For each campaign, maintain:

- data/campaigns/<campaign_id>/candidate_registry.parquet
- data/campaigns/<campaign_id>/acquisition_decisions.parquet
- data/campaigns/<campaign_id>/diversity_ledger.parquet
- data/campaigns/<campaign_id>/active_constraint_ledger.parquet
- data/campaigns/<campaign_id>/security_boundary_ledger.parquet
- data/campaigns/<campaign_id>/contingency_portfolio.parquet
- data/campaigns/<campaign_id>/screening_audit.parquet
- data/campaigns/<campaign_id>/round_summaries/

Track workflow evolution in [evolution_log.md](evolution_log.md) and maintain field compatibility notes in [schema_contracts.md](schema_contracts.md).

## Implementation Modules

Core implementation modules:

- acquisition: queue construction, constraints, selector, uncertainty, audit sampling
- diversity: descriptors, nearest-neighbor novelty, clustering, duplicate detection, reporting
- constraints: normalized margins, active-set signatures, coverage ledger updates, targeted generation hints
- boundaries: security margin, stress directions, continuation, bisection, counterfactual distance
- screening: DC feature capture, voltage/reactive risk, multi-trigger escalation, screening audit rates
- contingencies: ontology, dependency graph, state-aware scoring, constrained beam search, sequential N-1-1 records
- campaigns: ledger management, round execution, orchestration, stopping checks

## Operational Workflow

1. Generate structured operating-point candidates from low-dimensional operational parameters.
2. Apply validity and physical-credibility checks.
3. Generate credible topology/contingency candidates.
4. Compute DC and AC-sensitive screening features.
5. Place candidates in independent acquisition queues.
6. Select by quotas and portfolio constraints.
7. Run high-fidelity PF/AC-OPF/SCOPF calculations.
8. Preserve all run artifacts.
9. Compute descriptors, margins, and active-set signatures.
10. Update ledgers and screening audits.
11. Generate targeted next-round candidates where coverage is weak.
12. Repeat until stopping criteria are met.

## Initial Sprint Scope

Start with:

- PGLib case14
- PGLib case57
- PGLib case118

Prior to scaling up, verify reproducibility of selection, quota enforcement, audit metrics, near-duplicate detection, and ledger correctness.

## Bootstrap Command

Run one full seed-to-selection round for the initial sprint scope:

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
PYTHONPATH=src python3.11 scripts/bootstrap_adaptive_campaign.py \
	--campaign-id pilot_pglib_14_57_118 \
	--config configs/campaign_default.yaml \
	--cases pglib_opf_case14_ieee pglib_opf_case57_ieee pglib_opf_case118_ieee \
	--per-case 500 \
	--budget 600 \
	--seed 7
```

After candidate selection, run AC-OPF and ledger updates for the selected set:

```bash
PYTHONPATH=src python3.11 scripts/run_campaign_ac_opf_round.py \
	--campaign-id pilot_pglib_14_57_118 \
	--round-index 0 \
	--selected-candidates-jsonl data/campaigns/pilot_pglib_14_57_118/round_summaries/round_000_selected_candidates.jsonl \
	--config configs/campaign_default.yaml \
	--solver-id powermodels_ac_opf_ipopt_campaign \
	--timeout-s 1200 \
	--continue-on-error
```
