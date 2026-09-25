# Riker Complementary Power-Flow Campaign

## Purpose

The Riker campaign generates AC power-flow (`pf`) samples around successful
AC-OPF states. Unlike AC-OPF, each PF solve receives explicit generator controls
and does not optimize them. The resulting corpus adds non-optimal controls,
topology outages, response policies, valid and invalid converged states, and
nonconvergence labels without sharing the Andes campaign namespace.

The design rationale and future research directions remain in
[Complementary PF Campaign Plan](complementary_pf_campaign_plan.md). This page
describes the implemented operator workflow.

## Implemented Pipeline

```text
Andes AC-OPF samples
  -> immutable Parquet anchor index and parent split registry
  -> seeded PF control/topology candidates
  -> atomic candidates.jsonl
  -> balanced consecutive shards
  -> persistent PowerModels.jl PF workers
  -> task- and outcome-partitioned samples
  -> deterministic campaign-ledger reduction
```

The implementation provides:

- stable-ID preservation and application of generator `pg`, `qg`, and `vg`,
  transformer taps/shifts, and shunts;
- immutable AC-OPF source checksums and deterministic parent-network splits;
- balanced active-power redispatch with generator-limit projection;
- post-projection control-distance enforcement;
- intact, N-1 branch, N-1 generator, and adjacent-branch N-2 topologies;
- reserve, fixed-participation, and governor-droop generator-outage responses;
- reactive-limit enforcement, PV-to-PQ conversion, and slack transfer;
- branch-flow reconstruction and physical validation;
- exact-anchor PF/AC-OPF state-consistency checks;
- separate valid, invalid, nonconvergent, islanded, and software/model outcomes;
- diversity, active-constraint, security-boundary, and PF-coverage ledgers;
- resumable dynamic shard execution and deterministic reduction.

## Prerequisites

Initialize the isolated Riker environment:

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
bash scripts/setup_riker_environment.sh
```

The runtime uses:

- `.venv-riker/bin/python`;
- `.software/bin/julia`, an Apptainer-backed Julia 1.10.10 wrapper;
- `julia/lockfiles/riker`;
- `.julia_depot_riker`.

The Slurm launcher exports these locations explicitly. The Python adapter also
selects `.julia_depot_riker` when the Riker project is selected and no depot is
specified.

## Build the Anchor Index

The anchor index is a generated scientific artifact and is not committed to the
repository. Build it from successful Andes AC-OPF shard samples:

```bash
.venv-riker/bin/python scripts/build_pf_anchor_index.py \
  'data/outputs/runs/mapreduce_round_*/shard_*/ac_opf/samples.jsonl' \
  --output data/derived/pf_anchors/andes_acopf_anchors.parquet
```

The builder accepts records with `task: ac_opf` and `success: true`. It skips
other records; it does not independently rerun AC-OPF validation. Inputs should
therefore come from the accepted Andes corpus selected for the PF campaign.

Outputs are:

- `andes_acopf_anchors.parquet`;
- `andes_acopf_anchors.parquet.manifest.json` with source paths, source SHA-256
  values, row count, schema version, and index SHA-256;
- `parent_split_registry.parquet` with deterministic 70/10/10/10
  train/validation/test/OOD assignment by parent network.

The index is written through an `.in_progress` file and atomically published.
Freeze the index and manifest for a campaign version; rebuilding from a changed
source corpus changes campaign provenance.

## Candidate Selection

Defaults are defined in [the Riker PF configuration](../configs/pf_campaign_riker.yaml).
Generation uses only anchors whose source candidate has no contingency by
default and samples at most 10,000 anchors with a seeded reservoir.

Control-distance quotas are:

| Stratum | Share | Realized normalized distance |
|---|---:|---:|
| exact consistency | 2% | 0 |
| near OPF | 18% | 0.005-0.03 |
| intermediate | 35% | 0.03-0.10 |
| large credible | 25% | 0.10-0.25 |
| boundary directed | 20% | 0.18-0.30 |

For non-exact states, the generator samples seeded random directions in active
power and voltage setpoints. Active power is clipped to `pmin`/`pmax`, then
rebalanced to preserve the anchor's total scheduled generation. Voltage targets
are clipped to bus limits. The proposal is iteratively rescaled until its actual
post-projection distance is in the selected band. Taps, shifts, and shunts are
currently preserved rather than sampled.

The current `boundary_directed` mechanism is a large-distance random band. It
does not yet implement continuation, gradient steering, trajectory search, or
bisection against a physical boundary.

Topology quotas are:

| Topology | Share | Selection |
|---|---:|---|
| intact | 50% | no outage |
| N-1 branch | 25% | branch from an AC-OPF flow-ranked criticality bucket |
| N-1 generator | 15% | non-reference generator from an output-ranked bucket |
| structured N-2 | 10% | two adjacent branches when possible |

N-1 criticality quotas are 40% critical, 25% intermediate, 25% benign, and 10%
random audit. Small cases fall back to the complete eligible non-reference
generator pool when a requested bucket contains only a reference generator.
N-2 events are adjacency-structured but are not currently criticality-ranked.

Generator-outage response quotas are 50% reserve participation, 25% fixed equal
participation, and 25% equal droop coefficients. Integer counts use deterministic
largest-remainder allocation, so small campaigns can differ by one sample from
nominal percentages.

Exact-consistency controls are always paired with intact topology. Candidate
identities, quota schedules, anchor traversal, and perturbations are reproducible
from the campaign seed. Later rounds use valid prior PF sample counts to favor
underfilled control-distance strata.

Generation streams expanded candidates directly to an `.in_progress` JSONL and
atomically renames it at completion. This bounds Python memory use, although each
candidate intentionally embeds its resolved case for immutable worker inputs.

## Validation and Outcomes

A converged solve is accepted for PF regression only when all configured checks
pass:

- active and reactive power residuals;
- non-slack active-control agreement;
- bus voltage bounds;
- generator reactive limits;
- branch thermal limits;
- complete bus, generator, and branch solution fields.

Exact-consistency candidates additionally compare bus voltage magnitude and
angle with the source AC-OPF solution. The default tolerances are in
[the campaign configuration](../configs/pf_campaign_riker.yaml).

Every completed solve is retained and classified as one of:

- `converged_valid`;
- `converged_invalid`;
- `nonconvergent`;
- `islanded`;
- `software_model_error`.

Only `converged_valid` is suitable for state-regression training. Other outcomes
form explicit quality-control and classification partitions.

## Output Layout

Each shard writes its authoritative records under:

```text
<RUNS_ROOT>/mapreduce_round_<round>/shard_<shard>/pf/samples.jsonl
<RUNS_ROOT>/mapreduce_round_<round>/shard_<shard>/pf/outcomes/<outcome>/samples.jsonl
```

Per-shard campaign metadata is stored under:

```text
data/outputs/campaigns/<campaign>__r<round>__s<shard>/
```

The reducer appends `diversity_ledger`, `security_boundary_ledger`, and
`pf_coverage_ledger` rows to the parent campaign. Active-constraint rows are
aggregated by `(constraint_family, component_id)`. Reduction and candidate
publication use atomic temporary files.

## Submit and Resume

Run the one-node smoke first:

```bash
configs/slurm/submit_riker_pf_smoke.sh
```

Submit the multi-node campaign after the smoke succeeds:

```bash
configs/slurm/submit_riker_pf_large.sh
```

Large-launch defaults are 100,000 candidates, 8 nodes, 32 tasks per node, 2,048
shards, and two hours. Override settings through the environment, for example:

```bash
COUNT=250000 NODES=16 SHARD_COUNT=4096 WALLTIME=04:00:00 \
CAMPAIGN_ID=riker_pf_complement_large_v1 \
  configs/slurm/submit_riker_pf_large.sh
```

The launcher refuses submission if the anchor index is absent. Shards are
balanced contiguous ranges of the generated JSONL. Workers claim consecutive
shard IDs from a shared queue dynamically, so completion order can differ from
shard order.

To continue an interrupted round, preserve the campaign ID, round, seed, count,
and shard count:

```bash
RESUME=1 CAMPAIGN_ID=riker_pf_complement_large_v1 \
  configs/slurm/submit_riker_pf_large.sh
```

Resume reuses a complete candidate file and existing shards, skips done shard
markers and already recorded candidate IDs, and exits immediately when a valid
reduce marker already exists.

## Current Limitations

- The production Andes anchor index must be built before the first Slurm smoke or
  large submission.
- Boundary-directed sampling is distance-banded random sampling, not a physical
  continuation or bisection method.
- N-2 selection covers adjacent branch pairs only and is not criticality-ranked.
- Adaptive generation currently corrects control-distance occupancy; it does not
  yet retarget topology, policy, or rare constraint-signature quotas.
- Resolved cases are embedded in candidates for immutability, so candidate JSONL
  storage can be substantial for very large networks even though generation is
  memory-bounded.
