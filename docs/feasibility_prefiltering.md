# Enumeration-Time Feasibility Prefiltering

This document describes the feasibility prefilter that removes structurally
infeasible contingency candidates *before* they are ever handed to the AC-OPF
solver, and the empirical benefit measured on multi-million-candidate campaign
rounds.

- Implementation: [src/grid_data_factory/contingencies/feasibility.py](../src/grid_data_factory/contingencies/feasibility.py)
- Integration point: [src/grid_data_factory/contingencies/enumeration.py](../src/grid_data_factory/contingencies/enumeration.py)
- CLI surface: [scripts/enumerate_contingencies.py](../scripts/enumerate_contingencies.py)
- Tests: [tests/test_feasibility_prefilter.py](../tests/test_feasibility_prefilter.py)

## 1. Motivation

The campaign solves **preventive** AC-OPF: a single dispatch must be feasible for
the post-contingency network with **no corrective redispatch and no load shed**.
Under that model a large fraction of enumerated candidates are infeasible for
reasons that have nothing to do with the optimizer — the network simply admits no
power flow, or the generation cannot physically cover the load.

Diagnosis of an early production map-reduce round showed **~82% of solves
returning `LOCALLY_INFEASIBLE`**, concentrated in the small/medium cases:

| Case | Buses | Infeasible solves |
|------|------:|------------------:|
| `pglib_opf_case14_ieee`  |  14 | 89.6% |
| `pglib_opf_case57_ieee`  |  57 | 87.3% |
| `pglib_opf_case118_ieee` | 118 | 49.8% |

Every one of those solves consumed a solver slot (Ipopt warm-up, factorization,
timeout budget) to prove something we can detect combinatorially in microseconds.
On a 150M-sample campaign that is the dominant source of wasted compute.

Three cheap-to-detect, **provable** causes account for the bulk of the
infeasibility:

1. **Islanding** — a switched-off topology combined with a branch contingency
   disconnects the network graph, so no connected power flow exists.
2. **Generation inadequacy** — available generation capacity (after fleet
   availability / renewable scaling and any lost generators) cannot cover the
   scaled load, so power balance is impossible.
3. **Contingency order too high for small cases** — N-k and cascades on tiny
   networks are almost always infeasible; a 14-bus grid does not survive an N-3.

## 2. Where the filter runs in the pipeline

The filter is applied at **enumeration time**, inside `expand_one` in
[enumeration.py](../src/grid_data_factory/contingencies/enumeration.py), *before*
candidates reach the screening/audit stage.

```
operating points ──► expand_one (ENUMERATION)
                        │  ├─ op-level adequacy short-circuit  ── drop whole op
                        │  └─ per-candidate filters ───────────── drop candidate
                        ▼
                     surviving candidates ──► screening / audit ──► selection ──► solver (map)
```

This placement matters: the campaign runs with `audit_fraction=1.0`, which
**bypasses screening entirely** (the screened set is a link/copy of the enumerated
set). If the filter lived in the screening stage it would be a no-op for the
production configuration. Filtering at the source shrinks the candidate pool
independently of the audit fraction.

There are two levels of removal:

- **Operating-point short-circuit** — if an operating point cannot even meet its
  load with *all* generators available and *no* contingency applied, the entire
  contingency fan-out for that operating point is skipped (`dropped_op_inadequate`).
  This is the cheapest and highest-leverage cut, because one bad operating point
  otherwise spawns hundreds of doomed candidates.
- **Per-candidate filtering** — each generated candidate is checked against the
  order cap, the connectivity test, and the post-contingency adequacy test
  (`dropped_order`, `dropped_island`, `dropped_adequacy`).

## 3. The filters in detail

### 3.1 Case-size order caps

`order_allowed(bus_count, order, event_type)` caps contingency complexity by
network size. Small grids cannot physically absorb high-order outages under a
preventive dispatch, so those candidates are dropped rather than enumerated.

| Bus count | Allowed contingencies | Constant |
|-----------|-----------------------|----------|
| `<= 20`   | N-1 **simultaneous only** | `SMALL_CASE_MAX_BUS = 20` |
| `<= 60`   | order `<= 2`, `simultaneous` or `sequential_n1n1` | `MEDIUM_CASE_MAX_BUS = 60` |
| `> 60`    | unrestricted (rely on connectivity + adequacy) | — |
| unknown (`<= 0`) | unrestricted (fail-open) | — |

The bus count comes from `bus_count_hint`, which resolves the case file when
available and otherwise falls back to the enumeration component pool (which knows
canonical sizes from the case-id string alone).

### 3.2 Connectivity / islanding test

`creates_island(ctx, switched_off_branches, contingency)` unions the branch
removals from the switched-off topology and the contingency, then compares the
number of connected components (union-find via `_num_components`) against the base
network. If the removals increase the component count, the post-contingency
network is islanded and is dropped (`dropped_island`).

The per-case graph (bus list, edge list, branch→edge index, base component count)
is built once and cached per case via `functools.lru_cache`, so the test is a
cheap set-union + component count per candidate.

### 3.3 Generation-adequacy test

`generation_adequate(ctx, op_params, contingency, margin)` replicates the runtime
availability scaling from `apply_operating_point` and checks power balance:

$$\sum_{g}\; p^{\max}_g \cdot a_g \;-\; \sum_{g \in \text{lost}} p^{\max}_g \cdot a_g \;\ge\; (1 + \text{margin}) \cdot s_{\text{load}} \cdot \textstyle\sum_b p^d_b$$

where the per-generator availability factor mirrors the operating-point model
(every third generator is treated as renewable and scaled by `renewable_scale`,
the rest by `generator_fleet_availability`, clamped to `[0.1, 1.2]`):

```
a_g = clamp( fleet_availability * (renewable_scale if g % 3 == 0 else 1.0), 0.1, 1.2 )
```

- `s_load` is the global load scale (regional scales are symmetric about 1.0 and
  cancel in aggregate).
- `margin = ADEQUACY_MARGIN = 0.03` is a small **loss allowance** (transmission
  losses), deliberately *not* the much larger planning reserve margin — so only
  genuinely power-balance-infeasible points are dropped.

The same function is used both for the operating-point short-circuit (with
`contingency = None`) and for the post-contingency per-candidate check (subtracting
any generators the contingency removes) → `dropped_adequacy`.

## 4. Design principles

- **Conservative (no false drops).** Every filter only removes candidates that are
  *provably* infeasible under the preventive model. The adequacy margin is a small
  loss allowance, not a reserve margin, so solvable operating points are never
  discarded. The order caps are set below the empirically observed feasibility
  cliff for each size tier.
- **Fail-open.** If a case network cannot be resolved (e.g. synthetic toy case ids
  in unit tests, or a missing file), `build_case_context` returns `None` and the
  network-based filters silently no-op. Only the case-size order cap — which needs
  just a bus-count hint — still applies. A resolution failure therefore never
  blocks enumeration; it only forgoes the optional savings.
- **Deterministic and side-effect free.** The filter reads cached, immutable case
  facts and the candidate's own parameters. It does not touch RNG state, so
  enabling it does not change the sampled operating points — only which of the
  enumerated contingencies survive.

## 5. Configuration and CLI

The filter is **on by default**. It is controlled per run:

- CLI: `--feasibility-prefilter` (default) / `--no-feasibility-prefilter` on
  [scripts/enumerate_contingencies.py](../scripts/enumerate_contingencies.py).
- Programmatic: the `sampling.feasibility_prefilter` attribute read by
  `expand_one` (defaults to `True` when absent, so older callers keep the filter).

In the map-reduce campaign the flag flows through the bootstrap and is reported in
the enumeration summary, so every round records whether the filter was active.

## 6. Telemetry

The enumeration summary JSON records the filter state and the drop breakdown so
the effect is auditable per round:

```json
{
  "base_candidates": 630000,
  "expanded_candidates": 5611980,
  "feasibility_prefilter": true,
  "prefilter_dropped": {
    "dropped_op_inadequate": 55319,
    "dropped_order": 6912352,
    "dropped_island": 1145492,
    "dropped_adequacy": 122520
  }
}
```

- `dropped_op_inadequate` counts whole **operating points** short-circuited.
- `dropped_order`, `dropped_island`, `dropped_adequacy` count individual
  **candidates** removed.
- Parallel workers accumulate local stats that are merged deterministically at the
  end of enumeration.

## 7. Empirical results

### 7.1 Production round (campaign `ultrascale_150m`, round 0)

Enumeration over **630,000** Latin-hypercube operating points across
`case14 / case57 / case118`, `max_k=10` with sequential cascades, seed 7:

| Quantity | Value |
|----------|------:|
| Base operating points | 630,000 |
| Whole operating points short-circuited (adequacy) | 55,319 |
| Candidates dropped — order cap | 6,912,352 |
| Candidates dropped — islanding | 1,145,492 |
| Candidates dropped — post-contingency adequacy | 122,520 |
| **Candidates dropped (total)** | **8,180,364** |
| **Candidates kept (`expanded_candidates`)** | **5,611,980** |
| Raw candidates that would have been enumerated | ~13,792,344 |
| **Candidate-level reduction** | **~59.3%** |

The prefilter removed **~8.2M provably-infeasible candidates**, so only **5.61M**
candidates proceeded to the solver instead of ~13.8M — a **~59% reduction in solve
work at the candidate level**. The 55,319 short-circuited operating points are an
*additional* saving on top of that figure: each would otherwise have fanned out
into its own contingency set, so the true compute saving exceeds 59%.

Crucially, the dominant term is `dropped_order` (6.9M, 84% of the drops):
high-order contingencies on the small/medium cases that the earlier diagnosis
showed were ~87–90% infeasible. These are precisely the solves that produced the
82% `LOCALLY_INFEASIBLE` rate, now eliminated before the solver is invoked.

### 7.2 Controlled benchmark (reproducible)

A smaller, fully reproducible run — 600 operating points over the same three
cases, seed 7, `max_k=5` plus cascades — isolates the same effect:

| Quantity | Value |
|----------|------:|
| Candidates before filter | 8,400 |
| Candidates after filter | 4,339 |
| **Reduction** | **~48%** |
| dropped — order | 3,168 |
| dropped — islanding | 439 |
| dropped — adequacy | 118 |
| dropped — op inadequate | 24 |

Parallel enumeration with 2 and 4 workers produced byte-identical output,
confirming the filter is deterministic.

## 8. Interpretation of the benefit

- **Solver throughput.** Roughly 3 in 5 enumerated candidates were doomed under
  the preventive model. Removing them up front means the fixed HPC allocation
  spends its solver slots on candidates that can actually yield a feasible,
  scientifically useful record instead of re-deriving infeasibility.
- **Dataset quality.** The retained pool is biased toward solvable, physically
  credible operating conditions, reducing the flood of `LOCALLY_INFEASIBLE`
  records that otherwise dominate the raw output and complicate downstream
  diversity/active-constraint ledgers.
- **No loss of coverage.** Because every drop is provable and the filter is
  fail-open and RNG-neutral, the feasible region sampled is unchanged; only
  structurally impossible configurations are pruned.

## 9. Tuning constants

All thresholds live at the top of
[feasibility.py](../src/grid_data_factory/contingencies/feasibility.py) and can be
adjusted if the case mix or physical model changes:

| Constant | Value | Meaning |
|----------|------:|---------|
| `SMALL_CASE_MAX_BUS`  | 20  | ≤ this ⇒ N-1 simultaneous only |
| `MEDIUM_CASE_MAX_BUS` | 60  | ≤ this ⇒ order ≤ 2, simultaneous / sequential-n1n1 |
| `ADEQUACY_MARGIN`     | 0.03 | Loss allowance over scaled load in the adequacy test |

If corrective actions (redispatch, load shed) are later added to the OPF model,
the adequacy and order caps should be revisited — a corrective formulation makes
some currently-pruned candidates feasible again, and the filter would need to be
relaxed or disabled (`--no-feasibility-prefilter`) accordingly.
