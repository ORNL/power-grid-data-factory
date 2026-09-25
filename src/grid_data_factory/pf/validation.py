from __future__ import annotations

import math
from typing import Any

from grid_data_factory.pf.schemas import PFControls


DEFAULT_TOLERANCES = {
    "active_power_residual_mw": 1.0,
    "reactive_power_residual_mvar": 1.0,
    "control_tolerance_mw": 0.1,
    "voltage_tolerance_pu": 1e-5,
    "limit_tolerance": 1e-5,
    "anchor_voltage_tolerance_pu": 1e-4,
    "anchor_angle_tolerance_rad": 1e-3,
}


def validate_anchor_consistency(
    source_solution: dict[str, Any],
    result: dict[str, Any],
    tolerances: dict[str, float] | None = None,
) -> dict[str, Any]:
    limits = {**DEFAULT_TOLERANCES, **(tolerances or {})}
    solution = ((result.get("raw_result") or {}).get("solution") or {})
    source_buses = source_solution.get("bus") or {}
    solved_buses = solution.get("bus") or {}
    voltage_errors = []
    angle_errors = []
    for bus_id, source_bus in source_buses.items():
        if bus_id not in solved_buses:
            continue
        solved_bus = solved_buses[bus_id]
        voltage_errors.append(abs(float(solved_bus.get("vm", 0.0)) - float(source_bus.get("vm", 0.0))))
        angle_errors.append(abs(float(solved_bus.get("va", 0.0)) - float(source_bus.get("va", 0.0))))
    max_voltage_error = max(voltage_errors, default=math.inf)
    max_angle_error = max(angle_errors, default=math.inf)
    passed = (
        bool(voltage_errors)
        and max_voltage_error <= limits["anchor_voltage_tolerance_pu"]
        and max_angle_error <= limits["anchor_angle_tolerance_rad"]
    )
    return {
        "passed": passed,
        "compared_bus_count": len(voltage_errors),
        "max_voltage_error_pu": max_voltage_error,
        "max_angle_error_rad": max_angle_error,
    }


def validate_pf_result(
    case_data: dict[str, Any],
    controls: PFControls | dict[str, Any],
    result: dict[str, Any],
    tolerances: dict[str, float] | None = None,
) -> dict[str, Any]:
    limits = {**DEFAULT_TOLERANCES, **(tolerances or {})}
    if not result.get("success"):
        return {
            "validation_passed": False,
            "reason": "solver_not_converged",
            "violations": [],
            "active_power_residual_mw": None,
            "reactive_power_residual_mvar": None,
        }

    controls = controls if isinstance(controls, PFControls) else PFControls.model_validate(controls)
    solution = ((result.get("raw_result") or {}).get("solution") or {})
    gen_solution = solution.get("gen") or {}
    bus_solution = solution.get("bus") or {}
    branch_solution = solution.get("branch") or {}
    base_mva = float(case_data.get("base_mva", 100.0))
    violations: list[dict[str, Any]] = []

    total_pg = 0.0
    total_qg = 0.0
    slack_adjustment = 0.0
    reactive_limit_count = 0
    final_slack_bus_ids = {str(bus_id) for bus_id in result.get("slack_bus_ids", [])}
    bus_by_id = {str(bus["bus_id"]): (index, bus) for index, bus in enumerate(case_data.get("buses", []), start=1)}
    for index, generator in enumerate(case_data.get("generators", []), start=1):
        gid = str(generator["gen_id"])
        solved = gen_solution.get(str(index))
        if solved is None:
            violations.append({"type": "missing_generator_solution", "component_id": gid})
            continue
        pg = float(solved.get("pg", 0.0)) * base_mva
        qg = float(solved.get("qg", 0.0)) * base_mva
        total_pg += pg
        total_qg += qg
        qmin = float(generator.get("qmin", -math.inf))
        qmax = float(generator.get("qmax", math.inf))
        if qg < qmin - limits["limit_tolerance"] or qg > qmax + limits["limit_tolerance"]:
            violations.append({"type": "generator_q_limit", "component_id": gid, "value": qg})
        if abs(qg - qmin) <= limits["limit_tolerance"] or abs(qg - qmax) <= limits["limit_tolerance"]:
            reactive_limit_count += 1
        control = controls.generators.get(gid)
        if control is not None:
            _, bus = bus_by_id[str(generator["bus_id"])]
            if str(generator["bus_id"]) in final_slack_bus_ids:
                slack_adjustment += pg - control.pg
            elif abs(pg - control.pg) > limits["control_tolerance_mw"]:
                violations.append({"type": "active_control_mismatch", "component_id": gid, "value": pg - control.pg})

    voltage_violations = 0
    bus_vm: dict[str, float] = {}
    for index, bus in enumerate(case_data.get("buses", []), start=1):
        bid = str(bus["bus_id"])
        solved = bus_solution.get(str(index))
        if solved is None:
            violations.append({"type": "missing_bus_solution", "component_id": bid})
            continue
        vm = float(solved.get("vm", 0.0))
        bus_vm[bid] = vm
        if vm < float(bus.get("vmin", 0.9)) - limits["limit_tolerance"] or vm > float(bus.get("vmax", 1.1)) + limits["limit_tolerance"]:
            voltage_violations += 1
            violations.append({"type": "voltage_limit", "component_id": bid, "value": vm})

    branch_p_loss = 0.0
    branch_q_loss = 0.0
    thermal_violations = 0
    for index, branch in enumerate(case_data.get("branches", []), start=1):
        solved = branch_solution.get(str(index))
        if solved is None:
            violations.append({"type": "missing_branch_solution", "component_id": str(branch["branch_id"])})
            continue
        pf = float(solved.get("pf", 0.0)) * base_mva
        pt = float(solved.get("pt", 0.0)) * base_mva
        qf = float(solved.get("qf", 0.0)) * base_mva
        qt = float(solved.get("qt", 0.0)) * base_mva
        branch_p_loss += pf + pt
        branch_q_loss += qf + qt
        rate = float(branch.get("rate_a", 0.0))
        loading = max(math.hypot(pf, qf), math.hypot(pt, qt))
        if 0.0 < rate < 1e6 and loading > rate * (1.0 + limits["limit_tolerance"]):
            thermal_violations += 1
            violations.append({"type": "branch_thermal", "component_id": str(branch["branch_id"]), "value": loading / rate})

    total_pd = sum(float(load.get("pd", 0.0)) for load in case_data.get("loads", []))
    total_qd = sum(float(load.get("qd", 0.0)) for load in case_data.get("loads", []))
    shunt_p = sum(float(bus.get("gs", 0.0)) * bus_vm.get(str(bus["bus_id"]), 1.0) ** 2 for bus in case_data.get("buses", []))
    shunt_q_injection = sum(float(bus.get("bs", 0.0)) * bus_vm.get(str(bus["bus_id"]), 1.0) ** 2 for bus in case_data.get("buses", []))
    active_residual = total_pg - total_pd - shunt_p - branch_p_loss
    reactive_residual = total_qg - total_qd + shunt_q_injection - branch_q_loss
    if abs(active_residual) > limits["active_power_residual_mw"]:
        violations.append({"type": "active_power_residual", "value": active_residual})
    if abs(reactive_residual) > limits["reactive_power_residual_mvar"]:
        violations.append({"type": "reactive_power_residual", "value": reactive_residual})

    return {
        "validation_passed": not violations,
        "reason": "valid" if not violations else "validation_violations",
        "violations": violations,
        "active_power_residual_mw": active_residual,
        "reactive_power_residual_mvar": reactive_residual,
        "slack_adjustment_mw": slack_adjustment,
        "reactive_limit_count": reactive_limit_count,
        "voltage_violation_count": voltage_violations,
        "thermal_violation_count": thermal_violations,
    }