"""Operating-point transforms applied to a parsed case at solve time.

Rebuilds loads from a reference snapshot (when supplied) and applies regional /
global scaling of loads, generator availability, reserve margins, branch ratings
and cost. Also applies per-branch i.i.d. admittance perturbation (series r/x and
shunt charging b, each drawn from U[1-sigma, 1+sigma) deterministically from a
seed), per-bus shunt susceptance (Bs) perturbation, and optional generator
cost-curve permutation. All transforms operate on a deep copy so the base case is
never mutated, and they compose: snapshot rebuild happens first, then scaling.
"""
from __future__ import annotations

import json
from typing import Any

_UINT64 = 0xFFFFFFFFFFFFFFFF


def _mix64(x: int) -> int:
    """SplitMix64 finalizer: deterministic, process-independent 64-bit hash."""
    x &= _UINT64
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & _UINT64
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & _UINT64
    return x ^ (x >> 31)


def _str_key(s: str) -> int:
    """FNV-1a hash of a string -> stable 64-bit int (unlike salted ``hash``)."""
    h = 0xCBF29CE484222325
    for ch in s.encode("utf-8"):
        h = ((h ^ ch) * 0x100000001B3) & _UINT64
    return h


def _branch_factor(seed: int, branch_key: int, dim: int, sigma: float) -> float:
    """Per-branch i.i.d. multiplicative factor drawn from U[1-sigma, 1+sigma).

    Deterministic in ``(seed, branch_key, dim)`` so the same candidate always
    reproduces the same per-branch perturbation without storing a value per
    branch (keeps candidate records O(1) even for 80k-branch grids).
    """
    if sigma <= 0.0:
        return 1.0
    h = _mix64(seed + branch_key + (dim + 1) * 0x9E3779B97F4A7C15)
    u = (h & 0x1FFFFFFFFFFFFF) / float(0x20000000000000)  # 53-bit mantissa -> [0,1)
    return 1.0 + (2.0 * u - 1.0) * sigma


def _seeded_permutation(n: int, seed: int) -> list[int]:
    """Deterministic Fisher-Yates permutation of ``range(n)`` from ``seed``."""
    perm = list(range(n))
    state = _mix64(seed + 0x1234567)
    for i in range(n - 1, 0, -1):
        state = _mix64(state)
        j = state % (i + 1)
        perm[i], perm[j] = perm[j], perm[i]
    return perm


def region_for_bus(bus_id: str) -> str:
    n = int(bus_id)
    m = n % 4
    if m == 0:
        return "north"
    if m == 1:
        return "south"
    if m == 2:
        return "east"
    return "west"


def apply_operating_point(case_data: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(case_data))

    snapshot = params.get("_load_snapshot_map")
    if snapshot:
        rebuilt = []
        for bus_id, pq in sorted(snapshot.items(), key=lambda kv: int(kv[0])):
            pd = float(pq[0])
            qd = float(pq[1]) if len(pq) > 1 else 0.0
            if abs(pd) > 0.0 or abs(qd) > 0.0:
                rebuilt.append({"load_id": f"load_{len(rebuilt) + 1:06d}", "bus_id": str(bus_id), "pd": pd, "qd": qd})
        out["loads"] = rebuilt

    g = float(params.get("global_load_scale", 1.0))
    reg = {
        "north": float(params.get("regional_load_scale_north", 1.0)),
        "south": float(params.get("regional_load_scale_south", 1.0)),
        "east": float(params.get("regional_load_scale_east", 1.0)),
        "west": float(params.get("regional_load_scale_west", 1.0)),
    }
    reg_q = {
        "north": float(params.get("regional_reactive_scale_north", 1.0)),
        "south": float(params.get("regional_reactive_scale_south", 1.0)),
        "east": float(params.get("regional_reactive_scale_north", 1.0)),
        "west": float(params.get("regional_reactive_scale_south", 1.0)),
    }

    reserve_margin = float(params.get("reserve_margin", 0.15))
    fleet_avail = float(params.get("generator_fleet_availability", 1.0))
    renew_scale = float(params.get("renewable_scale", 1.0))
    branch_scale = float(params.get("branch_rating_scale", 1.0))
    cost_scale = float(params.get("cost_scale", 1.0))
    # Per-branch i.i.d. admittance perturbation: candidate carries only the
    # deviation magnitudes (sigma) + a seed; the actual per-branch factor is
    # derived deterministically at solve time. Legacy candidates that instead
    # carry global ``line_*_scale`` scalars still work via the fallback below.
    r_sigma = float(params.get("line_resistance_sigma", 0.0))
    x_sigma = float(params.get("line_reactance_sigma", 0.0))
    b_sigma = float(params.get("line_charging_sigma", 0.0))
    bus_bs_sigma = float(params.get("bus_shunt_susceptance_sigma", 0.0))
    pert_seed = int(params.get("perturbation_seed", 0))
    r_scale_global = float(params.get("line_resistance_scale", 1.0))
    x_scale_global = float(params.get("line_reactance_scale", 1.0))
    b_scale_global = float(params.get("line_charging_scale", 1.0))

    if bus_bs_sigma > 0.0:
        for bus in out.get("buses", []):
            if "bs" in bus and float(bus["bs"]) != 0.0:
                bus_key = _str_key(str(bus.get("bus_id", "")))
                bus["bs"] = float(bus["bs"]) * _branch_factor(pert_seed, bus_key, 3, bus_bs_sigma)

    for load in out.get("loads", []):
        region = region_for_bus(str(load["bus_id"]))
        p_factor = g * reg[region]
        q_factor = g * reg_q[region]
        load["pd"] = float(load["pd"]) * p_factor
        load["qd"] = float(load["qd"]) * q_factor

    generators = out.get("generators", [])
    # Cost permutation: reassign whole cost curves among generators (a la
    # gridfm-datakit) so identical dispatch topologies see different merit
    # orders. Deterministic in ``perturbation_seed`` and applied before the
    # per-generator cost scaling below.
    if float(params.get("cost_permutation", 0.0)) and len(generators) > 1:
        perm = _seeded_permutation(len(generators), pert_seed ^ 0xC057C057)
        orig_costs = [list(gen.get("cost", [0.0, 1.0, 0.0])) for gen in generators]
        for i, gen in enumerate(generators):
            gen["cost"] = list(orig_costs[perm[i]])

    for i, gen in enumerate(generators):
        is_renew = (i % 3 == 0)
        avail = fleet_avail * (renew_scale if is_renew else 1.0)
        avail = max(0.1, min(1.2, avail))

        pmax = float(gen["pmax"]) * avail
        pmin = float(gen["pmin"]) * max(0.5, min(1.2, 1.0 - reserve_margin * 0.5))
        if pmin > pmax:
            pmin = 0.8 * pmax

        gen["pmax"] = pmax
        gen["pmin"] = pmin
        gen["qmax"] = float(gen["qmax"]) * avail
        gen["qmin"] = float(gen["qmin"]) * avail

        c = list(gen.get("cost", [0.0, 1.0, 0.0]))
        while len(c) < 3:
            c.append(0.0)
        c[0] = float(c[0]) * cost_scale
        c[1] = float(c[1]) * cost_scale
        gen["cost"] = c

    for br in out.get("branches", []):
        branch_key = _str_key(str(br.get("branch_id", br.get("from", "") + "_" + br.get("to", ""))))
        r_factor = _branch_factor(pert_seed, branch_key, 0, r_sigma) if r_sigma > 0.0 else r_scale_global
        x_factor = _branch_factor(pert_seed, branch_key, 1, x_sigma) if x_sigma > 0.0 else x_scale_global
        b_factor = _branch_factor(pert_seed, branch_key, 2, b_sigma) if b_sigma > 0.0 else b_scale_global
        br["rate_a"] = max(1.0, float(br["rate_a"]) * branch_scale)
        br["r"] = max(0.0, float(br["r"]) * r_factor)
        br["x"] = max(1e-6, float(br["x"]) * x_factor)  # keep strictly positive for solver stability
        if "b" in br:
            br["b"] = float(br["b"]) * b_factor

    return out
