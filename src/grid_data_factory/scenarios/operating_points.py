"""Operating-point transforms applied to a parsed case at solve time.

Rebuilds loads from a reference snapshot (when supplied) and applies regional /
global scaling of loads, generator availability, reserve margins, branch ratings
and cost. All transforms operate on a deep copy so the base case is never
mutated, and they compose: snapshot rebuild happens first, then scaling.
"""
from __future__ import annotations

import json
from typing import Any


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

    for load in out.get("loads", []):
        region = region_for_bus(str(load["bus_id"]))
        p_factor = g * reg[region]
        q_factor = g * reg_q[region]
        load["pd"] = float(load["pd"]) * p_factor
        load["qd"] = float(load["qd"]) * q_factor

    for i, gen in enumerate(out.get("generators", [])):
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
        br["rate_a"] = max(1.0, float(br["rate_a"]) * branch_scale)

    return out
