from __future__ import annotations

import copy
from typing import Any

from grid_data_factory.contingencies.apply import apply_contingency, outaged_components
from grid_data_factory.pf.schemas import PFControls, ResponsePolicy


def controls_from_case(case_data: dict[str, Any]) -> PFControls:
    generators = {
        str(gen["gen_id"]): {
            "pg": float(gen.get("pg", 0.0)),
            "qg": float(gen.get("qg", 0.0)),
            "vg": float(gen.get("vg", 1.0)),
        }
        for gen in case_data.get("generators", [])
        if int(gen.get("status", 1)) > 0
    }
    taps = {}
    shifts = {}
    shunts = {}
    for bus in case_data.get("buses", []):
        if float(bus.get("gs", 0.0)) != 0.0 or float(bus.get("bs", 0.0)) != 0.0:
            shunts[f"bus:{bus['bus_id']}"] = float(bus.get("bs", 0.0))
    for branch in case_data.get("branches", []):
        branch_id = str(branch["branch_id"])
        if branch.get("transformer") or float(branch.get("tap", 0.0)) != 0.0:
            taps[branch_id] = float(branch.get("tap", 0.0))
            shifts[branch_id] = float(branch.get("shift", 0.0))
    return PFControls(
        generators=generators,
        transformer_taps=taps,
        transformer_shifts=shifts,
        shunts=shunts,
    )


def _generator_map(case_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(gen["gen_id"]): gen for gen in case_data.get("generators", [])}


def _rebalance(
    values: dict[str, float],
    generators: dict[str, dict[str, Any]],
    target_total: float,
    weights: dict[str, float] | None = None,
    tolerance_mw: float = 1e-9,
) -> float:
    for _ in range(len(values) + 1):
        residual = sum(values.values()) - target_total
        if abs(residual) <= tolerance_mw:
            return residual
        if residual > 0.0:
            room = {gid: max(0.0, values[gid] - float(generators[gid]["pmin"])) for gid in values}
            sign = -1.0
        else:
            room = {gid: max(0.0, float(generators[gid]["pmax"]) - values[gid]) for gid in values}
            sign = 1.0
        eligible = {gid: amount for gid, amount in room.items() if amount > tolerance_mw}
        if not eligible:
            break
        raw_weights = {gid: max(0.0, float((weights or {}).get(gid, amount))) for gid, amount in eligible.items()}
        weight_total = sum(raw_weights.values())
        if weight_total <= 0.0:
            raw_weights = eligible
            weight_total = sum(eligible.values())
        need = abs(residual)
        moved = 0.0
        for gid in sorted(eligible):
            amount = min(eligible[gid], need * raw_weights[gid] / weight_total)
            values[gid] += sign * amount
            moved += amount
        if moved <= tolerance_mw:
            break
    return sum(values.values()) - target_total


def balanced_redispatch(
    case_data: dict[str, Any],
    proposed_delta_pg: dict[str, float],
    *,
    voltage_setpoints: dict[str, float] | None = None,
    tolerance_mw: float | None = None,
) -> tuple[PFControls, dict[str, Any]]:
    generators = _generator_map(case_data)
    unknown = sorted(set(proposed_delta_pg) - set(generators))
    if unknown:
        raise ValueError(f"Unknown generator IDs: {unknown}")
    active = {gid: gen for gid, gen in generators.items() if int(gen.get("status", 1)) > 0}
    target_total = sum(float(gen.get("pg", 0.0)) for gen in active.values())
    values = {
        gid: min(
            float(gen["pmax"]),
            max(float(gen["pmin"]), float(gen.get("pg", 0.0)) + float(proposed_delta_pg.get(gid, 0.0))),
        )
        for gid, gen in active.items()
    }
    total_load = sum(float(load.get("pd", 0.0)) for load in case_data.get("loads", []))
    allowed = float(tolerance_mw if tolerance_mw is not None else min(1.0, 0.001 * total_load))
    residual = _rebalance(values, active, target_total, tolerance_mw=max(allowed * 1e-6, 1e-9))
    if abs(residual) > allowed:
        raise ValueError(f"Could not balance redispatch: residual={residual:.6g} MW, tolerance={allowed:.6g} MW")

    voltage_setpoints = voltage_setpoints or {}
    controls = controls_from_case(case_data).model_dump()
    for gid, pg in values.items():
        controls["generators"][gid]["pg"] = pg
        if gid in voltage_setpoints:
            controls["generators"][gid]["vg"] = float(voltage_setpoints[gid])
    applied_delta = {gid: values[gid] - float(active[gid].get("pg", 0.0)) for gid in values}
    return PFControls.model_validate(controls), {
        "proposed_delta_pg": {gid: float(value) for gid, value in proposed_delta_pg.items()},
        "applied_delta_pg": applied_delta,
        "residual_mw": residual,
        "target_total_pg_mw": target_total,
    }


def normalized_control_distance(
    case_data: dict[str, Any],
    anchor: PFControls | dict[str, Any],
    controls: PFControls | dict[str, Any],
) -> float:
    anchor = anchor if isinstance(anchor, PFControls) else PFControls.model_validate(anchor)
    controls = controls if isinstance(controls, PFControls) else PFControls.model_validate(controls)
    generators = _generator_map(case_data)
    terms = []
    for gid, anchor_control in anchor.generators.items():
        if gid not in controls.generators or gid not in generators:
            continue
        span = float(generators[gid]["pmax"]) - float(generators[gid]["pmin"])
        if span > 0.0:
            terms.append(((controls.generators[gid].pg - anchor_control.pg) / span) ** 2)
        terms.append((controls.generators[gid].vg - anchor_control.vg) ** 2)
    return (sum(terms) / len(terms)) ** 0.5 if terms else 0.0


def apply_response_policy(
    case_data: dict[str, Any],
    contingency: dict[str, Any] | None,
    controls: PFControls | dict[str, Any],
    policy: ResponsePolicy | dict[str, Any],
) -> tuple[dict[str, Any], PFControls, dict[str, Any]]:
    controls = controls if isinstance(controls, PFControls) else PFControls.model_validate(controls)
    policy = policy if isinstance(policy, ResponsePolicy) else ResponsePolicy.model_validate(policy)
    original_generators = _generator_map(case_data)
    lost_ids = [comp_id for comp_type, comp_id in outaged_components(contingency or {}) if comp_type == "generator"]
    lost_pg = sum(controls.generators[gid].pg for gid in lost_ids if gid in controls.generators)
    post_case = apply_contingency(case_data, contingency)
    post_controls = copy.deepcopy(controls.model_dump())
    for gid in lost_ids:
        post_controls["generators"].pop(gid, None)

    if lost_pg > 0.0:
        if policy.policy_id == "fixed_controls_slack_loss":
            raise ValueError("fixed_controls_slack_loss does not define generator-outage replacement")
        remaining = _generator_map(post_case)
        values = {gid: float(control["pg"]) for gid, control in post_controls["generators"].items()}
        target_total = sum(values.values()) + lost_pg
        if policy.policy_id == "reserve_participation":
            weights = {gid: max(0.0, float(remaining[gid]["pmax"]) - pg) for gid, pg in values.items()}
        elif policy.policy_id == "fixed_participation":
            weights = policy.participation_factors
        else:
            weights = policy.droop_coefficients
        residual = _rebalance(values, remaining, target_total, weights=weights)
        if abs(residual) > 1e-6:
            raise ValueError(f"Insufficient reserve for generator outage: residual={residual:.6g} MW")
        for gid, pg in values.items():
            post_controls["generators"][gid]["pg"] = pg
    else:
        residual = 0.0

    metadata = {
        "response_policy_id": policy.policy_id,
        "lost_generator_ids": lost_ids,
        "lost_pg_mw": lost_pg,
        "response_residual_mw": residual,
        "participation_factors": policy.participation_factors,
        "droop_coefficients": policy.droop_coefficients,
        "reactive_limit_mode": policy.reactive_limit_mode,
        "pv_to_pq": policy.pv_to_pq,
    }
    return post_case, PFControls.model_validate(post_controls), metadata