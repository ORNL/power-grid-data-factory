"""Prototype: network-expansion topology variants.

Where :mod:`grid_data_factory.topology.generation` only edits *branches* (line
switching and parallel-circuit reinforcement), this module models genuine
**network expansion** — inserting new equipment into a case:

* ``generator_addition``   — a new generator at an existing (typically high-load)
  bus. Models new dispatchable/renewable generation capacity.
* ``transformer_addition`` — a new controllable transformer (off-nominal tap,
  optional phase shift) placed in parallel with an existing corridor. Models an
  on-load tap changer / phase-shifting transformer added to steer flow.
* ``greenfield_bus``       — a brand-new bus interconnected to the existing grid
  by a new line, hosting a new generator (or load). Models a new substation /
  generation site.
* ``bus_split``            — split a high-degree substation bus into two buses
  joined by a low-impedance bus-tie, redistributing incident branches. Models
  substation expansion / breaker-and-a-half reconfiguration.
* ``load_addition``         — a new load (new demand) at an existing bus. Models
  organic demand growth / electrification / a new interconnecting customer.

Every plan is deterministic for a given ``(case_id, seed)`` and is applied with
:func:`apply_expansion`, which returns a new case dict with extended
``buses`` / ``generators`` / ``branches`` / ``loads``. Adding equipment never
disconnects the network, so expansions are valid even for radial cases.

Transformer branches carry ``tap`` / ``shift`` / ``transformer`` fields. The
production solver (``julia/run_opf.jl``) currently pins every branch to a nominal
tap; use the transformer-aware runner (``julia/run_opf_expansion.jl``) for
campaigns that exercise these plans.

This is a prototype intended to seed a future data campaign; it is deliberately
self-contained and does not alter the existing topology/solve hot path.
"""
from __future__ import annotations

import hashlib
from random import Random
from typing import Any

try:
    from grid_data_factory.topology.generation import (
        _num_components,
        _stable_seed,
        apply_topology,
    )
except ModuleNotFoundError:  # pragma: no cover - exercised only without src on path
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from grid_data_factory.topology.generation import (
        _num_components,
        _stable_seed,
        apply_topology,
    )

# Class labels carry the "expansion" token so any expanded topology is
# identifiable by substring in its id / run path (mirrors the "upgrade" token
# convention in generation.py).
GENERATOR_ADDITION = "generator_addition_expansion"
TRANSFORMER_ADDITION = "transformer_addition_expansion"
GREENFIELD_BUS = "greenfield_bus_expansion"
BUS_SPLIT = "bus_split_expansion"
LOAD_ADDITION = "load_addition_expansion"

# A cascade is an ordered sequence of the single-unit classes above, each built
# against the network state left by the previous step (so later units see the
# earlier ones). Used when a plan adds more than one unit.
CASCADE_EXPANSION = "cascade_expansion"

EXPANSION_CLASSES = (
    GENERATOR_ADDITION,
    TRANSFORMER_ADDITION,
    GREENFIELD_BUS,
    BUS_SPLIT,
    LOAD_ADDITION,
)


def _hash8(*parts: Any) -> str:
    payload = "|".join(str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def _median(values: list[float]) -> float:
    s = sorted(float(v) for v in values)
    if not s:
        return 0.0
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else 0.5 * (s[mid - 1] + s[mid])


def _weighted_pick(rng: Random, items: list[Any], weights: list[float]) -> Any:
    total = sum(w for w in weights if w > 0.0)
    if total <= 0.0:
        return rng.choice(items)
    threshold = rng.random() * total
    acc = 0.0
    for item, w in zip(items, weights):
        if w <= 0.0:
            continue
        acc += w
        if acc >= threshold:
            return item
    return items[-1]


class _CaseStats:
    """Derived quantities used to size new equipment plausibly."""

    __slots__ = (
        "bus_ids",
        "max_bus_id",
        "gen_buses",
        "load_by_bus",
        "typical_pmax",
        "typical_cost1",
        "typical_r",
        "typical_x",
        "typical_rate",
        "typical_pd",
        "typical_qd",
        "vmin",
        "vmax",
        "degree",
        "size_multiplier",
    )

    def __init__(self, case_data: dict[str, Any], size_multiplier: float = 1.0) -> None:
        buses = case_data.get("buses", [])
        gens = case_data.get("generators", [])
        loads = case_data.get("loads", [])
        branches = case_data.get("branches", [])

        self.size_multiplier = max(0.0, float(size_multiplier))
        self.bus_ids = [str(b["bus_id"]) for b in buses]
        int_ids = [int(bid) for bid in self.bus_ids] or [0]
        self.max_bus_id = max(int_ids)

        self.gen_buses = {str(g["bus_id"]) for g in gens}

        self.load_by_bus: dict[str, float] = {}
        for ld in loads:
            bid = str(ld["bus_id"])
            self.load_by_bus[bid] = self.load_by_bus.get(bid, 0.0) + abs(float(ld.get("pd", 0.0)))

        pds = [abs(float(ld.get("pd", 0.0))) for ld in loads if abs(float(ld.get("pd", 0.0))) > 0.0]
        self.typical_pd = _median(pds) or 50.0
        qds = [abs(float(ld.get("qd", 0.0))) for ld in loads if abs(float(ld.get("qd", 0.0))) > 0.0]
        self.typical_qd = _median(qds) or (0.3 * self.typical_pd)

        pmaxes = [abs(float(g.get("pmax", 0.0))) for g in gens if abs(float(g.get("pmax", 0.0))) > 0.0]
        self.typical_pmax = _median(pmaxes) or (sum(self.load_by_bus.values()) / max(1, len(gens))) or 100.0

        cost1 = []
        for g in gens:
            c = g.get("cost") or []
            if len(c) >= 2:
                cost1.append(float(c[1]))
        self.typical_cost1 = _median(cost1) or 20.0

        self.typical_r = _median([abs(float(b.get("r", 0.0))) for b in branches]) or 0.01
        self.typical_x = _median([abs(float(b.get("x", 0.0))) for b in branches]) or 0.1
        rates = [float(b.get("rate_a", 0.0)) for b in branches if 0.0 < float(b.get("rate_a", 0.0)) < 1.0e6]
        self.typical_rate = _median(rates) or 250.0

        vmins = [float(b.get("vmin", 0.9)) for b in buses]
        vmaxs = [float(b.get("vmax", 1.1)) for b in buses]
        self.vmin = _median(vmins) or 0.9
        self.vmax = _median(vmaxs) or 1.1

        self.degree: dict[str, int] = {bid: 0 for bid in self.bus_ids}
        for b in branches:
            f, t = str(b.get("from")), str(b.get("to"))
            if f in self.degree:
                self.degree[f] += 1
            if t in self.degree and t != f:
                self.degree[t] += 1


def _new_bus(bus_id: int, stats: _CaseStats, bus_type: int) -> dict[str, Any]:
    return {
        "bus_id": str(bus_id),
        "type": bus_type,
        "vm": 1.0,
        "va": 0.0,
        "vmin": float(stats.vmin),
        "vmax": float(stats.vmax),
        "gs": 0.0,
        "bs": 0.0,
    }


def _new_generator(gen_id: str, bus_id: str, rng: Random, stats: _CaseStats) -> dict[str, Any]:
    pmax = float(stats.typical_pmax) * stats.size_multiplier * (0.5 + rng.random())  # [0.5, 1.5) x typical x size
    cost1 = float(stats.typical_cost1) * (0.8 + 0.4 * rng.random())  # [0.8, 1.2) x typical
    return {
        "gen_id": gen_id,
        "bus_id": str(bus_id),
        "pmin": 0.0,
        "pmax": round(pmax, 6),
        "qmin": round(-0.4 * pmax, 6),
        "qmax": round(0.4 * pmax, 6),
        "cost": [0.0, round(cost1, 6), 0.0],
    }


def _new_load(load_id: str, bus_id: str, rng: Random, stats: _CaseStats) -> dict[str, Any]:
    pd = float(stats.typical_pd) * stats.size_multiplier * (0.5 + rng.random())  # [0.5, 1.5) x typical x size
    pf_q_over_p = (float(stats.typical_qd) / float(stats.typical_pd)) if stats.typical_pd else 0.3
    return {
        "load_id": load_id,
        "bus_id": str(bus_id),
        "pd": round(pd, 6),
        "qd": round(pd * pf_q_over_p, 6),
    }


def _new_line(branch_id: str, from_bus: str, to_bus: str, stats: _CaseStats) -> dict[str, Any]:
    return {
        "branch_id": branch_id,
        "from": str(from_bus),
        "to": str(to_bus),
        "r": round(float(stats.typical_r), 6),
        "x": round(float(stats.typical_x), 6),
        "b": 0.0,
        "rate_a": round(float(stats.typical_rate) * stats.size_multiplier, 6),
        "tap": 1.0,
        "shift": 0.0,
        "transformer": False,
    }


def _new_transformer(
    branch_id: str, from_bus: str, to_bus: str, rng: Random, stats: _CaseStats, x_ref: float
) -> dict[str, Any]:
    tap = round(0.95 + 0.10 * rng.random(), 4)  # [0.95, 1.05)
    shift = round((rng.random() - 0.5) * 10.0, 4)  # +/- 5 degrees (phase shifter)
    x = abs(float(x_ref)) or float(stats.typical_x)
    return {
        "branch_id": branch_id,
        "from": str(from_bus),
        "to": str(to_bus),
        "r": round(float(stats.typical_r) * 0.2, 6),
        "x": round(x, 6),
        "b": 0.0,
        "rate_a": round(float(stats.typical_rate) * stats.size_multiplier, 6),
        "tap": tap,
        "shift": shift,
        "transformer": True,
    }


def _tie_line(branch_id: str, from_bus: str, to_bus: str, stats: _CaseStats) -> dict[str, Any]:
    # Low-impedance bus-tie / breaker between the two halves of a split bus.
    return {
        "branch_id": branch_id,
        "from": str(from_bus),
        "to": str(to_bus),
        "r": 1.0e-4,
        "x": 1.0e-3,
        "b": 0.0,
        "rate_a": round(float(stats.typical_rate) * 4.0 * stats.size_multiplier, 6),
        "tap": 1.0,
        "shift": 0.0,
        "transformer": False,
    }


def _empty_plan(index: int, case_id: str, expansion_class: str, seed: int) -> dict[str, Any]:
    return {
        "expansion_id": f"expansion_{index:06d}_{expansion_class}_{_hash8(case_id, seed, expansion_class, index)}",
        "expansion_class": expansion_class,
        "added_buses": [],
        "added_generators": [],
        "added_branches": [],
        "added_loads": [],
        "moved_branches": [],
    }


def _plan_generator_addition(index: int, case_id: str, stats: _CaseStats, rng: Random, seed: int) -> dict[str, Any] | None:
    # Prefer load buses that do not already host a generator.
    candidates = [b for b in stats.bus_ids if b not in stats.gen_buses and stats.load_by_bus.get(b, 0.0) > 0.0]
    if not candidates:
        candidates = [b for b in stats.bus_ids if b not in stats.gen_buses] or stats.bus_ids
    weights = [stats.load_by_bus.get(b, 0.0) + 1.0 for b in candidates]
    bus_id = _weighted_pick(rng, candidates, weights)

    plan = _empty_plan(index, case_id, GENERATOR_ADDITION, seed)
    gen_id = f"gen_expansion_{bus_id}_{_hash8(case_id, seed, index, bus_id)}"
    plan["added_generators"].append(_new_generator(gen_id, bus_id, rng, stats))
    plan["target_bus_id"] = str(bus_id)
    return plan


def _plan_load_addition(index: int, case_id: str, stats: _CaseStats, rng: Random, seed: int) -> dict[str, Any] | None:
    if not stats.bus_ids:
        return None
    # New demand attaches to a well-connected bus (models load growth near a strong
    # point rather than at a radial stub).
    weights = [float(stats.degree.get(b, 0)) + 1.0 for b in stats.bus_ids]
    bus_id = _weighted_pick(rng, stats.bus_ids, weights)

    plan = _empty_plan(index, case_id, LOAD_ADDITION, seed)
    load_id = f"load_expansion_{bus_id}_{_hash8(case_id, seed, index, bus_id)}"
    plan["added_loads"].append(_new_load(load_id, bus_id, rng, stats))
    plan["target_bus_id"] = str(bus_id)
    return plan


def _plan_transformer_addition(
    index: int, case_id: str, case_data: dict[str, Any], stats: _CaseStats, rng: Random, seed: int
) -> dict[str, Any] | None:
    branches = case_data.get("branches", [])
    corridors = [b for b in branches if str(b.get("from")) != str(b.get("to"))]
    if not corridors:
        return None
    # Bias toward high-reactance corridors where a controllable transformer most
    # changes the dispatch.
    weights = [abs(float(b.get("x", 0.0))) + 1.0e-3 for b in corridors]
    corridor = _weighted_pick(rng, corridors, weights)

    plan = _empty_plan(index, case_id, TRANSFORMER_ADDITION, seed)
    xfmr_id = f"branch_transformer_expansion_{_hash8(case_id, seed, index, corridor.get('branch_id'))}"
    plan["added_branches"].append(
        _new_transformer(xfmr_id, corridor["from"], corridor["to"], rng, stats, float(corridor.get("x", 0.0)))
    )
    plan["target_corridor_id"] = str(corridor.get("branch_id"))
    return plan


def _plan_greenfield_bus(index: int, case_id: str, stats: _CaseStats, rng: Random, seed: int) -> dict[str, Any] | None:
    if not stats.bus_ids:
        return None
    # Interconnect the new bus to an existing bus, biased toward load centers.
    weights = [stats.load_by_bus.get(b, 0.0) + 1.0 for b in stats.bus_ids]
    anchor = _weighted_pick(rng, stats.bus_ids, weights)
    new_bus_id = stats.max_bus_id + 1

    plan = _empty_plan(index, case_id, GREENFIELD_BUS, seed)
    plan["added_buses"].append(_new_bus(new_bus_id, stats, bus_type=2))  # PV: hosts new generation
    line_id = f"branch_greenfield_expansion_{_hash8(case_id, seed, index, new_bus_id)}"
    plan["added_branches"].append(_new_line(line_id, anchor, str(new_bus_id), stats))
    gen_id = f"gen_greenfield_{new_bus_id}_{_hash8(case_id, seed, index)}"
    plan["added_generators"].append(_new_generator(gen_id, str(new_bus_id), rng, stats))
    plan["anchor_bus_id"] = str(anchor)
    plan["new_bus_id"] = str(new_bus_id)
    return plan


def _plan_bus_split(
    index: int, case_id: str, case_data: dict[str, Any], stats: _CaseStats, rng: Random, seed: int
) -> dict[str, Any] | None:
    branches = case_data.get("branches", [])
    # Split the busiest substation; needs enough incident branches to redistribute.
    splittable = [bid for bid, deg in stats.degree.items() if deg >= 4]
    if not splittable:
        return None
    weights = [float(stats.degree[b]) for b in splittable]
    bus_id = str(_weighted_pick(rng, splittable, weights))
    new_bus_id = stats.max_bus_id + 1

    incident = [
        b
        for b in branches
        if str(b.get("from")) == bus_id or str(b.get("to")) == bus_id
    ]
    # Move roughly half of the incident branches to the new bus (deterministic).
    order = sorted(incident, key=lambda b: str(b.get("branch_id")))
    rng.shuffle(order)
    move_count = max(1, len(order) // 2)
    to_move = order[:move_count]

    plan = _empty_plan(index, case_id, BUS_SPLIT, seed)
    plan["added_buses"].append(_new_bus(new_bus_id, stats, bus_type=1))  # PQ half-bus
    for b in to_move:
        endpoint = "from" if str(b.get("from")) == bus_id else "to"
        plan["moved_branches"].append(
            {"branch_id": str(b.get("branch_id")), "endpoint": endpoint, "new_bus_id": str(new_bus_id)}
        )
    tie_id = f"branch_bustie_expansion_{_hash8(case_id, seed, index, bus_id)}"
    plan["added_branches"].append(_tie_line(tie_id, bus_id, str(new_bus_id), stats))
    plan["split_bus_id"] = bus_id
    plan["new_bus_id"] = str(new_bus_id)
    return plan


def generate_expansion_variants(
    case_id: str,
    case_data: dict[str, Any],
    n_variants: int = 6,
    seed: int = 0,
    max_steps: int = 1,
    size_multiplier: float = 1.0,
    max_additions_fraction: float = 0.0,
) -> list[dict[str, Any]]:
    """Return up to ``n_variants`` deterministic expansion plans for ``case_data``.

    The first plan is always the empty baseline (no expansion). Remaining plans
    cycle through the four expansion classes. Plans that cannot apply to a given
    case (e.g. ``bus_split`` on a case with no degree-4 bus) are skipped, so the
    returned list may be shorter than ``n_variants``.

    ``max_steps`` (>=1) controls how *aggressive* a plan may be: when greater
    than 1, non-baseline plans become **cascades** of up to ``max_steps`` units
    added sequentially, each built against the network already carrying the
    earlier units (never all at once). ``size_multiplier`` (>=0) scales the
    capacity of each new unit (generator ``pmax`` and branch ``rate_a``) so a
    campaign can request larger reinforcements.

    ``max_additions_fraction`` (>0) makes the cascade depth *relative to the
    network size* — the effective depth ceiling becomes
    ``round(fraction * n_buses)`` (floored at 1), so bigger topologies get
    proportionally bigger expansions. When both are given, ``max_steps`` acts as
    an absolute cap on that size-derived ceiling. All three default to today's
    single-unit, self-scaled behavior.
    """
    baseline = _empty_plan(0, case_id, "baseline", seed)
    baseline["expansion_class"] = "baseline"
    plans: list[dict[str, Any]] = [baseline]
    if n_variants <= 1 or not case_data.get("buses"):
        return plans

    max_steps = max(1, int(max_steps))
    stats = _CaseStats(case_data, size_multiplier)
    # Relative budget: scale the cascade-depth ceiling to the network size so a
    # 2% build-out means 2% on any case (floored at one unit for tiny cases).
    if max_additions_fraction and max_additions_fraction > 0.0:
        rel_ceiling = max(1, round(float(max_additions_fraction) * len(stats.bus_ids)))
        depth_ceiling = min(rel_ceiling, max_steps) if max_steps > 1 else rel_ceiling
    else:
        depth_ceiling = max_steps
    order = [GENERATOR_ADDITION, TRANSFORMER_ADDITION, GREENFIELD_BUS, BUS_SPLIT, LOAD_ADDITION]
    seen: set[str] = set()
    index = 1
    attempts = 0
    max_attempts = max(50, n_variants * 12 * depth_ceiling)
    while len(plans) < n_variants and attempts < max_attempts:
        attempts += 1
        # Vary cascade depth across variants so the corpus spans single-unit
        # through depth_ceiling-unit reinforcements.
        depth = 1 if depth_ceiling <= 1 else (1 + ((index - 1) % depth_ceiling))

        working = case_data
        working_stats = stats
        steps: list[dict[str, Any]] = []
        for s in range(depth):
            expansion_class = order[(index - 1 + s) % len(order)]
            sub_index = index if s == 0 else index * 1000 + s
            rng = Random(_stable_seed(case_id, seed, sub_index))
            step = _build_single_plan(
                expansion_class, sub_index, case_id, working, working_stats, rng, seed
            )
            if step is None:
                continue
            steps.append(step)
            working = apply_expansion(working, step)
            working_stats = _CaseStats(working, size_multiplier)
        index += 1
        if not steps:
            continue

        plan = steps[0] if len(steps) == 1 else _combine_cascade(index - 1, case_id, seed, steps)
        signature = _plan_signature(plan)
        if signature in seen:
            continue
        seen.add(signature)
        plan["added_bus_count"] = len(plan["added_buses"])
        plan["added_generator_count"] = len(plan["added_generators"])
        plan["added_branch_count"] = len(plan["added_branches"])
        plan["added_load_count"] = len(plan["added_loads"])
        plan["moved_branch_count"] = len(plan["moved_branches"])
        plan["step_count"] = len(steps)
        plans.append(plan)
    return plans


def _build_single_plan(
    expansion_class: str,
    index: int,
    case_id: str,
    case_data: dict[str, Any],
    stats: _CaseStats,
    rng: Random,
    seed: int,
) -> dict[str, Any] | None:
    if expansion_class == GENERATOR_ADDITION:
        return _plan_generator_addition(index, case_id, stats, rng, seed)
    if expansion_class == TRANSFORMER_ADDITION:
        return _plan_transformer_addition(index, case_id, case_data, stats, rng, seed)
    if expansion_class == GREENFIELD_BUS:
        return _plan_greenfield_bus(index, case_id, stats, rng, seed)
    if expansion_class == LOAD_ADDITION:
        return _plan_load_addition(index, case_id, stats, rng, seed)
    return _plan_bus_split(index, case_id, case_data, stats, rng, seed)


def _combine_cascade(index: int, case_id: str, seed: int, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold sequential ``steps`` into one plan; ``steps`` is the source of truth.

    The aggregated ``added_*`` / ``moved_branches`` lists are for reporting and
    dedup only; :func:`apply_expansion` replays ``steps`` in order so a unit can
    build on equipment added by an earlier unit.
    """
    plan = _empty_plan(index, case_id, CASCADE_EXPANSION, seed)
    plan["expansion_class"] = CASCADE_EXPANSION
    plan["steps"] = steps
    plan["cascade_classes"] = [s["expansion_class"] for s in steps]
    for s in steps:
        plan["added_buses"].extend(s.get("added_buses", []))
        plan["added_generators"].extend(s.get("added_generators", []))
        plan["added_branches"].extend(s.get("added_branches", []))
        plan["added_loads"].extend(s.get("added_loads", []))
        plan["moved_branches"].extend(s.get("moved_branches", []))
    return plan


def _plan_signature(plan: dict[str, Any]) -> str:
    return "|".join(
        [
            plan["expansion_class"],
            ",".join(sorted(str(b["bus_id"]) for b in plan["added_buses"])),
            ",".join(sorted(str(g["gen_id"]) for g in plan["added_generators"])),
            ",".join(sorted(str(b["branch_id"]) for b in plan["added_branches"])),
            ",".join(sorted(str(ld["load_id"]) for ld in plan["added_loads"])),
            ",".join(sorted(m["branch_id"] for m in plan["moved_branches"])),
        ]
    )


def apply_expansion(case_data: dict[str, Any], plan: dict[str, Any] | None) -> dict[str, Any]:
    """Return a new case dict with the expansion ``plan`` applied.

    Adds any new buses/generators/branches/loads and reassigns the endpoints of
    moved branches (bus split). The input ``case_data`` is not mutated.
    """
    if not plan or plan.get("expansion_class") in (None, "baseline"):
        return case_data
    steps = plan.get("steps")
    if steps:
        out = case_data
        for step in steps:
            out = apply_expansion(out, step)
        return out
    if not (plan.get("added_buses") or plan.get("added_generators") or plan.get("added_branches") or plan.get("moved_branches") or plan.get("added_loads")):
        return case_data

    buses = [dict(b) for b in case_data.get("buses", [])]
    generators = [dict(g) for g in case_data.get("generators", [])]
    branches = [dict(b) for b in case_data.get("branches", [])]
    loads = [dict(ld) for ld in case_data.get("loads", [])]

    moved = {str(m["branch_id"]): m for m in plan.get("moved_branches", [])}
    if moved:
        for br in branches:
            m = moved.get(str(br.get("branch_id")))
            if m is not None:
                br[m["endpoint"]] = str(m["new_bus_id"])

    buses.extend(dict(b) for b in plan.get("added_buses", []))
    generators.extend(dict(g) for g in plan.get("added_generators", []))
    branches.extend(dict(b) for b in plan.get("added_branches", []))
    loads.extend(dict(ld) for ld in plan.get("added_loads", []))

    out = dict(case_data)
    out["buses"] = buses
    out["generators"] = generators
    out["branches"] = branches
    out["loads"] = loads
    return out


def apply_topology_and_expansion(
    case_data: dict[str, Any],
    switched_off_branches: list[str] | None = None,
    reinforced_branches: list[str] | None = None,
    expansion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convenience: apply branch-level topology edits then a network expansion.

    Integration point for a future expansion campaign — a candidate record can
    carry ``switched_off_branches`` / ``reinforced_branches`` / ``expansion`` and
    this reproduces the solve-time network in one call.
    """
    out = apply_topology(case_data, switched_off_branches, reinforced_branches)
    return apply_expansion(out, expansion)


def is_connected_after_expansion(case_data: dict[str, Any], plan: dict[str, Any] | None) -> bool:
    """True if the case remains a single connected component after ``plan``."""
    expanded = apply_expansion(case_data, plan)
    bus_ids = [str(b["bus_id"]) for b in expanded.get("buses", [])]
    index_of = {bid: i for i, bid in enumerate(bus_ids)}
    edges: list[tuple[int, int]] = []
    for br in expanded.get("branches", []):
        a = index_of.get(str(br.get("from")))
        b = index_of.get(str(br.get("to")))
        if a is None or b is None:
            continue
        edges.append((a, b))
    if not bus_ids:
        return True
    return _num_components(len(bus_ids), edges, set()) == 1
