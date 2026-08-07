# Data Generation Capabilities

Power Grid Data Factory is a **high-throughput, provenance-first engine for
manufacturing power-system optimization datasets at extreme scale**. It is built
to saturate leadership-class HPC resources while sweeping an unusually wide space
of grid conditions, so that the resulting corpora are large *and* diverse enough
to train and stress foundation models for power-grid analysis.

This document summarizes what sets the generator apart: the breadth of
variability it parametrizes, and the HPC machinery that lets it turn that
variability into billions of labeled samples.

---

## 1. Why this library is distinctive

Most grid-data pipelines vary one or two axes (usually load scaling and a handful
of N-1 outages) on a small fixed set of cases. This library instead treats data
generation as a **multi-axis, fully parametrized design-of-experiments problem**
and executes it as an **embarrassingly parallel HPC campaign**:

- **Every physical and structural degree of freedom is a tunable, seeded
  parameter** — demand, dispatch, network admittance, shunts, topology,
  reinforcements, contingencies, and cost structure all vary independently and
  reproducibly.
- **Feasible and infeasible outcomes are both first-class data.** Nothing is
  silently discarded, so the output doubles as a labeled corpus for
  feasibility/robustness classification, not just a set of solved OPFs.
- **It is designed from the ground up for the machine, not the laptop.** A single
  campaign scales to thousands of ranks across 64+ nodes, chains across many
  walltime windows, resumes after interruption, and is Lustre-aware so that
  metadata pressure never becomes the bottleneck. Campaigns of **3 billion
  configurations** are a supported operating point, not a thought experiment.
- **Determinism is a hard requirement.** Every sample is derived from
  process-independent, hash-based seeds, so any record can be regenerated
  bit-for-bit on any node at any time.

The remaining sections detail each of these claims with the concrete parameters
and scale figures behind them.

---

## 2. Variability and parametrization

The generator samples a high-dimensional space along five independent axes. Each
axis is seeded deterministically and controlled by explicit config knobs, so the
diversity is both broad and fully reproducible.

### 2.1 Operating-point / demand variability

Demand scenarios are drawn with a choice of **four low-discrepancy / stochastic
samplers** — `sobol` (low-discrepancy), `latin_hypercube` (production default,
stratified and O(1) in batch size), `stratified` (block grid with jitter), and
`time_series` (sinusoidal load profiles) — implemented in
[operating_point_generation.py](../src/grid_data_factory/scenarios/operating_point_generation.py).

Load is shaped as a hierarchy rather than a single global multiplier
([operating_points.py](../src/grid_data_factory/scenarios/operating_points.py)):

- **Global load scaling** across regimes.
- **Regionally correlated variation** over four regions (north/south/east/west),
  each with independent real- and reactive-power scale factors.
- **Per-bus residual noise** (`local_noise_stddev`, σ = 0.02 default) for local
  heterogeneity.
- **Power-factor preservation** as a default-on flag.

These are organized into **14 named operating regimes** (baseline, low_load,
shoulder, summer_peak, winter_peak, high_renewable, low_renewable, high_import,
high_export, maintenance, branch_derating, generator_cost_shift, reactive_stress,
extreme_peak), each with its own load-scale, rating-scale, and cost-scale ranges.

Dispatch and reserves also vary: `generator_fleet_availability`,
`renewable_scale`, and `reserve_margin` (≈0.12–0.20) reshape the generation fleet
per sample.

### 2.2 Physics / admittance enrichment

Beyond operating conditions, the **electrical parameters of the network itself
are perturbed** per sample, so the model never sees the exact same admittance
matrix twice ([operating_points.py](../src/grid_data_factory/scenarios/operating_points.py),
`_branch_factor`):

- Per-branch **series resistance** — `line_resistance_sigma`
- Per-branch **series reactance** — `line_reactance_sigma`
- Per-branch **shunt charging susceptance** — `line_charging_sigma`
- Per-bus **shunt susceptance (Bs)** — `bus_shunt_susceptance_sigma`
- **Generator cost-curve permutation** (deterministic Fisher–Yates) to decouple
  network state from merit order

Every perturbation is drawn i.i.d. from `U[1−σ, 1+σ)` using a SplitMix64 hash of
`(perturbation_seed, branch/bus key, dimension)`, so it is identical across
processes and fully reproducible.

### 2.3 Topology variability

Topologies are generated as distinct network configurations
([topology/generation.py](../src/grid_data_factory/topology/generation.py)):

- **Line switching / maintenance outages** via deterministic weighted sampling
  without replacement (Efraimidis–Spirakis), classified as
  `single_line_switching`, `double_line_switching`, or
  `maintenance_configuration`.
- **Grid reinforcements (upgrades)** modeled as *distinct* parallel circuits, not
  identical clones: each added `{branch_id}_parallel` circuit gets a
  deterministic ampacity in **[1.5×, 2.5×)** of the base line with resistance
  reduced proportionally (`R' = R/scale`), while reactance and shunt susceptance
  stay fixed to respect tower geometry. Classified as `single_line_upgrade`,
  `double_line_upgrade`, `multi_line_upgrade`.
- **Connectivity is always preserved** via iterative Tarjan bridge detection
  (O(V+E), safe past 80k branches), with case-size-aware caps on simultaneous
  outages.

Key knobs: `topologies_per_case` (default 6), `max_switched_branches` (default
3), and a per-variant seed `SHA256(case_id|seed|index)`.

### 2.4 Contingency variability

Contingencies are enumerated over a rich **ontology of ~15 credibility-labeled
classes** ([contingencies/ontology.py](../src/grid_data_factory/contingencies/ontology.py),
[enumeration.py](../src/grid_data_factory/contingencies/enumeration.py)) —
including `independent_random`, `common_corridor`, `common_tower`,
`common_substation`, `generator_export_path`, `sequential_n1n1`, `cut_set`,
`cascade_induced`, and `adversarial_but_credible`.

Enumeration streams span:

- **N-1, N-2, and general N-k** simultaneous outages.
- **Parallel-circuit contingencies**: independent loss of one of two parallel
  circuits, plus **common-mode N-2** loss of both — automatically emitted for
  every reinforced corridor.
- **Sequential N-1-N-1** and **multi-stage cascades** (`SEQ_MAX_LEN` up to 10)
  that capture intermediate post-contingency states.

Each contingency carries severity, security-boundary, active-constraint,
physical-credibility, and estimated-compute scores used downstream for selection.

**Enumeration-time feasibility prefiltering**
([feasibility.py](../src/grid_data_factory/contingencies/feasibility.py)) drops
structurally hopeless contingencies *before* any solve — islanding detection
(reinforcement-aware), generation-adequacy checks, and case-size order caps —
removing a large fraction (empirically up to ~80%) of guaranteed-infeasible work
so compute is spent where it is informative.

### 2.5 Acquisition and selection

Rather than a flat random draw, candidates are prioritized across **six
acquisition queues** ([queues.py](../src/grid_data_factory/acquisition/queues.py))
— broad coverage, active-constraint novelty, security boundary, credible
contingencies, high-severity/uncertainty, and an unscreened audit stream — with
tunable budget fractions per campaign. Optional **portfolio constraints**
(`max_per_grid`, `max_per_regime`, `max_per_contingency_class`) bound
over-representation, and a **full-budget streaming path**
([campaign_round.py](../src/grid_data_factory/campaigns/campaign_round.py))
selects every constraint-passing candidate with O(buckets) memory when the budget
exceeds the pool — essential at billion-scale.

---

## 3. HPC scalability

The generator is architected as a **map/reduce campaign** that is designed to
consume leadership-class allocations end to end.

### 3.1 Massively parallel map/reduce

- **Map stage**: work is split into shards (`SHARD_COUNT = ntasks × 8`) consumed
  from a **dynamic, file-locked work queue** so faster ranks pull more shards —
  self-balancing instead of static assignment. Each rank solves independently and
  streams results to its own shard ledger.
- **Reduce stage**: deterministic aggregation of all shard ledgers into a single
  campaign ledger.

A representative CPU configuration
([andes_powermodels_acopf_mapreduce_10n_36h.sbatch](../configs/slurm/andes_powermodels_acopf_mapreduce_10n_36h.sbatch))
runs **64 nodes × 16 ranks = 1,024 concurrent solves** for a 36-hour window. A
GPU configuration
([frontier_exago_acopf_mapreduce_8n_2h.sbatch](../configs/slurm/frontier_exago_acopf_mapreduce_8n_2h.sbatch))
maps one MPI rank per MI250X GCD (8 GCDs/node) with an IPOPT CPU fallback.

### 3.2 Billion-scale campaigns via rounds and chaining

Large campaigns are split into **rounds**, each sized to complete within a single
walltime window and **chained with SLURM `afterok` dependencies**
([launch_ultrascale_campaign.py](../scripts/launch_ultrascale_campaign.py)). This
is how a **3-billion-configuration** campaign is expressed: e.g. 150 chained
rounds of 20M configurations each, over a 29-case portfolio, each round
independently seeded. Budgets can be split constant/linear/geometric across
rounds, and a separate `--runs-root` isolates concurrent campaigns on shared
storage.

### 3.3 Resilience and reproducibility at scale

- **Resumable**: `RESUME=1` reuses completed shards via per-shard done-markers,
  so an interrupted round restarts only the unfinished work.
- **Append-only ledgers**: `run_registry.jsonl` / `.parquet` record every solve;
  a `run_id` already present is skipped.
- **Deterministic sharding**: round-robin by `candidate_id` spreads coverage keys
  (dataset, topology, regime, contingency class) evenly and reproducibly across
  shards.

### 3.4 Parallel-filesystem (Lustre) awareness

Extreme concurrency stresses the metadata server, so the pipeline is explicitly
Lustre-aware:

- **Striping is tuned per artifact class**: wide striping (`lfs setstripe -c 8`)
  for large JSONL, single-OST striping for the millions of tiny per-shard files
  to avoid MDS storms.
- **Julia depots live on Lustre** to survive 1,000+ concurrent solver startups.
- **Inode relief via archiving**: each completed round's raw output is collapsed
  into a single **zstd tarball** with a verified stream test, file-count match,
  and SHA-256 checksum before the raw tree is purged — turning millions of tiny
  inodes into a few large striped files. Archive output paths are per-campaign to
  avoid collisions across concurrent campaigns.

---

## 4. Multi-solver support

The same campaign can target multiple solver backends
([configs/solvers.yaml](../configs/solvers.yaml)):

- **PowerModels.jl** — PF, DC-OPF, AC-OPF, relaxed/corrective OPF, SCOPF (Ipopt /
  HiGHS), invoked via a precompiled Julia sysimage for fast startup.
- **ExaGO** — AC-OPF and SCOPF, including a GPU sparse solver (HiOp) on MI250X
  with an Ipopt CPU fallback.
- **pandapower** — AC-OPF, primarily for cross-solver consistency validation.

Cross-solver agreement is calibrated and gated
([phase1_calibration.yaml](../configs/phase1_calibration.yaml),
[phase1_gate.py](../scripts/phase1_gate.py)) so the same case solved by different
backends agrees within tight tolerances.

---

## 5. Output: a labeled corpus for training *and* classification

Every solve emits a rich record that captures the full parametrization *and* the
outcome, and **both feasible and infeasible results are retained**:

- **Inputs / parametrization**: the operating-point parameters (all scales,
  reserve margin, per-branch/-bus σ values, perturbation seed, cost permutation),
  topology modifications (`switched_off_branches`, `reinforced_branches`,
  topology class), operating regime, and the full contingency description with
  ontology labels and credibility.
- **Screening scores**: DC severity, branch margins, voltage/reactive risk,
  novelty, active-constraint, security-boundary, and model-uncertainty scores.
- **Solve outcome**: termination status, objective, runtime, MPI/GPU context, and
  validation mismatches (P/Q/voltage/generator/branch).

Because **infeasible and non-converged solves are never filtered out**, the
corpus is directly usable as a **feasibility-classification dataset**, and each
archived round ships a convergence sidecar recording the feasible/infeasible
split and label definition.

---

## 6. Where to go next

- [Architecture and Data Layout](architecture.md)
- [Adaptive Campaign Strategy](adaptive_campaign_strategy.md)
- [Enumeration-Time Feasibility Prefiltering](feasibility_prefiltering.md)
- [Resumable Campaigns and the Top-Level Driver](resumable_campaigns.md)
- [Configuration Reference](configuration_reference.md)
- [Schema Contracts](schema_contracts.md)
- [Reproducibility Workflow](reproducibility.md)
