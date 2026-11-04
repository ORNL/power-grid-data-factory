# Complementary Power-Flow Campaign Plan

> **Status (2026-09-25):** The baseline campaign is implemented and locally
> validated. For exact commands, runtime behavior, output paths, and current
> limitations, use [Riker Complementary PF Campaign](riker_pf_campaign.md).
> This document retains the scientific rationale and longer-term roadmap.

## Implementation Status

| Capability | Status |
|---|---|
| Immutable AC-OPF anchor index, checksums, and parent split registry | Implemented |
| Stable-ID controls, balanced redispatch, and post-projection distance bands | Implemented |
| Intact, flow-ranked N-1 branch, output-ranked N-1 generator, adjacent N-2 | Implemented |
| Reserve, fixed-participation, and droop generator response | Implemented |
| Reactive-limit enforcement, PV-to-PQ conversion, and branch-flow reconstruction | Implemented |
| Exact-anchor and physical validation with outcome partitions | Implemented |
| Persistent Julia map workers, contiguous shards, resume, and reduction | Implemented |
| Distance-stratum adaptation from prior valid PF samples | Implemented |
| Local fixed-mode PF sensitivity post-processing pilot | Validated, bounded pilot only; not production |
| Gradient/trajectory boundary targeting and continuation/bisection | Future work |
| PTDF/LODF, centrality, islanding-risk, and multi-mechanism N-2 ranking | Future work |
| Adaptive retargeting of topology, policy, and rare-signature quotas | Future work |

## Objective

The Riker PowerModels campaign complements the Andes `ultrascale_3b`
AC-OPF corpus. The implemented baseline generates data for the physical mapping

```text
topology + demand + specified non-optimal controls + contingency + response policy
    -> electrical state
```

It must not reuse the current AC-OPF candidate generator unchanged. That
generator primarily varies demand, availability, costs, network parameters,
topology, and contingencies before optimizing controls. The PF campaign instead
needs multiple credible specified control vectors for each retained operating
condition and topology.

## Source Corpus

Use successful PowerModels records from the Andes campaign as PF anchors:

```text
data/outputs/runs/mapreduce_round_*/shard_*/ac_opf/samples.jsonl
data/outputs/campaigns/ultrascale_3b/round_summaries/
```

Each anchor must retain:

- `source_ac_opf_run_id`, `candidate_id`, case, topology, operating point, and contingency;
- the resolved input case and original candidate;
- optimal generator `pg` and `qg`, and bus `vm` and `va` from the solution;
- solver status and objective;
- a deterministic parent-topology hash and preassigned dataset split.

Only successful, validated AC-OPF records are regression anchors. Failed or
indeterminate AC-OPF records may inform boundary sampling but must not provide
control targets.

Build a compact Parquet anchor index before generation. Do not make PF workers
scan the multi-round JSONL corpus. The index is immutable for a PF campaign
version and records the exact Andes source files and checksums.

## Split Policy

Assign `train`, `validation`, `test`, and `ood` by parent network before
generating any PF variants. Every operating condition, topology perturbation,
and contingency derived from one parent remains in that parent's split.

Recommended initial split by parent-network family:

| Split | Parent networks | Purpose |
|---|---:|---|
| train | 70% | control/state learning |
| validation | 10% | adaptive-policy tuning |
| test | 10% | unseen controls and contingencies |
| ood | 10% | entirely held-out topology families |

Store the assignment in a versioned `parent_split_registry.parquet`; never infer
the split from individual sample IDs.

## Independent Sampling Axes

Treat topology, operating condition, control distance, contingency, and response
policy as separate fields and coverage ledgers. A sample can be both
near-boundary and N-1, for example; those labels must not be collapsed into one
exclusive category.

### Topology quota

The implemented topology quotas are:

| Group | Share | Definition |
|---|---:|---|
| A | 50% | intact topologies represented in the Andes corpus |
| B1 | 25% | selected N-1 line or transformer variants |
| B2 | 15% | selected N-1 generator variants |
| B3 | 10% | structured N-2 variants |

Group A provides the strongest direct complement: same grid and demand, new
non-optimal controls. Parent-network split assignment prevents any derived
topology or control variant from leaking across train, validation, test, or OOD.

### Control-distance quota

For control vector $u$ and AC-OPF anchor $u^*$, record

$$
d_u(u,u^*) = \sqrt{\frac{1}{n_u}\sum_i
\left(\frac{u_i-u_i^*}{u_i^{\max}-u_i^{\min}}\right)^2}.
$$

Initial accepted-sample targets across topology groups A and B:

| Stratum | Share | Target $d_u$ |
|---|---:|---:|
| exact consistency | 2% | 0 |
| near OPF | 18% | $(0, 0.03]$ |
| intermediate | 35% | $(0.03, 0.10]$ |
| large credible | 25% | $(0.10, 0.25]$ |
| boundary-directed | 20% | current baseline: random projected distance in $[0.18, 0.30]$ |

Distance thresholds are starting values. Recalibrate them after the pilot using
convergence rates and state-space novelty, not merely candidate counts.

### Operating-condition modes

Within each control stratum, label and balance these modes:

- fixed load, varying controls: 35%;
- varying load, systematically balanced controls: 25%;
- jointly varying load and controls: 25%;
- fixed pre-contingency controls, varying topology: 15%.

## Balanced Control Generation

Start from the AC-OPF `pg` and generator-bus voltage `vm`. Phase 1 controls are
generator active-power setpoints and voltage setpoints; taps, phase shifters,
and shunts remain fixed until the canonical parser and PowerModels conversion
preserve their values and limits end to end.

Generate active-power redispatch directions with approximate pre-loss balance:

$$
\sum_g \Delta P_g = 0.
$$

The implemented baseline samples dense seeded random directions. The following
targeted direction families remain roadmap items:

- source/sink transfers between electrical regions;
- transfers between generator groups or technologies when metadata exists;
- renewable increase/decrease balanced by conventional generation;
- reserve-weighted participation groups;
- sparse pairwise transfers for local sensitivity;
- corridor-targeted transfers for boundary discovery.

Project every proposal onto generator availability and `pmin`/`pmax`, then
rebalance iteratively over generators with remaining headroom. Reject proposals
whose residual exceeds both 0.1% of total load and 1 MW. The slack generator may
absorb losses and only this small residual.

Record the unprojected direction, projected setpoints, participation factors,
residual mismatch, random seed, method ID, and $d_u$.

## Response Policies

Every contingency sample requires a `response_policy_id` and complete policy
parameters.

Initial policies:

| ID | Applies to | Behavior |
|---|---|---|
| `fixed_controls_slack_loss` | line/transformer | fixed `pg`, voltage targets, taps, and shunts; slack covers losses |
| `reserve_participation` | generator | replace lost `pg` proportional to upward reserve |
| `fixed_participation` | generator | replace lost `pg` using stored participation factors |
| `governor_droop` | generator | bounded droop-like response using configured coefficients |

All policies initially enforce reactive limits and allow PV-to-PQ conversion.
Also record slack generator selection, reactive-limit mode, PV-to-PQ behavior,
and whether corrective redispatch occurred. A post-contingency PF record remains
task `pf`; it must never be labeled SC-ACOPF.

## Contingency Selection

Do not sample components uniformly. Build criticality strata from inexpensive
base-state features and retain a random audit component.

The baseline ranks line/transformer N-1 candidates by absolute AC-OPF branch
real-power flow and generator N-1 candidates by absolute AC-OPF generator real
power. It allocates critical, intermediate, benign, and random-audit strata.
PTDF/LODF impact, centrality, corridor, islanding-risk, technology, and regional
reserve features remain future ranking inputs.

N-2 generation is structured and budgeted. The baseline selects adjacent branch
pairs when possible; additional same-corridor, impact-ranked, geographically
separated, and mixed-component mechanisms remain future work.

## Boundary Campaign

The configured baseline reserves twenty percent for a large-distance
`boundary_directed` band, but currently uses one-shot random projected draws.
The intended future implementation will scale a balanced control/load direction until the first
of these events occurs:

- branch thermal margin approaches zero;
- bus voltage approaches a limit;
- generator reactive margin approaches zero;
- PV-to-PQ conversion occurs;
- angle difference becomes large;
- PF changes from convergent to nonconvergent.

Use bisection between the last convergent and first nonconvergent points. Add
continuation PF only for a smaller, explicitly labeled voltage-stability subset.
Store converged states in the regression corpus and nonconvergent, islanded, or
numerically failed cases in a separate classification corpus.

## Required PF Record

Add these fields to the existing sample contract:

- `task: pf`, `source_ac_opf_run_id`, `parent_network_id`, `parent_topology_id`,
  `topology_hash`, `dataset_split`;
- `control_sampling_method`, `control_distance`, `control_distance_stratum`,
  proposed and applied controls;
- `response_policy_id`, participation factors, slack policy, reactive-limit
  policy, PV-to-PQ policy;
- solver and package versions, tolerances, convergence status, termination
  reason, iteration count;
- active/reactive residuals, slack adjustment, losses, voltage/thermal/generator
  limit violations, PV-to-PQ count;
- physical-state descriptor, novelty distances, and constraint signature.

Keep four outcome classes separate: converged-valid, converged-invalid,
nonconvergent, and software/model error. Only converged-valid records enter PF
state regression.

## Fixed-Mode PF Sensitivity Pilot

A separate local post-processing pilot labels derivatives at saved AC-OPF
operating points. It does not launch a PF solve, alter a source record, or
differentiate the AC-OPF optimizer. For fixed PV/PQ mode and controls $u$, it
forms the polar PF residual $F(z,u)=0$, assembles sparse $A=\partial F/\partial z$
and $B=\partial F/\partial u$, and solves

$$
A S_z=-B,
\qquad
S_z=\frac{\partial z}{\partial u}
$$

through sparse factorization and linear solves, without forming an explicit
inverse. The current label outputs are bus voltage magnitude (p.u.) and
non-reference bus
angle (radians) with respect to non-slack generator $P_g$ (MW) and PV-bus
voltage setpoint (p.u.). The slack generator $P_g$ is dependent and excluded.
For these state outputs, the direct output term is zero; future derived outputs
must include the full chain rule $S_y=H_zS_z+H_u$ and document units and stable
IDs. Branch-flow derivatives are not part of this pilot. Aggregate bus reactive
injection may be added only with its bus-level meaning explicit; individual
generator $Q_g$ allocation is not implied by the PF equations.

The bounded driver streams at most 500 records from one existing AC-OPF shard
and accepts at most eight. It verifies candidate-declared outages against the
resolved branch IDs from the intact MATPOWER case, stable bus/generator solution
IDs, one-reference supported bus types, finite derivatives, direct fixed-mode
PF residual, and central finite differences of each input-Jacobian column.
Labels retain the raw source-line checksum, source-file checksum, checksum of
the pristine source `inputs.resolved_case` (before saved solution values are
applied), topology and contingency IDs, control/state ordering, units, mode and
Q-limit proximity, QA values, runtime, Julia/PowerModels versions, and solver
convention. Each output matrix includes explicit dimensions and its control
column names. The saved AC-OPF primals are only operating points for PF
differentiation, not optimizer sensitivities or evidence of general
state-transition derivatives.

The initial measured run processed five accepted 14-bus records from a
500-line scan of one 922,262,750-byte shard. It took about 44 seconds and wrote
about 32 kB of JSONL labels. Maximum fixed-mode residual and input-Jacobian
finite-difference error were each below $10^{-11}$ in their respective units.
Two successful records were excluded because their resolved topology disagreed
with candidate outage metadata; failed solves and other case IDs were also
excluded. Q-limit-near and solver-tolerance headroom values are recorded
separately from the fixed PV/PQ mode.

The final corrected artifact is stored in
`data/derived/pf_sensitivities_pilot_v4/`; source JSONL and historical pilot
artifacts are immutable. The current dense
state-by-control matrix is suitable only for small cases. Large networks need
selected-control directions or adjoint output queries; sparse $A$ alone does
not make the full sensitivity matrix sparse. This pilot is not a production
campaign, does not define a complete sensitivity training corpus, and does not
cover PV-to-PQ transitions, active-limit mode changes, branch outputs, or
validation on PF-only records.

## Novelty and Adaptive Selection

Compare each candidate against both the Andes AC-OPF anchor index and accepted
PF samples. Use three descriptor groups:

1. Input: topology hash, normalized loads, renewable injections, controls,
   contingency, policy, and $d_u$.
2. State: voltage and angle quantiles, branch loading, losses, reactive reserve,
   residuals, PV-to-PQ count, and convergence.
3. Constraint signature: near/active thermal, voltage, active/reactive generator,
   tap, and shunt limits.

Downsample candidates that are near-identical in all three groups. Do not reject
solely because their load vector resembles an AC-OPF sample; changed controls
are the intended enrichment.

After each batch, update coverage by parent topology, topology group,
contingency mechanism, response policy, distance stratum, margin band, and
constraint signature. Increase subsequent proposal weights for underfilled
buckets and rare signatures. Preserve a 5-10% random audit stream to measure
screening bias.

## Original Implementation Sequence and Gates

Phases 0-3 have an implemented baseline. Phase 4 remains partial: ledgers and
distance-stratum adaptation exist, while trajectory/bisection and rare-signature
targeting do not. Phase 5 has validated local map/reduce behavior and production
Slurm launchers; the immutable production anchor index and Slurm smoke remain
operator prerequisites.

### Phase 0: contracts and source audit

1. Define the PF candidate/sample schemas and parent split registry.
2. Build the immutable Andes AC-OPF anchor index.
3. Audit field availability by case family and reject malformed anchors.

Gate: at least 99.9% of indexed anchors reproduce their source topology,
operating condition, and generator ordering deterministically.

### Phase 1: exact-control consistency

1. Preserve original `pg`, `qg`, `vg`, transformer tap/shift, and shunt fields in
   the canonical parser and PowerModels conversion.
2. Apply explicit stable-ID controls in `run_pf.jl`, reconstruct branch flows,
   and enforce reactive limits with PV-to-PQ conversion and slack transfer.
3. Run exact $u^*$ PF checks on case14, case57, case118, and representative
   1k-10k bus cases.

Gate: power-balance residuals meet tolerance; PF and OPF states agree within
documented slack/reactive-policy tolerances; no silent generator reordering.

### Phase 2: balanced intact redispatch pilot

Implement projection, distance strata, validation, PF-specific storage, and
novelty descriptors. Generate 10,000 accepted samples on case14/57/118, then
100,000 across representative size families.

Gate: pre-loss imbalance threshold holds for every proposal, at least 90% of
non-boundary proposals converge, all metadata validate, and distance/constraint
buckets show useful occupancy.

### Phase 3: contingency response pilot

Add criticality-stratified N-1 line/transformer and generator outages with the
four explicit response policies. Add structured N-2 only after N-1 validation.

Gate: generator outages replace exactly the intended lost generation within
tolerance; policy IDs reproduce controls deterministically; islanding and
nonconvergence are separated from regression data.

### Phase 4: boundary and adaptive rounds

Add trajectory scaling, bisection, rare-signature targeting, and dynamic quota
updates. Validate stopping criteria against novelty saturation, not solve count.

### Phase 5: Riker production

Use a separate campaign namespace and output root, for example:

```text
CAMPAIGN_ID=riker_pf_complement_v1
RUNS_ROOT=data/outputs/runs/riker_pf_complement_v1
```

Never share the Andes campaign ID, shard queues, or runs root. Start with one
Riker node and 32 persistent Julia workers, benchmark 32/64/128 workers per node,
then choose concurrency from throughput and Lustre metadata rates. Scale only
after a one-node restart/resume test and a multi-node deterministic-reduction
test pass.

## Initial Production Mix

Do not start with millions of solves. Use these accepted-sample milestones:

1. 10k small-case correctness pilot.
2. 100k multi-family diversity pilot.
3. 1M adaptive campaign with frozen schema and split registry.
4. Scale further only when marginal new state clusters or constraint signatures
   justify the compute and storage cost.

The Andes campaign selects roughly 12.1 million candidates per round. The PF
campaign should not inherit that volume target. Its stopping signal is marginal
physical novelty and coverage, with exact-control PF capped at 2%.